import time

from PyQt6.QtGui import QCloseEvent

from ui.widgets.docs_dialog import (
    _DocLoadWorker,
    _DocsIndexWorker,
    DocsDialog,
    available_doc_entries,
    available_docs,
    doc_title,
    markdown_document_html,
)
from tests.conftest import write_extension


def test_available_docs_uses_known_order_then_extras(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    for name in ("z-extra.md", "skills.md", "configuration.md"):
        (docs / name).write_text(f"# {name}\n", encoding="utf-8")

    assert [path.name for path in available_docs(docs)] == [
        "configuration.md",
        "skills.md",
        "z-extra.md",
    ]


def test_available_doc_entries_includes_extension_docs(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "configuration.md").write_text("# Configuration\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    doc_dir = workspace / ".aichs" / "extensions" / "decision-memory" / "docs"
    doc_dir.mkdir(parents=True)
    (doc_dir / "decision-memory.md").write_text("# Decision Memory\n", encoding="utf-8")
    write_extension(
        workspace,
        "decision-memory/extension.py",
        """
        def register(registry):
            registry.doc(
                name="decision_memory",
                title="Decision Memory",
                path="docs/decision-memory.md",
            )
        """,
    )

    entries = available_doc_entries(docs, str(workspace))

    assert [entry.display_title for entry in entries] == [
        "Configuration",
        "Decision Memory [EXTENSION]",
    ]
    assert entries[1].is_extension is True


def test_docs_dialog_labels_extension_docs(qapp, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    workspace = tmp_path / "workspace"
    doc_dir = workspace / ".aichs" / "extensions" / "decision-memory" / "docs"
    doc_dir.mkdir(parents=True)
    (doc_dir / "decision-memory.md").write_text("# Decision Memory\n\nDurable choices.", encoding="utf-8")
    write_extension(
        workspace,
        "decision-memory/extension.py",
        """
        def register(registry):
            registry.doc(
                name="decision_memory",
                title="Decision Memory",
                path="docs/decision-memory.md",
            )
        """,
    )

    dialog = DocsDialog(root=docs, cwd=str(workspace))
    try:
        assert _process_until(qapp, lambda: dialog.nav.count() == 1)
        assert dialog.nav.item(0).text() == "Decision Memory [EXTENSION]"
        assert _process_until(qapp, lambda: "Durable choices" in dialog.viewer.toPlainText())
    finally:
        dialog.close()


def test_doc_title_reads_first_heading(tmp_path):
    path = tmp_path / "custom-models.md"
    path.write_text("# Custom Model Providers\n\nBody", encoding="utf-8")

    assert doc_title(path) == "Custom Model Providers"


def test_markdown_document_html_renders_tables():
    html = markdown_document_html("| A | B |\n|---|---|\n| 1 | 2 |\n")

    assert "<table>" in html
    assert "markdown-body" not in html


def test_docs_index_worker_reads_titles(qapp, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "configuration.md").write_text("# Configuration\n\nBody", encoding="utf-8")
    worker = _DocsIndexWorker(3, docs)
    done = []
    worker.signals.done.connect(lambda *args: done.append(args))

    worker.run()

    assert done == [(3, [(
        "configuration.md",
        "Configuration",
        str((docs / "configuration.md").resolve()),
        False,
        "",
    )], "")]


def test_doc_load_worker_reads_selected_doc(qapp, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "skills.md").write_text("# Skills\n\nBody", encoding="utf-8")
    worker = _DocLoadWorker(7, docs, "skills.md")
    done = []
    worker.signals.done.connect(lambda *args: done.append(args))

    worker.run()

    assert done == [(7, str((docs / "skills.md").resolve()), "# Skills\n\nBody", "")]


def test_docs_dialog_loads_markdown(qapp, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "configuration.md").write_text("# Configuration\n\nHello **docs**.", encoding="utf-8")

    dialog = DocsDialog(root=docs)
    try:
        assert _process_until(qapp, lambda: dialog.nav.count() == 1)
        assert _process_until(qapp, lambda: "Hello" in dialog.viewer.toPlainText())

        assert dialog.nav.count() == 1
        assert dialog.nav.item(0).text() == "Configuration"
        assert "QListWidget::item:selected" in dialog.nav.styleSheet()
        assert "QListWidget::item:selected:focus" in dialog.nav.styleSheet()
        assert "Hello" in dialog.viewer.toPlainText()
    finally:
        dialog.close()


def test_docs_dialog_ignores_stale_index_results(qapp, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    dialog = DocsDialog(root=docs)
    try:
        dialog._pool.waitForDone(1000)
        qapp.processEvents()
        dialog._index_generation = 2

        dialog._on_docs_index_ready(1, [("old.md", "Old")], "")

        assert dialog.nav.count() == 0
    finally:
        dialog.close()


def test_docs_dialog_ignores_stale_doc_results(qapp, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "configuration.md").write_text("# Configuration\n\nFresh", encoding="utf-8")
    dialog = DocsDialog(root=docs)
    try:
        assert _process_until(qapp, lambda: dialog.nav.count() == 1)
        dialog._pool.waitForDone(1000)
        qapp.processEvents()
        original = dialog.viewer.toPlainText()
        dialog._doc_generation = 2

        dialog._on_doc_ready(1, "configuration.md", "# Old\n\nStale", "")

        assert dialog.viewer.toPlainText() == original
    finally:
        dialog.close()


def test_docs_dialog_close_invalidates_work_without_waiting(qapp, tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setattr(
        "ui.widgets.docs_dialog.QThreadPool.start",
        lambda _pool, _worker: None,
    )
    waited = []
    dialog = DocsDialog(root=docs)
    dialog._index_generation = 3
    dialog._doc_generation = 5
    monkeypatch.setattr(
        "ui.widgets.docs_dialog.QThreadPool.waitForDone",
        lambda *_args: waited.append("wait"),
    )

    dialog.closeEvent(QCloseEvent())

    assert dialog._index_generation == 4
    assert dialog._doc_generation == 6
    assert waited == []


def _process_until(qapp, predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return predicate()
