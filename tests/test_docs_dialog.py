from ui.widgets.docs_dialog import (
    DocsDialog,
    available_docs,
    doc_title,
    markdown_document_html,
)


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


def test_doc_title_reads_first_heading(tmp_path):
    path = tmp_path / "custom-models.md"
    path.write_text("# Custom Model Providers\n\nBody", encoding="utf-8")

    assert doc_title(path) == "Custom Model Providers"


def test_markdown_document_html_renders_tables():
    html = markdown_document_html("| A | B |\n|---|---|\n| 1 | 2 |\n")

    assert "<table>" in html
    assert "markdown-body" not in html


def test_docs_dialog_loads_markdown(qapp, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "configuration.md").write_text("# Configuration\n\nHello **docs**.", encoding="utf-8")

    dialog = DocsDialog(root=docs)
    try:
        qapp.processEvents()

        assert dialog.nav.count() == 1
        assert dialog.nav.item(0).text() == "Configuration"
        assert "Hello" in dialog.viewer.toPlainText()
    finally:
        dialog.close()
