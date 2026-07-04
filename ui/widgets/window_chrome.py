from PyQt6.QtCore import QObject, QEvent, QPoint, QSize, QTimer, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from ui.theme import palette, window_chrome_button_style, window_chrome_frame_style, window_chrome_style


_RESIZE_BORDER = 8


def _empty_edges():
    return Qt.Edge(0)


def _resize_edges_for_pos(frame, pos: QPoint, *, border: int = _RESIZE_BORDER, maximized: bool = False):
    if maximized or frame.isNull():
        return _empty_edges()
    on_left = frame.left() <= pos.x() < frame.left() + border
    on_right = frame.right() - border < pos.x() <= frame.right()
    on_top = frame.top() <= pos.y() < frame.top() + border
    on_bottom = frame.bottom() - border < pos.y() <= frame.bottom()
    edges = _empty_edges()
    if on_left:
        edges |= Qt.Edge.LeftEdge
    elif on_right:
        edges |= Qt.Edge.RightEdge
    if on_top:
        edges |= Qt.Edge.TopEdge
    elif on_bottom:
        edges |= Qt.Edge.BottomEdge
    return edges


def _window_control_icon(role: str, *, maximized: bool = False) -> QIcon:
    p = palette()
    color = QColor(p["TEXT"] if role == "close" else p["TEXT_DIM"])
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if role == "minimize":
        painter.drawLine(4, 10, 12, 10)
    elif role == "maximize" and maximized:
        painter.drawRect(5, 6, 7, 6)
        painter.drawLine(7, 4, 13, 4)
        painter.drawLine(13, 4, 13, 9)
    elif role == "maximize":
        painter.drawRect(4, 4, 8, 8)
    else:
        painter.drawLine(5, 5, 11, 11)
        painter.drawLine(11, 5, 5, 11)
    painter.end()
    return QIcon(pixmap)


def _resize_cursor_for_edges(edges):
    if not edges:
        return None
    horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
    vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
    if horizontal and vertical:
        if edges in (Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.Edge.RightEdge | Qt.Edge.BottomEdge):
            return Qt.CursorShape.SizeFDiagCursor
        return Qt.CursorShape.SizeBDiagCursor
    if horizontal:
        return Qt.CursorShape.SizeHorCursor
    return Qt.CursorShape.SizeVerCursor


class _WindowResizeFilter(QObject):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._window = window

    def eventFilter(self, obj, event):
        if not self._is_window_event_target(obj):
            return False
        if event.type() == QEvent.Type.MouseMove:
            self._sync_cursor(event)
            return False
        if event.type() == QEvent.Type.Leave:
            self._window.unsetCursor()
            return False
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            return self._start_resize(event)
        return False

    def _is_window_event_target(self, obj) -> bool:
        try:
            if obj is self._window:
                return True
            return isinstance(obj, QWidget) and self._window.isAncestorOf(obj)
        except RuntimeError:
            return False

    def _event_pos(self, event) -> QPoint:
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        return event.globalPos()

    def _edges_for_event(self, event):
        return _resize_edges_for_pos(
            self._window.frameGeometry(),
            self._event_pos(event),
            maximized=self._window.isMaximized() or self._window.isFullScreen(),
        )

    def _sync_cursor(self, event) -> None:
        cursor = _resize_cursor_for_edges(self._edges_for_event(event))
        if cursor is None:
            self._window.unsetCursor()
        else:
            self._window.setCursor(cursor)

    def _start_resize(self, event) -> bool:
        edges = self._edges_for_event(event)
        if not edges:
            return False
        handle = self._window.windowHandle()
        if handle is None:
            return False
        if handle.startSystemResize(edges):
            event.accept()
            return True
        return False


class _WindowCornerPreferenceFilter(QObject):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._window = window

    def eventFilter(self, obj, event):
        if obj is self._window and event.type() in {
            QEvent.Type.Show,
            QEvent.Type.WindowStateChange,
        }:
            QTimer.singleShot(0, self._apply)
        return False

    def _apply(self) -> None:
        self._window.clearMask()
        from ui.win_caption import apply_windows_corner_preference

        apply_windows_corner_preference(self._window)


class _ChromeDragArea(QWidget):
    def __init__(self, window, *, allow_maximize: bool = True, parent=None):
        super().__init__(parent)
        self._window = window
        self._allow_maximize = allow_maximize
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self._window.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self._allow_maximize and event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _toggle_maximized(self):
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()


