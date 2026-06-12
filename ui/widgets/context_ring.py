from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QRectF, pyqtSignal
from PyQt6.QtGui import QEnterEvent, QPainter, QPen, QColor, QFont

from services.context_budget import ContextBudget
from ui.theme import palette


def _ring_color(pct: float) -> str:
    if pct >= 90:
        return "#ff3b30"
    if pct >= 70:
        return "#ff9500"
    return "#34c759"


def _ring_pen(color: str, width: float, *, alpha: int = 255) -> QPen:
    c = QColor(color)
    c.setAlpha(alpha)
    pen = QPen(c, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen


class ContextRing(QWidget):
    """Circular context-usage indicator; click to open the breakdown dialog."""

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._budget: ContextBudget | None = None
        self._hovered = False
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Context usage")
        self.setAccessibleName("Context usage")

    def enterEvent(self, event: QEnterEvent):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

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
        painter.setBrush(Qt.BrushStyle.NoBrush)

        rect = QRectF(4, 4, 20, 20)
        pct = self._budget.pct if self._budget else 0.0
        span = int(-pct / 100 * 360 * 16) if pct > 0 else 0

        if self._hovered:
            painter.setPen(_ring_pen(p["TEXT_DIM"], 5, alpha=72))
            painter.drawArc(rect, 0, 360 * 16)
            if span:
                painter.setPen(_ring_pen(_ring_color(pct), 5, alpha=96))
                painter.drawArc(rect, 90 * 16, span)

        track_color = p["TEXT_DIM"] if self._hovered else p["BORDER"]
        painter.setPen(_ring_pen(track_color, 3))
        painter.drawArc(rect, 0, 360 * 16)

        if span:
            painter.setPen(_ring_pen(_ring_color(pct), 3))
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
