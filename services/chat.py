import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import uuid4

import anthropic
from openai import OpenAI
from PyQt6.QtCore import QThread, pyqtSignal

from services.crew import (
    ASK_CREW_TOOL_NAME,
    all_crew,
    ask_crew_tool_anthropic,
    ask_crew_tool_openai,
    crew_model_choice,
    crew_enabled,
    crew_metadata,
    crew_prompt,
    crew_system_prompt,
    get_crew_member,
)
from services.crew_context import crew_context_window
from services.compaction import compact_with_result
from services.model_registry import get_model_config, resolve_api_key
from services.content import content_preview, prepare_for_anthropic, prepare_for_openai
from services.tool_policy import ConversationToolPolicy, ToolApprovalBus, resolve_path
from services.shell_tool import is_shell_tool
from services.tools import (
    execute,
    is_parallel_safe,
    tool_approval,
    tools_anthropic,
    tools_openai,
)
from services.tool_registry import HookContext, run_extension_hooks
from services.usage import merge_usage, normalize_usage

_MAX_PARALLEL = 8
_MAX_CREW_CALLS_PER_TURN = 2
_ACTIVE_TASK_PREVIEW_CHARS = 500
_CREW_ONLY_TOOLS = {"search_project_chats", "read_project_chat"}
_CHUNK_EMIT_INTERVAL_SEC = 0.10
_CHUNK_EMIT_MAX_CHARS = 512