class WindowChrome(QWidget):
    def __init__(self, window, *, allow_minimize: bool = True, allow_maximize: bool = True, parent=None):
        super().__init__(parent)
        self._window = window
        self._allow_minimize = allow_minimize
        self._allow_maximize = allow_maximize
        self.setObjectName("windowChrome")
        self.setFixedHeight(34)
        self._window.installEventFilter(self)

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 0, 0, 0)
        root.setSpacing(0)

        self._drag_area = _ChromeDragArea(window, allow_maximize=allow_maximize, parent=self)
        drag_layout = QHBoxLayout(self._drag_area)
        drag_layout.setContentsMargins(0, 0, 8, 0)
        drag_layout.setSpacing(8)

        self._icon = QLabel()
        self._icon.setObjectName("windowChromeIcon")
        self._icon.setFixedSize(18, 18)
        drag_layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)

        self._title = QLabel(window.windowTitle() or "AICHS")
        self._title.setObjectName("windowChromeTitle")
        drag_layout.addWidget(self._title, 0, Qt.AlignmentFlag.AlignVCenter)
        drag_layout.addStretch(1)
        root.addWidget(self._drag_area, 1)

        self._minimize_btn = self._window_button("minimize", "Minimize")
        self._maximize_btn = self._window_button("maximize", "Maximize")
        self._close_btn = self._window_button("close", "Close")
        root.addWidget(self._minimize_btn)
        root.addWidget(self._maximize_btn)
        root.addWidget(self._close_btn)

        self._minimize_btn.setVisible(allow_minimize)
        self._maximize_btn.setVisible(allow_maximize)
        self._minimize_btn.clicked.connect(window.showMinimized)
        self._maximize_btn.clicked.connect(self._toggle_maximized)
        self._close_btn.clicked.connect(window.close)
        self.apply_appearance()
        self.sync_window_state()

    def _window_button(self, role: str, tooltip: str) -> QPushButton:
        button = QPushButton("")
        button.setObjectName(f"windowChrome{role.title()}")
        button.setProperty("chromeRole", role)
        button.setAccessibleName(tooltip)
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedSize(46, 34)
        button.setIconSize(self._button_icon_size())
        return button

    def _button_icon_size(self):
        return QSize(16, 16)

    def _toggle_maximized(self):
        if not self._allow_maximize:
            return
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self.sync_window_state()

    def eventFilter(self, obj, event):
        if obj is self._window and event.type() in {
            QEvent.Type.WindowIconChange,
            QEvent.Type.WindowStateChange,
            QEvent.Type.WindowTitleChange,
        }:
            self.sync_window_state()
        return False

    def apply_appearance(self):
        self.setStyleSheet(window_chrome_style())
        for button in (self._minimize_btn, self._maximize_btn, self._close_btn):
            role = str(button.property("chromeRole") or "")
            button.setStyleSheet(window_chrome_button_style(role=role))
        self.sync_window_state()

    def sync_window_state(self):
        icon = self._window.windowIcon()
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self._icon.setPixmap(icon.pixmap(16, 16))
        self._title.setText(self._window.windowTitle() or "AICHS")
        self._minimize_btn.setIcon(_window_control_icon("minimize"))
        if self._window.isMaximized():
            self._maximize_btn.setIcon(_window_control_icon("maximize", maximized=True))
            self._maximize_btn.setToolTip("Restore")
            self._maximize_btn.setAccessibleName("Restore")
        else:
            self._maximize_btn.setIcon(_window_control_icon("maximize"))
            self._maximize_btn.setToolTip("Maximize")
            self._maximize_btn.setAccessibleName("Maximize")
        self._close_btn.setIcon(_window_control_icon("close"))


def _remove_event_filter(target, filt) -> None:
    try:
        target.removeEventFilter(filt)
    except RuntimeError:
        pass


def _ensure_transparent_window_background(window) -> None:
    marker = "/* aichs-custom-chrome-root */"
    style = window.styleSheet() or ""
    if marker in style:
        return
    window.setStyleSheet(
        f"{style}\n{marker}\nQDialog, QMainWindow {{ background: transparent; }}"
    )


def configure_custom_window(window, *, resizable: bool = True) -> None:
    window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    window.setProperty("_aichsCustomChrome", True)
    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    window.setAutoFillBackground(False)
    _ensure_transparent_window_background(window)

    if not hasattr(window, "_aichs_corner_filter"):
        corner_filter = _WindowCornerPreferenceFilter(window, window)
        window.installEventFilter(corner_filter)
        window._aichs_corner_filter = corner_filter

    if resizable and not hasattr(window, "_aichs_resize_filter"):
        resize_filter = _WindowResizeFilter(window, window)
        target = QApplication.instance() or window
        target.installEventFilter(resize_filter)
        window.destroyed.connect(lambda: _remove_event_filter(target, resize_filter))
        window._aichs_resize_filter = resize_filter


def _apply_chrome_frame_style(frame: QWidget) -> None:
    frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    frame.setStyleSheet(window_chrome_frame_style())


def set_chromed_central_widget(window, content: QWidget) -> QWidget:
    configure_custom_window(window, resizable=True)
    shell = QWidget()
    shell.setObjectName("windowChromeFrame")
    _apply_chrome_frame_style(shell)
    shell_layout = QVBoxLayout(shell)
    shell_layout.setContentsMargins(1, 1, 1, 1)
    shell_layout.setSpacing(0)
    chrome = WindowChrome(window)
    shell_layout.addWidget(chrome)
    shell_layout.addWidget(content, 1)
    window.setCentralWidget(shell)
    window._window_shell = shell
    window._window_shell_layout = shell_layout
    window._window_chrome = chrome
    return shell


def chromed_dialog_layout(
    dialog,
    layout_class=QVBoxLayout,
    *,
    contents_margins=(14, 14, 14, 14),
    spacing: int = 10,
):
    configure_custom_window(dialog, resizable=True)
    content = QWidget(dialog)
    content.setObjectName("windowChromeContent")
    content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    content_layout = layout_class(content)
    content_layout.setContentsMargins(*contents_margins)
    content_layout.setSpacing(spacing)

    frame = QWidget(dialog)
    frame.setObjectName("windowChromeFrame")
    _apply_chrome_frame_style(frame)
    frame_layout = QVBoxLayout(frame)
    frame_layout.setContentsMargins(1, 1, 1, 1)
    frame_layout.setSpacing(0)
    chrome = WindowChrome(dialog, allow_minimize=False, allow_maximize=False)
    frame_layout.addWidget(chrome)
    frame_layout.addWidget(content, 1)

    root = QVBoxLayout(dialog)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)
    root.addWidget(frame, 1)
    dialog._window_chrome = chrome
    dialog._window_chrome_frame = frame
    dialog._window_chrome_content = content
    return content_layout