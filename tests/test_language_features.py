from services.code_completion import CompletionItem
from services.language_features import (
    CodeActionResult,
    Diagnostic,
    LanguageCompletionProvider,
    LanguageService,
    apply_code_action,
    code_actions,
    completions,
    diagnostics,
    format_document,
    format_file,
    language_status,
    symbols,
)
from tests.conftest import write_extension


def test_language_diagnostics_match_file_patterns_and_context(workspace):
    path = workspace / "src" / "main.py"
    write_extension(
        workspace,
        "python_lang.py",
        """
        def register(registry):
            registry.language(
                name="python",
                file_patterns=["src/*.py"],
                diagnostics=diagnose,
            )

        def diagnose(ctx):
            assert ctx.path.endswith("main.py")
            assert "problem" in ctx.content
            ctx.storage.save_state({"seen": True}, name="diagnostics")
            return [{
                "line": 2,
                "column": 4,
                "severity": "error",
                "message": "boom",
                "source": "fake",
                "fix_available": True,
                "fix_safety": "unsafe",
                "data": {"rule_url": "https://example.test/rule"},
            }]
        """,
    )

    items, errors = diagnostics(str(workspace), str(path), "ok\nproblem\n")

    assert errors == []
    assert items[0].line == 2
    assert items[0].column == 4
    assert items[0].severity == "error"
    assert items[0].message == "boom"
    assert items[0].fix_available is True
    assert items[0].fix_safety == "unsafe"
    assert items[0].data == {"rule_url": "https://example.test/rule"}
    state_path = workspace / ".aichs" / "state" / "python_lang" / "diagnostics.json"
    assert state_path.exists()


def test_language_diagnostics_accept_alias_metadata_and_fix_safety(workspace):
    path = workspace / "src" / "main.py"
    write_extension(
        workspace,
        "python_lang.py",
        """
        def register(registry):
            registry.language(
                name="python",
                file_patterns=["*.py"],
                diagnostics=lambda ctx: [{
                    "line": 1,
                    "message": "alias metadata",
                    "fixAvailable": True,
                    "fixSafety": "safe",
                    "metadata": {"rule": "demo"},
                }],
            )
        """,
    )

    items, errors = diagnostics(str(workspace), str(path), "x\n")

    assert errors == []
    assert items[0].fix_available is True
    assert items[0].fix_safety == "safe"
    assert items[0].data == {"rule": "demo"}


def test_language_features_ignore_non_matching_patterns(workspace):
    path = workspace / "notes.txt"
    path.write_text("note\n", encoding="utf-8")
    write_extension(
        workspace,
        "python_lang.py",
        """
        def register(registry):
            registry.language(
                name="python",
                file_patterns=["*.py"],
                diagnostics=lambda ctx: [{"line": 1, "message": "wrong"}],
            )
        """,
    )

    items, errors = diagnostics(str(workspace), str(path), "note\n")

    assert errors == []
    assert items == []


def test_language_symbols_and_completion_normalize_results(workspace):
    path = workspace / "src" / "main.py"
    write_extension(
        workspace,
        "python_lang.py",
        """
        def register(registry):
            registry.language(
                name="python",
                file_patterns=["*.py"],
                symbols=lambda ctx: {"items": [{"name": "App", "kind": "class", "line": 3}]},
                completion=complete,
            )

        def complete(ctx):
            assert ctx.prefix == "ren"
            assert ctx.position == len(ctx.content)
            return [
                {"label": "render", "insert_text": "render()", "detail": "fake"},
                "renderer",
            ]
        """,
    )

    symbol_items, symbol_errors = symbols(str(workspace), str(path), "class App:\n    pass\n")
    completion_items, completion_errors = completions(str(workspace), str(path), "ren", 3, "ren")

    assert symbol_errors == []
    assert completion_errors == []
    assert symbol_items[0].name == "App"
    assert symbol_items[0].kind == "class"
    assert completion_items == [
        CompletionItem(label="render", insert_text="render()", detail="fake"),
        CompletionItem(label="renderer", insert_text="renderer", detail=""),
    ]


