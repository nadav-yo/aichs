from PyQt6.QtWidgets import QWidget

from services.context_budget import ContextBudget, ContextSegment
from ui.widgets.context_breakdown import ContextBreakdownDialog, _ROW_OBJECT_NAME


def test_context_breakdown_rows_use_transparent_widgets(qapp):
    budget = ContextBudget(
        segments=[
            ContextSegment("System prompt", "hello"),
            ContextSegment("Messages", "world", "0 messages"),
        ],
        window_tokens=32_768,
        reserve_tokens=6_553,
    )
    dialog = ContextBreakdownDialog(budget, "test-model")
    dialog.show()
    qapp.processEvents()

    assert "background: transparent" in dialog.styleSheet()
    rows = [
        row
        for row in dialog.findChildren(QWidget)
        if row.objectName() == _ROW_OBJECT_NAME
    ]
    assert len(rows) == 2

    dialog.close()