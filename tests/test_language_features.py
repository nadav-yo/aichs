from services.code_completion import CompletionItem
from services.language_features import (
    LanguageCompletionProvider,
    completions,
    diagnostics,
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
            }]
        """,
    )

    items, errors = diagnostics(str(workspace), str(path), "ok\nproblem\n")

    assert errors == []
    assert items[0].line == 2
    assert items[0].column == 4
    assert items[0].severity == "error"
    assert items[0].message == "boom"
    state_path = workspace / ".aichs" / "state" / "python_lang" / "diagnostics.json"
    assert state_path.exists()


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