def test_language_code_actions_list_and_apply(workspace):
    path = workspace / "src" / "main.py"
    write_extension(
        workspace,
        "fix_lang.py",
        """
        def register(registry):
            registry.language(
                name="python",
                file_patterns=["*.py"],
                code_actions=actions,
            )

        def actions(ctx):
            if ctx.action_id == "demo.fix":
                assert ctx.diagnostics[0].message == "boom"
                return {
                    "content": ctx.content.replace("bad", "good"),
                    "message": "fixed",
                }
            return [{
                "id": "demo.fix",
                "title": "Fix demo",
                "kind": "quickfix",
                "source": "demo",
                "safety": "unsafe",
            }]
        """,
    )
    diagnostic = Diagnostic(path=str(path), line=1, column=0, message="boom")

    actions, action_errors = code_actions(
        str(workspace),
        str(path),
        "bad\n",
        [diagnostic],
    )
    result, apply_errors = apply_code_action(
        str(workspace),
        str(path),
        "bad\n",
        "demo.fix",
        [diagnostic],
    )

    assert action_errors == []
    assert actions[0].id == "demo.fix"
    assert actions[0].title == "Fix demo"
    assert actions[0].safe is False
    assert actions[0].safety == "unsafe"
    assert apply_errors == []
    assert result == CodeActionResult(content="good\n", message="fixed")


def test_language_code_actions_accept_safe_flag_and_metadata(workspace):
    path = workspace / "src" / "main.py"
    write_extension(
        workspace,
        "fix_lang.py",
        """
        def register(registry):
            registry.language(
                name="python",
                file_patterns=["*.py"],
                code_actions=lambda ctx: [{
                    "id": "demo.unsafe",
                    "title": "Unsafe demo",
                    "safe": False,
                    "metadata": {"why": "demo"},
                }],
            )
        """,
    )

    actions, errors = code_actions(str(workspace), str(path), "bad\n")

    assert errors == []
    assert actions[0].safe is False
    assert actions[0].safety == "unsafe"
    assert actions[0].data == {"why": "demo"}


def test_language_explicit_apply_code_action_and_format_document(workspace):
    path = workspace / "src" / "main.py"
    write_extension(
        workspace,
        "format_lang.py",
        """
        def register(registry):
            registry.language(
                name="python",
                file_patterns=["*.py"],
                code_actions=lambda ctx: [{
                    "id": "demo.fix",
                    "title": "Fix demo",
                    "safety": "safe",
                }],
                apply_code_action=apply,
                format_document=fmt,
            )

        def apply(ctx):
            assert ctx.action_id == "demo.fix"
            return {"content": ctx.content.replace("bad", "good"), "message": "applied"}

        def fmt(ctx):
            return {"content": ctx.content.strip() + "\\n", "message": "formatted"}
        """,
    )

    result, apply_errors = apply_code_action(
        str(workspace),
        str(path),
        "bad\n\n",
        "demo.fix",
    )
    formatted, format_errors = format_document(str(workspace), str(path), "x = 1\n\n")

    assert apply_errors == []
    assert result == CodeActionResult(content="good\n\n", message="applied")
    assert format_errors == []
    assert formatted == CodeActionResult(content="x = 1\n", message="formatted")


def test_language_format_file_uses_buffer_without_writing(workspace):
    path = workspace / "src" / "main.py"
    path.write_text("disk\n", encoding="utf-8")
    write_extension(
        workspace,
        "format_lang.py",
        """
        def register(registry):
            registry.language(
                name="python",
                file_patterns=["*.py"],
                format_document=fmt,
            )

        def fmt(ctx):
            assert ctx.content == "buffer\\n\\n"
            return {"content": ctx.content.strip() + "\\n", "message": "formatted"}
        """,
    )

    result, errors = format_file(str(workspace), "src/main.py", content="buffer\n\n")

    assert errors == []
    assert result == CodeActionResult(content="buffer\n", message="formatted")
    assert path.read_text(encoding="utf-8") == "disk\n"


def test_language_format_file_reads_file_without_writing(workspace):
    path = workspace / "src" / "main.py"
    path.write_text("disk\n\n", encoding="utf-8")
    write_extension(
        workspace,
        "format_lang.py",
        """
        def register(registry):
            registry.language(
                name="python",
                file_patterns=["*.py"],
                format_document=lambda ctx: {"content": ctx.content.strip() + "\\n"},
            )
        """,
    )

    result, errors = LanguageService(str(workspace)).format_file("src/main.py")

    assert errors == []
    assert result == CodeActionResult(content="disk\n")
    assert path.read_text(encoding="utf-8") == "disk\n\n"


