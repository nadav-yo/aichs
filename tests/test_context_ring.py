from PyQt6.QtCore import QEvent, QPointF
from PyQt6.QtGui import QEnterEvent

from ui.widgets.context_ring import ContextRing


def test_context_ring_tracks_hover(qapp):
    ring = ContextRing()
    pos = QPointF(14, 14)

    ring.enterEvent(QEnterEvent(pos, pos, pos))
    assert ring._hovered is True

    ring.leaveEvent(QEvent(QEvent.Type.Leave))
    assert ring._hovered is False
