from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QRectF, pyqtSignal
from PyQt6.QtGui import QPainter, QPen, QColor, QFont

from services.context_budget import ContextBudget, format_bytes
from ui.theme import palette


def _ring_color(pct: float) -> str:
    if pct >= 90:
        return "#ff3b30"
    if pct >= 70:
        return "#ff9500"
    return "#34c759"


class ContextRing(QWidget):
    """Circular context-usage indicator; click to open the breakdown dialog."""

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._budget: ContextBudget | None = None
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Context usage")

    def set_budget(self, budget: ContextBudget | None):
        self._budget = budget
        if budget:
            self.setToolTip(
                f"Context: {budget.used_tokens:,} / {budget.window_tokens:,} tokens "
                f"({budget.pct:.0f}%)\nClick for breakdown"
            )
        self.update()

    def paintEvent(self, _event):
        p = palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(4, 4, 20, 20)
        track = QPen(QColor(p["BORDER"]), 3)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track)
        painter.drawArc(rect, 0, 360 * 16)

        pct = self._budget.pct if self._budget else 0.0
        if pct > 0:
            color = QPen(QColor(_ring_color(pct)), 3)
            color.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(color)
            span = int(-pct / 100 * 360 * 16)
            painter.drawArc(rect, 90 * 16, span)

        if self._budget and self._budget.pct >= 99:
            painter.setPen(QColor(p["TEXT_DIM"]))
            painter.setFont(QFont("", 8))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "!")

        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)
