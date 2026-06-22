from PyQt6.QtCore import QPointF, QRectF, Qt, QMimeData, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QFrame, QGraphicsView

from services.chat_drag import AICHS_FILE_DROP_MIME, parse_file_drop
from ui.theme import palette
from ui.widgets.agent_canvas_schema import AICHS_CANVAS_TOKEN_MIME, parse_canvas_token


class _GraphView(QGraphicsView):
    BASE_SCENE_RECT = QRectF(-2200, -1600, 4400, 3200)
    SCENE_GROW_MARGIN = 1400
    MIN_ZOOM = 0.12
    MAX_ZOOM = 2.4

    token_dropped = pyqtSignal(object, QPointF)
    files_dropped = pyqtSignal(object, QPointF)
    delete_requested = pyqtSignal()
    activate_requested = pyqtSignal()
    edit_requested = pyqtSignal()
    connection_cancel_requested = pyqtSignal()
    canvas_context_requested = pyqtSignal(QPointF)
    zoom_changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("canvasGraphView")
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setSceneRect(QRectF(self.BASE_SCENE_RECT))
        self._zoom = 1.0
        self._middle_pan_active = False
        self._middle_pan_last = None

    def dragEnterEvent(self, event):
        if self._can_accept(event.mimeData()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        mime = event.mimeData()
        point = self.mapToScene(event.position().toPoint())
        if mime.hasFormat(AICHS_CANVAS_TOKEN_MIME):
            token = parse_canvas_token(mime.data(AICHS_CANVAS_TOKEN_MIME))
            if token is None:
                event.ignore()
                return
            self.token_dropped.emit(token, point)
            event.acceptProposedAction()
            return
        if mime.hasFormat(AICHS_FILE_DROP_MIME):
            refs = parse_file_drop(mime.data(AICHS_FILE_DROP_MIME))
            if not refs:
                event.ignore()
                return
            self.files_dropped.emit(refs, point)
            event.acceptProposedAction()
            return
        event.ignore()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        self.zoom_by(1.12 if delta > 0 else 1 / 1.12)
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._middle_pan_active = True
            self._middle_pan_last = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.connection_cancel_requested.emit()
            if self.itemAt(event.position().toPoint()) is None:
                self.canvas_context_requested.emit(self.mapToScene(event.position().toPoint()))
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._middle_pan_active and self._middle_pan_last is not None:
            self.expand_scene_around_viewport()
            current = event.position().toPoint()
            delta = current - self._middle_pan_last
            self._middle_pan_last = current
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self.expand_scene_around_viewport()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.expand_scene_around_viewport()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton and self._middle_pan_active:
            self._middle_pan_active = False
            self._middle_pan_last = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_F2:
            self.edit_requested.emit()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.activate_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.connection_cancel_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        self.connection_cancel_requested.emit()
        super().focusOutEvent(event)

    def zoom_in(self):
        self.zoom_by(1.18)

    def zoom_out(self):
        self.zoom_by(1 / 1.18)

    def zoom_by(self, factor: float):
        target = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self._zoom * factor))
        if abs(target - self._zoom) < 0.0001:
            return
        factor = target / self._zoom
        self._zoom = target
        self.scale(factor, factor)
        self.expand_scene_around_viewport()
        self.zoom_changed.emit(self._zoom)

    def zoom_reset(self):
        if abs(self._zoom - 1.0) < 0.0001:
            return
        self.resetTransform()
        self._zoom = 1.0
        self.expand_scene_around_viewport()
        self.zoom_changed.emit(self._zoom)

    def expand_scene_around_viewport(self):
        if self.scene() is None or self.viewport() is None:
            return
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        self.ensure_scene_rect_contains(visible)

    def ensure_scene_rect_contains(self, target: QRectF | QPointF):
        target_rect = QRectF(target, target) if isinstance(target, QPointF) else QRectF(target)
        if target_rect.isNull():
            target_rect = QRectF(target_rect.topLeft(), target_rect.topLeft() + QPointF(1, 1))
        target_rect = target_rect.normalized().adjusted(
            -self.SCENE_GROW_MARGIN,
            -self.SCENE_GROW_MARGIN,
            self.SCENE_GROW_MARGIN,
            self.SCENE_GROW_MARGIN,
        )
        current = self.sceneRect()
        expanded = QRectF(self.BASE_SCENE_RECT).united(current).united(target_rect)
        if self._rect_changed(current, expanded):
            self.setSceneRect(expanded)

    @staticmethod
    def _rect_changed(left: QRectF, right: QRectF) -> bool:
        return (
            abs(left.left() - right.left()) > 0.5
            or abs(left.top() - right.top()) > 0.5
            or abs(left.right() - right.right()) > 0.5
            or abs(left.bottom() - right.bottom()) > 0.5
        )

    def drawBackground(self, painter: QPainter, rect: QRectF):
        p = palette()
        painter.fillRect(rect, QColor(p["BG"]))
        grid = 48
        fine = QColor(p["BORDER_SUBTLE"])
        fine.setAlpha(150)
        major = QColor(p["BORDER"])
        major.setAlpha(170)
        left = int(rect.left()) - (int(rect.left()) % grid)
        top = int(rect.top()) - (int(rect.top()) % grid)
        painter.setPen(QPen(fine, 1))
        x = left
        while x < rect.right():
            painter.drawLine(QPointF(float(x), rect.top()), QPointF(float(x), rect.bottom()))
            x += grid
        y = top
        while y < rect.bottom():
            painter.drawLine(QPointF(rect.left(), float(y)), QPointF(rect.right(), float(y)))
            y += grid
        painter.setPen(QPen(major, 1.2))
        painter.drawLine(QPointF(0.0, rect.top()), QPointF(0.0, rect.bottom()))
        painter.drawLine(QPointF(rect.left(), 0.0), QPointF(rect.right(), 0.0))

    @staticmethod
    def _can_accept(mime: QMimeData) -> bool:
        return mime.hasFormat(AICHS_CANVAS_TOKEN_MIME) or mime.hasFormat(AICHS_FILE_DROP_MIME)
