import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic
from openai import OpenAI
from PyQt6.QtCore import QThread, pyqtSignal

from services.model_registry import get_model_config, resolve_api_key
from services.content import prepare_for_anthropic, prepare_for_openai
from services.tool_policy import ConversationToolPolicy, ToolApprovalBus
from services.tools import tools_anthropic, tools_openai, execute, is_parallel_safe, tool_approval
from services.tool_registry import HookContext, run_extension_hooks

_MAX_PARALLEL = 8
_CHUNK_EMIT_INTERVAL_SEC = 0.10
_CHUNK_EMIT_MAX_CHARS = 512


class ChatThread(QThread):
    chunk       = pyqtSignal(str)
    tool_called = pyqtSignal(str, dict)
    bash_line   = pyqtSignal(str)
    tool_result = pyqtSignal(str, str)
    done        = pyqtSignal(str)
    error       = pyqtSignal(str)

    def __init__(self, model: str, history: list, system: str, cwd: str,
                 allowed_tools: list[str] | None = None,
                 tool_policy: ConversationToolPolicy | None = None,
                 approval_bus: ToolApprovalBus | None = None):
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
        self._chunk_buffer: list[str] = []
        self._last_chunk_emit = 0.0

    def cancel(self):
        self._cancel.set()
        if self._approval_bus:
            self._approval_bus.cancel_wait()

    def _tools_anthropic(self) -> list:
        tools = tools_anthropic(self.cwd)
        if self._allowed_tools is None:
            return tools
        return [t for t in tools if t["name"] in self._allowed_tools]

    def _tools_openai(self) -> list:
        tools = tools_openai(self.cwd)
        if self._allowed_tools is None:
            return tools
        return [t for t in tools if t["function"]["name"] in self._allowed_tools]

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

        while True:
            if self._cancel.is_set():
                break
            turn_text = ""
            request_ctx = HookContext(
                event="before_model_request",
                cwd=self.cwd,
                model=self.model,
                system=self.system,
                history=self.history,
            )
            run_extension_hooks(self.cwd, "before_model_request", request_ctx)
            self.system = request_ctx.system
            self.history = request_ctx.history
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
                self.history.append({"role": "user", "content": tool_results})
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
        msgs      = [{"role": "system", "content": self.system}] + prepare_for_openai(self.history)
        full_text = ""

        while True:
            if self._cancel.is_set():
                break
            turn_text = ""
            pending: dict[int, dict] = {}
            request_ctx = HookContext(
                event="before_model_request",
                cwd=self.cwd,
                model=self.model,
                system=self.system,
                history=msgs,
            )
            run_extension_hooks(self.cwd, "before_model_request", request_ctx)
            self.system = request_ctx.system
            msgs = request_ctx.history
            if msgs and msgs[0].get("role") == "system":
                msgs[0]["content"] = self.system

            with client.chat.completions.create(
                model=self.model, messages=msgs,
                tools=self._tools_openai(), stream=True,
            ) as stream:
                for chunk in stream:
                    if self._cancel.is_set():
                        break
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
                msgs.append(assistant_msg)
                ordered = sorted(pending.items())
                tools = [(s["id"], s["name"], json.loads(s["args"])) for _, s in ordered]
                for tool_id, name, output in self._execute_tools(tools):
                    msgs.append({
                        "role":         "tool",
                        "tool_call_id": tool_id,
                        "content":      output,
                    })
            else:
                full_text += turn_text
                break

        return full_text

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
        blocked = self._check_tool_gate(name, inputs)
        if blocked:
            self.tool_result.emit(name, blocked)
            return tool_id, name, blocked
        self.tool_called.emit(name, inputs)
        on_line = (lambda line: self.bash_line.emit(line)) if name == "bash" else None
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