class ChatThread(QThread):
    chunk       = pyqtSignal(str)
    tool_called = pyqtSignal(str, dict)
    bash_line   = pyqtSignal(str)
    tool_result = pyqtSignal(str, str)
    crew_started = pyqtSignal(dict)
    crew_chunk   = pyqtSignal(dict, str)
    crew_done    = pyqtSignal(dict, str)
    crew_error   = pyqtSignal(dict, str)
    runtime_event = pyqtSignal(dict)
    done        = pyqtSignal(str)
    error       = pyqtSignal(str)

    def __init__(self, model: str, history: list, system: str, cwd: str,
                 allowed_tools: list[str] | None = None,
                 tool_policy: ConversationToolPolicy | None = None,
                 approval_bus: ToolApprovalBus | None = None,
                 write_roots: list[str] | tuple[str, ...] | None = None,
                 enable_crew_tool: bool = True,
                 crew_settings: dict | None = None,
                 configured_providers: set[str] | None = None):
        super().__init__()
        self.model          = model
        self._model_cfg     = get_model_config(model)
        self.provider       = self._model_cfg.api   # "anthropic" | "openai-compatible"
        self.history        = list(history)
        self.system         = system
        self.cwd            = cwd
        self._cancel        = threading.Event()
        self._allowed_tools = allowed_tools
        self._tool_policy   = tool_policy or ConversationToolPolicy()
        self._approval_bus  = approval_bus
        self._write_roots   = tuple(write_roots) if write_roots is not None else None
        self._enable_crew_tool = enable_crew_tool
        self._crew_settings = dict(crew_settings or {})
        self._configured_providers = set(configured_providers or set())
        self._chunk_buffer: list[str] = []
        self._last_chunk_emit = 0.0
        self.last_usage: dict = {}
        self._crew_calls = 0

    def cancel(self):
        self._cancel.set()
        if self._approval_bus:
            self._approval_bus.cancel_wait()

    def _tools_anthropic(self) -> list:
        tools = tools_anthropic(self.cwd)
        allowed = None if self._allowed_tools is None else set(self._allowed_tools)
        if allowed is None:
            selected = [t for t in tools if t["name"] not in _CREW_ONLY_TOOLS]
        else:
            selected = [t for t in tools if t["name"] in allowed]
        if self._enable_crew_tool and (allowed is None or ASK_CREW_TOOL_NAME in allowed):
            selected = selected + [ask_crew_tool_anthropic()]
        return selected

    def _tools_openai(self) -> list:
        tools = tools_openai(self.cwd)
        allowed = None if self._allowed_tools is None else set(self._allowed_tools)
        if allowed is None:
            selected = [
                t for t in tools
                if t["function"]["name"] not in _CREW_ONLY_TOOLS
            ]
        else:
            selected = [t for t in tools if t["function"]["name"] in allowed]
        if self._enable_crew_tool and (allowed is None or ASK_CREW_TOOL_NAME in allowed):
            selected = selected + [ask_crew_tool_openai()]
        return selected

    def run(self):
        try:
            start_ctx = HookContext(
                event="turn_start",
                cwd=self.cwd,
                model=self.model,
                system=self.system,
                history=self.history,
            )
            run_extension_hooks(self.cwd, "turn_start", start_ctx)
            self.system = start_ctx.system
            self.history = start_ctx.history

            if self.provider == "anthropic":
                text = self._loop_anthropic()
            else:
                text = self._loop_openai()
            self._flush_chunk_buffer()
            status = "cancelled" if self._cancel.is_set() else "ok"
            run_extension_hooks(
                self.cwd,
                "turn_done",
                HookContext(
                    event="turn_done",
                    cwd=self.cwd,
                    model=self.model,
                    system=self.system,
                    history=self.history,
                    output=text,
                    status=status,
                ),
            )
            self.done.emit(text)
        except Exception as exc:
            self._flush_chunk_buffer()
            if not self._cancel.is_set():
                run_extension_hooks(
                    self.cwd,
                    "turn_done",
                    HookContext(
                        event="turn_done",
                        cwd=self.cwd,
                        model=self.model,
                        system=self.system,
                        history=self.history,
                        status="error",
                        error=str(exc),
                    ),
                )
                self.error.emit(str(exc))

    def _emit_chunk(self, text: str, *, force: bool = False):
        if not text:
            return
        self._chunk_buffer.append(text)
        now = time.monotonic()
        buffered = sum(len(part) for part in self._chunk_buffer)
        if force or buffered >= _CHUNK_EMIT_MAX_CHARS or now - self._last_chunk_emit >= _CHUNK_EMIT_INTERVAL_SEC:
            self._flush_chunk_buffer(now)

    def _flush_chunk_buffer(self, now: float | None = None):
        if not self._chunk_buffer:
            return
        self.chunk.emit("".join(self._chunk_buffer))
        self._chunk_buffer.clear()
        self._last_chunk_emit = now if now is not None else time.monotonic()

    # ── Anthropic agentic loop ────────────────────────────────────────────────

    def _loop_anthropic(self) -> str:
        cfg    = self._model_cfg
        kwargs: dict = {"api_key": resolve_api_key(cfg.api_key_spec)}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        client    = anthropic.Anthropic(**kwargs)
        full_text = ""
        self.last_usage = {}

        while True:
            if self._cancel.is_set():
                break
            turn_text = ""
            if not self._run_runtime_hook("before_model_request"):
                break
            with client.messages.stream(
                model=self.model,
                max_tokens=4096,
                system=self.system,
                tools=self._tools_anthropic(),
                messages=prepare_for_anthropic(self.history),
            ) as stream:
                for text in stream.text_stream:
                    if self._cancel.is_set():
                        break
                    self._emit_chunk(text)
                    turn_text += text
                self._flush_chunk_buffer()
                if self._cancel.is_set():
                    break
                message = stream.get_final_message()
                self.last_usage = merge_usage(
                    self.last_usage,
                    normalize_usage("anthropic", getattr(message, "usage", None)),
                )

            if self._cancel.is_set():
                full_text += turn_text
                break

            self.history.append({
                "role":    "assistant",
                "content": _serialize_anthropic(message.content),
            })

            tool_results = []
            tools = [
                (block.id, block.name, dict(block.input))
                for block in message.content
                if block.type == "tool_use"
            ]
            for tool_id, name, output in self._execute_tools(tools):
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": tool_id,
                    "content":     output,
                })

            if tool_results and not self._cancel.is_set():
                self.history.append({
                    "role": "user",
                    "content": self._tool_results_with_active_task(tool_results),
                    "synthetic": "tool_results",
                })
                if not self._run_runtime_hook("before_next_model_request"):
                    break
            else:
                full_text += turn_text
                break

        return full_text

    # ── OpenAI agentic loop ───────────────────────────────────────────────────

    def _loop_openai(self) -> str:
        cfg    = self._model_cfg
        kwargs: dict = {"api_key": resolve_api_key(cfg.api_key_spec)}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        client    = OpenAI(**kwargs)
        full_text = ""
        self.last_usage = {}

        while True:
            if self._cancel.is_set():
                break
            turn_text = ""
            pending: dict[int, dict] = {}
            if not self._run_runtime_hook("before_model_request"):
                break
            msgs = [{"role": "system", "content": self.system}] + prepare_for_openai(self.history)
            if msgs and msgs[0].get("role") == "system":
                msgs[0]["content"] = self.system

            request = {
                "model": self.model,
                "messages": msgs,
                "tools": self._tools_openai(),
                "stream": True,
            }
            if cfg.provider_id == "openai" and not cfg.base_url:
                request["stream_options"] = {"include_usage": True}
            turn_usage = {}
            with client.chat.completions.create(**request) as stream:
                for chunk in stream:
                    if self._cancel.is_set():
                        break
                    turn_usage = merge_usage(
                        turn_usage,
                        normalize_usage("openai-compatible", getattr(chunk, "usage", None)),
                    )
                    if not getattr(chunk, "choices", None):
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        self._emit_chunk(delta.content)
                        turn_text += delta.content
                    for tc in delta.tool_calls or []:
                        slot = pending.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function.name:
                            slot["name"] = tc.function.name
                        if tc.function.arguments:
                            slot["args"] += tc.function.arguments
                self._flush_chunk_buffer()
            self.last_usage = merge_usage(self.last_usage, turn_usage)

            if self._cancel.is_set():
                full_text += turn_text
                break

            if pending:
                assistant_msg = {
                    "role": "assistant",
                    "content": turn_text or None,
                    "tool_calls": [
                        {"id": s["id"], "type": "function",
                         "function": {"name": s["name"], "arguments": s["args"]}}
                        for s in pending.values()
                    ],
                }
                self.history.append(assistant_msg)
                ordered = sorted(pending.items())
                tools = [(s["id"], s["name"], json.loads(s["args"])) for _, s in ordered]
                for tool_id, name, output in self._execute_tools(tools):
                    self.history.append({
                        "role":         "tool",
                        "tool_call_id": tool_id,
                        "content":      output,
                    })
                anchor = self._active_task_anchor()
                if anchor:
                    self.history.append({"role": "user", "content": anchor, "synthetic": "active_task"})
                if not self._run_runtime_hook("before_next_model_request"):
                    break
            else:
                full_text += turn_text
                break

        return full_text

    def _run_runtime_hook(self, event: str) -> bool:
        ctx = HookContext(
            event=event,
            cwd=self.cwd,
            model=self.model,
            system=self.system,
            history=self.history,
        )
        run_extension_hooks(self.cwd, event, ctx)
        self.system = ctx.system
        self.history = ctx.history
        return self._apply_runtime_directives(ctx)

    def _apply_runtime_directives(self, ctx: HookContext) -> bool:
        for directive in ctx.directives:
            action = directive.action
            params = dict(directive.params)
            if action == "show_notice":
                self.runtime_event.emit({"type": "notice", "text": str(params.get("text") or "")})
            elif action == "enqueue_message":
                text = str(params.get("text") or "").strip()
                if text:
                    self.history.append({"role": "user", "content": text, "synthetic": "extension"})
            elif action in {"compact_now", "compact_and_resume"}:
                if not self._apply_compaction_directive(action, params):
                    return False
            elif action == "block":
                self.runtime_event.emit({
                    "type": "blocked",
                    "text": str(params.get("text") or params.get("reason") or "Blocked by extension."),
                })
                return False
        return not self._cancel.is_set()

    def _apply_compaction_directive(self, action: str, params: dict) -> bool:
        try:
            result = compact_with_result(
                self.model,
                self.history,
                force=bool(params.get("force", False)),
                source=str(params.get("source") or "extension"),
                ledger=bool(params.get("ledger", False)),
            )
        except Exception as exc:
            self.runtime_event.emit({
                "type": "compaction_failed",
                "action": action,
                "error": str(exc),
            })
            return False
        self.runtime_event.emit({
            "type": "compaction",
            "action": action,
            "status": result.status,
            "proof": result.proof,
            "artifact": result.artifact,
        })
        if result.status == "compacted":
            self.history = list(result.messages)
        if action == "compact_and_resume":
            resume_prompt = str(params.get("resume_prompt") or "").strip()
            if not resume_prompt:
                resume_prompt = "Continue the active task from the compacted context."
            self.history.append({
                "role": "user",
                "content": resume_prompt,
                "synthetic": "extension_resume",
            })
        return True

    def _execute_tools(self, tools: list[tuple[str, str, dict]]) -> list[tuple[str, str, str]]:
        """Run tool calls, parallelizing consecutive read/search tools."""
        if not tools:
            return []

        results: list[tuple[str, str, str] | None] = [None] * len(tools)
        i = 0
        while i < len(tools):
            if self._cancel.is_set():
                for j in range(i, len(tools)):
                    tid, name, _ = tools[j]
                    results[j] = (tid, name, "[cancelled]")
                break

            tool_id, name, inputs = tools[i]
            if not is_parallel_safe(name, self.cwd):
                results[i] = self._execute_one(tool_id, name, inputs)
                i += 1
                continue

            j = i
            while j < len(tools) and is_parallel_safe(tools[j][1], self.cwd):
                j += 1
            batch = tools[i:j]

            if len(batch) == 1:
                tid, n, inp = batch[0]
                results[i] = self._execute_one(tid, n, inp)
            else:
                batch_results = self._execute_parallel_batch(batch)
                for k, item in enumerate(batch_results):
                    results[i + k] = item
            i = j

        return results  # type: list[tuple[str, str, str]]

    def _execute_one(self, tool_id: str, name: str, inputs: dict) -> tuple[str, str, str]:
        hook_ctx = HookContext(
            event="before_tool_call",
            cwd=self.cwd,
            model=self.model,
            system=self.system,
            history=self.history,
            tool_name=name,
            inputs=dict(inputs),
        )
        run_extension_hooks(self.cwd, "before_tool_call", hook_ctx)
        inputs = hook_ctx.inputs
        if hook_ctx.status == "error":
            blocked = hook_ctx.output or hook_ctx.error or "[tool error] Tool blocked by extension hook."
            self.tool_result.emit(name, blocked)
            return tool_id, name, blocked
        if name == ASK_CREW_TOOL_NAME:
            return tool_id, name, self._execute_ask_crew(inputs)
        scoped = self._check_tool_scope(name, inputs)
        if scoped:
            self.tool_result.emit(name, scoped)
            return tool_id, name, scoped
        blocked = self._check_tool_gate(name, inputs)
        if blocked:
            self.tool_result.emit(name, blocked)
            return tool_id, name, blocked
        self.tool_called.emit(name, inputs)
        on_line = (lambda line: self.bash_line.emit(line)) if is_shell_tool(name) else None
        output = execute(name, inputs, self.cwd, on_line=on_line, cancel=self._cancel)
        result_ctx = HookContext(
            event="after_tool_result",
            cwd=self.cwd,
            model=self.model,
            system=self.system,
            history=self.history,
            tool_name=name,
            inputs=inputs,
            output=output,
        )
        run_extension_hooks(self.cwd, "after_tool_result", result_ctx)
        output = result_ctx.output
        self.tool_result.emit(name, output)
        return tool_id, name, output

    def _execute_parallel_batch(
        self, batch: list[tuple[str, str, dict]],
    ) -> list[tuple[str, str, str]]:
        indexed: list[tuple[int, str, str, str]] = []

        def run(idx: int, tool_id: str, name: str, inputs: dict):
            if self._cancel.is_set():
                return idx, tool_id, name, "[cancelled]"
            hook_ctx = HookContext(
                event="before_tool_call",
                cwd=self.cwd,
                model=self.model,
                system=self.system,
                history=self.history,
                tool_name=name,
                inputs=dict(inputs),
            )
            run_extension_hooks(self.cwd, "before_tool_call", hook_ctx)
            inputs = hook_ctx.inputs
            if hook_ctx.status == "error":
                blocked = hook_ctx.output or hook_ctx.error or "[tool error] Tool blocked by extension hook."
                return idx, tool_id, name, blocked
            if name == ASK_CREW_TOOL_NAME:
                return idx, tool_id, name, self._execute_ask_crew(inputs)
            scoped = self._check_tool_scope(name, inputs)
            if scoped:
                return idx, tool_id, name, scoped
            blocked = self._check_tool_gate(name, inputs)
            if blocked:
                return idx, tool_id, name, blocked
            self.tool_called.emit(name, inputs)
            output = execute(name, inputs, self.cwd, cancel=self._cancel)
            result_ctx = HookContext(
                event="after_tool_result",
                cwd=self.cwd,
                model=self.model,
                system=self.system,
                history=self.history,
                tool_name=name,
                inputs=inputs,
                output=output,
            )
            run_extension_hooks(self.cwd, "after_tool_result", result_ctx)
            output = result_ctx.output
            return idx, tool_id, name, output

        with ThreadPoolExecutor(max_workers=min(len(batch), _MAX_PARALLEL)) as pool:
            futures = [
                pool.submit(run, k, tid, name, inputs)
                for k, (tid, name, inputs) in enumerate(batch)
            ]
            for future in as_completed(futures):
                indexed.append(future.result())

        indexed.sort(key=lambda x: x[0])
        results = [(tid, name, output) for _, tid, name, output in indexed]
        for _, name, output in results:
            self.tool_result.emit(name, output)
        return results

    def _check_tool_gate(self, name: str, inputs: dict) -> str | None:
        if not self._approval_bus:
            return None
        approval = tool_approval(name, self.cwd)
        if approval == "once":
            return self._approval_bus.check_extension_tool(
                name, inputs, self.cwd, self._tool_policy, self._cancel.is_set,
            )
        return self._approval_bus.check(
            name, inputs, self.cwd, self._tool_policy, self._cancel.is_set,
        )

    def _execute_ask_crew(self, inputs: dict) -> str:
        member_id = str(inputs.get("member") or "").strip().casefold()
        task = str(inputs.get("task") or "").strip()
        reason = str(inputs.get("reason") or "").strip()
        member = get_crew_member(member_id)
        if member is None:
            names = ", ".join(member.id for member in all_crew())
            return f"[tool error] Unknown crew member. Use one of: {names}."
        if not crew_enabled(self._crew_settings, member):
            return f"[tool error] {member.name} is disabled in Crew settings."
        if not task:
            return "[tool error] ask_crew requires a focused task."
        if self._crew_calls >= _MAX_CREW_CALLS_PER_TURN:
            return "[tool error] Crew is limited to two focused calls per turn. Synthesize with the information already gathered."

        meta = crew_metadata(member, self._crew_settings)
        self._crew_calls += 1
        meta["reason"] = reason
        meta["invocation_id"] = uuid4().hex
        self.crew_started.emit(meta)
        if member.id == "archivist":
            return self._execute_archivist_lookup(member, meta, task)
        model = str(meta.get("model") or "")
        model = crew_model_choice(
            member,
            self.model,
            {member.id: model},
            self._configured_providers,
        )
        crew_history = crew_context_window(self.history)
        crew_history.append({
            "role": "user",
            "content": _crew_task_content(member.name, task, reason),
        })
        crew = ChatThread(
            model,
            crew_history,
            crew_system_prompt(member, self.system, crew_prompt(member, self._crew_settings)),
            self.cwd,
            allowed_tools=list(member.tools),
            tool_policy=self._tool_policy,
            approval_bus=self._approval_bus,
            write_roots=list(member.write_roots),
            enable_crew_tool=False,
            crew_settings=self._crew_settings,
            configured_providers=self._configured_providers,
        )
        crew.chunk.connect(lambda text, m=meta: self.crew_chunk.emit(m, text))
        crew.tool_called.connect(self.tool_called.emit)
        crew.bash_line.connect(self.bash_line.emit)
        crew.tool_result.connect(self.tool_result.emit)
        try:
            if crew.provider == "anthropic":
                text = crew._loop_anthropic()
            else:
                text = crew._loop_openai()
            crew._flush_chunk_buffer()
            if self._cancel.is_set():
                text = text or "[cancelled]"
            if crew.last_usage:
                meta["usage"] = dict(crew.last_usage)
            self.crew_done.emit(meta, text)
            return f"{member.name}: {text}"
        except Exception as exc:
            message = f"[tool error] {member.name} failed: {exc}"
            if not self._cancel.is_set():
                self.crew_error.emit(meta, message)
            return message

    def _execute_archivist_lookup(self, member, meta: dict, task: str) -> str:
        self.tool_called.emit("search_project_chats", {"query": task})
        text = execute(
            "search_project_chats",
            {"query": task, "limit": 5},
            self.cwd,
            cancel=self._cancel,
        )
        self.tool_result.emit("search_project_chats", text)
        if self._cancel.is_set():
            text = text or "[cancelled]"
        self.crew_done.emit(meta, text)
        return f"{member.name}: {text}"

    def _check_tool_scope(self, name: str, inputs: dict) -> str | None:
        if self._write_roots is None or name != "edit_file":
            return None
        if not self._write_roots:
            return "[tool error] edit_file is not available in this crew scope."
        path = inputs.get("path")
        if not path:
            return "[tool error] Missing edit_file path."
        try:
            target = resolve_path(str(path), self.cwd)
            root = resolve_path(".", self.cwd)
            rel = target.relative_to(root)
        except Exception:
            return "[tool error] edit_file path must stay inside the workspace."
        allowed = []
        for write_root in self._write_roots:
            try:
                root_rel = resolve_path(write_root, self.cwd).relative_to(root)
            except Exception:
                continue
            allowed.append(root_rel)
            if rel == root_rel or root_rel in rel.parents:
                return None
        roots = ", ".join(str(p).replace("\\", "/") for p in allowed) or "(none)"
        return f"[tool error] edit_file is limited to: {roots}."

    def _tool_results_with_active_task(self, tool_results: list[dict]) -> list[dict]:
        anchor = self._active_task_anchor()
        if not anchor:
            return tool_results
        return tool_results + [{
            "type": "text",
            "text": anchor,
            "synthetic": "active_task",
            "internal": True,
        }]

    def _active_task_anchor(self) -> str:
        task = _active_task_preview(self.history)
        if not task:
            return ""
        return (
            "Continue the active user task. Use the tool results above as evidence; "
            "do not ask for a new task unless the active task is impossible.\n\n"
            f"Active task: {task}"
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _serialize_anthropic(content) -> list:
    """Convert Anthropic content blocks to JSON-serialisable dicts."""
    out = []
    for block in content:
        if block.type == "text":
            out.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            out.append({"type": "tool_use", "id": block.id,
                        "name": block.name, "input": dict(block.input)})
    return out


def _active_task_preview(history: list[dict]) -> str:
    for msg in reversed(history):
        if msg.get("role") != "user":
            continue
        if msg.get("synthetic"):
            continue
        content = msg.get("content", "")
        if _is_tool_result_only(content):
            continue
        text = " ".join(content_preview(content).split())
        if not text:
            continue
        if len(text) > _ACTIVE_TASK_PREVIEW_CHARS:
            text = text[: _ACTIVE_TASK_PREVIEW_CHARS - 1].rstrip() + "…"
        return text
    return ""


def _is_tool_result_only(content) -> bool:
    if not isinstance(content, list):
        return False
    meaningful = [block for block in content if isinstance(block, dict)]
    return bool(meaningful) and all(block.get("type") == "tool_result" for block in meaningful)


def _crew_task_content(name: str, task: str, reason: str = "") -> str:
    parts = [
        f"{name}, answer this focused crew request for the lead assistant.",
        "",
        f"Task: {task}",
    ]
    if reason:
        parts.append(f"Reason: {reason}")
    parts.append("")
    parts.append("Keep the answer concise and actionable. The lead will synthesize it for the user.")
    return "\n".join(parts)