def test_language_format_file_blocks_outside_workspace(workspace, tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")

    result, errors = format_file(str(workspace), str(outside))

    assert result.content is None
    assert result.message.startswith("Format blocked:")
    assert errors == [result.message]
    assert outside.read_text(encoding="utf-8") == "x = 1\n"


def test_language_service_facade_matches_free_functions(workspace):
    path = workspace / "src" / "main.py"
    write_extension(
        workspace,
        "service_lang.py",
        """
        def register(registry):
            registry.language(
                name="python",
                file_patterns=["*.py"],
                diagnostics=lambda ctx: [{"line": 1, "message": "service"}],
                format_document=lambda ctx: {"content": ctx.content.strip() + "\\n"},
            )
        """,
    )
    service = LanguageService(str(workspace))

    assert service.diagnostics(str(path), "x\n")[0] == diagnostics(str(workspace), str(path), "x\n")[0]
    assert service.format_document(str(path), "x\n\n")[0] == format_document(str(workspace), str(path), "x\n\n")[0]


def test_language_status_reports_features_and_requirements(workspace):
    ext_dir = workspace / ".aichs" / "extensions" / "python-status"
    ext_dir.mkdir(parents=True)
    (ext_dir / "aichs-extension.json").write_text(
        '{"requires": {"executables": ["definitely-missing-aichs-tool"], "python": ["json"]}}\n',
        encoding="utf-8",
    )
    (ext_dir / "extension.py").write_text(
        "def register(registry):\n"
        "    registry.language(\n"
        "        name='python',\n"
        "        file_patterns=['*.py'],\n"
        "        diagnostics=lambda ctx: [],\n"
        "        symbols=lambda ctx: [],\n"
        "        completion=lambda ctx: [],\n"
        "        code_actions=lambda ctx: [],\n"
        "        apply_code_action=lambda ctx: {},\n"
        "        format_document=lambda ctx: {},\n"
        "    )\n",
        encoding="utf-8",
    )

    statuses, errors = language_status(str(workspace))

    assert errors == []
    assert len(statuses) == 1
    status = statuses[0]
    assert status.extension_id == "python-status"
    assert status.language == "python"
    assert status.file_patterns == ("*.py",)
    assert status.features == (
        "diagnostics",
        "symbols",
        "completion",
        "code_actions",
        "apply_code_action",
        "format_document",
    )
    assert status.requirements == {
        "executables": ("definitely-missing-aichs-tool",),
        "python": ("json",),
    }
    assert status.missing_requirements == ("executable:definitely-missing-aichs-tool",)
    assert status.ready is False


def test_language_status_reports_extension_errors(workspace):
    write_extension(
        workspace,
        "broken_language.py",
        """
        def register(registry):
            raise RuntimeError("language broken")
        """,
    )

    statuses, errors = LanguageService(str(workspace)).status()

    assert statuses == []
    assert "language broken" in errors[0]


def test_language_format_document_no_provider(workspace):
    path = workspace / "src" / "main.py"
    write_extension(
        workspace,
        "python_lang.py",
        """
        def register(registry):
            registry.language(
                name="python",
                file_patterns=["*.py"],
                diagnostics=lambda ctx: [],
            )
        """,
    )

    result, errors = format_document(str(workspace), str(path), "x = 1\n")

    assert errors == []
    assert result == CodeActionResult(message="No formatter available.")


def test_language_format_document_errors_are_captured(workspace):
    path = workspace / "src" / "main.py"
    write_extension(
        workspace,
        "python_lang.py",
        """
        def register(registry):
            registry.language(
                name="python",
                file_patterns=["*.py"],
                format_document=lambda ctx: 1 / 0,
            )
        """,
    )

    result, errors = format_document(str(workspace), str(path), "x = 1\n")

    assert result == CodeActionResult(message="No formatter available.")
    assert "language format_document python" in errors[0]


def test_language_provider_errors_are_captured(workspace):
    path = workspace / "src" / "main.py"
    write_extension(
        workspace,
        "bad_lang.py",
        """
        def register(registry):
            registry.language(
                name="python",
                file_patterns=["*.py"],
                diagnostics=lambda ctx: 1 / 0,
            )
        """,
    )

    items, errors = diagnostics(str(workspace), str(path), "x\n")

    assert items == []
    assert "language diagnostics python" in errors[0]


def test_language_completion_provider_keeps_local_fallback(workspace):
    path = workspace / "src" / "main.py"
    provider = LanguageCompletionProvider(str(workspace))

    items = provider.complete(
        path=str(path),
        content="renderer\nren",
        position=len("renderer\nren"),
        prefix="ren",
    )

    assert any(item.label == "renderer" for item in items)
