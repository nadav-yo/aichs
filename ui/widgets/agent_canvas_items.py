import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QStyle,
)

from ui.theme import mono_font, palette
from ui.widgets.agent_canvas_schema import (
    CanvasToken,
    NODE_STATUSES,
    PortSpec,
    component_spec,
    edge_color,
    input_ports,
    output_ports,
)


class _GraphFrame(QGraphicsPathItem):
    HEADER_HEIGHT = 30.0
    MIN_WIDTH = 260.0
    MIN_HEIGHT = 180.0

    def __init__(
        self,
        frame_id: int,
        title: str,
        color: str,
        rect: QRectF,
        *,
        root_id: int | None,
        node_ids: set[int] | None,
        selected,
        activated,
        parent=None,
    ):
        super().__init__(parent)
        self.frame_id = frame_id
        self.title = str(title or "Graph").strip() or "Graph"
        self.color = self._normalized_color(color)
        self.root_id = root_id
        self.node_ids = set(node_ids or set())
        self._selected = selected
        self._activated = activated
        self._rect = QRectF(0, 0, self.MIN_WIDTH, self.MIN_HEIGHT)
        self.setFlags(self.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setZValue(-20)
        self.set_rect(rect)
        self._update_tooltip()

    def boundingRect(self):
        return self._rect.adjusted(-2, -2, 2, 2)

    def shape(self):
        path = QPainterPath()
        path.addRoundedRect(self._rect, 14, 14)
        return path

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        p = palette()
        color = QColor(self.color)
        fill = QColor(color)
        fill.setAlpha(34 if not self.isSelected() else 54)
        border = QColor(color)
        border.setAlpha(125 if not self.isSelected() else 220)
        painter.setPen(QPen(border, 1.5 if not self.isSelected() else 2.2, Qt.PenStyle.SolidLine))
        painter.setBrush(fill)
        painter.drawRoundedRect(self._rect, 14, 14)

        header = QRectF(14, 0, max(0.0, self._rect.width() - 28), self.HEADER_HEIGHT)
        title_font = QFont()
        title_font.setPointSize(9)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor(p["TEXT"]))
        metrics = QFontMetrics(title_font)
        title = metrics.elidedText(self.title, Qt.TextElideMode.ElideRight, int(header.width()))
        painter.drawText(header, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)

    def itemChange(self, change, value):
        if change == self.GraphicsItemChange.ItemSelectedHasChanged and bool(value):
            self._selected(self)
        return super().itemChange(change, value)

    def mouseDoubleClickEvent(self, event):
        self._activated(self)
        event.accept()

    def set_rect(self, rect: QRectF):
        rect = QRectF(rect)
        width = max(self.MIN_WIDTH, rect.width())
        height = max(self.MIN_HEIGHT, rect.height())
        self.prepareGeometryChange()
        self.setPos(rect.topLeft())
        self._rect = QRectF(0, 0, width, height)
        self.update()

    def scene_rect(self) -> QRectF:
        return self.mapRectToScene(self._rect)

    def set_title(self, title: str):
        self.title = str(title or "Graph").strip() or "Graph"
        self._update_tooltip()
        self.update()

    def set_color(self, color: str):
        self.color = self._normalized_color(color)
        self._update_tooltip()
        self.update()

    @staticmethod
    def _normalized_color(color: str) -> str:
        raw = str(color or "").strip()
        if not raw:
            return "#2f8f62"
        if not raw.startswith("#"):
            raw = f"#{raw}"
        parsed = QColor(raw)
        return parsed.name() if parsed.isValid() else "#2f8f62"

    def _update_tooltip(self):
        self.setToolTip("Graph frame. Double-click to rename or change color in the inspector.")


class _GraphNode(QGraphicsItem):
    WIDTH = 270
    HEIGHT = 126
    COLLAPSED_WIDTH = 188
    COLLAPSED_HEIGHT = 102
    COLLAPSED_CHILD_BOX_HEIGHT = 16
    PAINT_PAD = 72

    def __init__(
        self,
        node_id: int,
        token: CanvasToken,
        *,
        moved,
        selected,
        activated,
        menu_requested,
        file_open_requested,
        run_requested,
        output_drag_started,
        output_drag_moved,
        output_drag_finished,
        input_drag_started,
        input_drag_moved,
        input_drag_finished,
        parent=None,
    ):
        super().__init__(parent)
        self.node_id = node_id
        self.token = token
        self._moved = moved
        self._selected = selected
        self._activated = activated
        self._menu_requested = menu_requested
        self._file_open_requested = file_open_requested
        self._run_requested = run_requested
        self._output_drag_started = output_drag_started
        self._output_drag_moved = output_drag_moved
        self._output_drag_finished = output_drag_finished
        self._input_drag_started = input_drag_started
        self._input_drag_moved = input_drag_moved
        self._input_drag_finished = input_drag_finished
        self._connecting_port_direction = ""
        self._connecting_port_key = ""
        self.status = "idle"
        self._status_note = ""
        self._spin_angle = 0
        self.agent_id = ""
        self.agent_name = ""
        self.is_root_goal = False
        self.is_unscoped = False
        self._collapsed = False
        self._collapsed_goal_children: list[str] = []
        self._collapsed_readability_scale = 1.0
        self._collapsed_frame_color = ""
        self._can_run = False
        self.setFlags(
            self.GraphicsItemFlag.ItemIsMovable
            | self.GraphicsItemFlag.ItemIsSelectable
            | self.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setZValue(10)
        self._update_tooltip()

    def boundingRect(self):
        return self._body_rect().adjusted(-self.PAINT_PAD, -self.PAINT_PAD, self.PAINT_PAD, self.PAINT_PAD)

    def _node_size(self) -> tuple[float, float]:
        if self.token.kind == "goal" and self._collapsed:
            scale = self._collapsed_readability_scale
            return self.COLLAPSED_WIDTH * scale, self.COLLAPSED_HEIGHT * scale
        return self.WIDTH, self.HEIGHT

    def _node_width(self) -> float:
        return self._node_size()[0]

    def _node_height(self) -> float:
        return self._node_size()[1]

    def shape(self):
        path = QPainterPath()
        path.addRoundedRect(self._body_rect(), 10, 10)
        if self.token.kind == "goal" and self._collapsed:
            return path
        for ports, left in ((input_ports(self.token.kind), True), (output_ports(self.token.kind), False)):
            for port in ports:
                path.addEllipse(self._port_rect(self._port_center(ports, port.key, left=left)))
        return path

    def paint(self, painter: QPainter, option, widget=None):
        p = palette()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._body_rect()
        bg, border, accent = self._colors(p)
        collapsed_goal = self.token.kind == "goal" and self._collapsed
        if self.isSelected():
            border = QColor("#8ab4ff")
        painter.setPen(QPen(border, 1.4))
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 10, 10)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawRoundedRect(QRectF(0, 0, 5, rect.height()), 3, 3)
        show_port_labels = self.isSelected() or bool(option.state & QStyle.StateFlag.State_MouseOver)
        if not collapsed_goal:
            self._paint_ports(painter, p, accent, show_labels=show_port_labels)
            self._paint_status(painter, p)
            self._paint_kind_badge(painter, p)
        else:
            self._paint_collapsed_header(painter, p)

        painter.setPen(QColor(p["TEXT"]))
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(max(1, int(10 * (self._collapsed_readability_scale if collapsed_goal else 1.0))))
        painter.setFont(title_font)
        self._paint_title(painter, title_font)

        if not collapsed_goal:
            detail_font = QFont()
            detail_font.setPointSize(8)
            if self._single_scope_ref() is not None:
                painter.setPen(QColor(p["LINK"]))
                detail_font.setUnderline(True)
            else:
                painter.setPen(QColor(p["TEXT_DIM"]))
            painter.setFont(detail_font)
            self._paint_detail(painter, detail_font)

        if not collapsed_goal:
            self._paint_run_button(painter, p, accent)
            self._paint_agent_assignment(painter, p)
        if collapsed_goal:
            self._paint_collapsed_badge(painter, p)
            self._paint_collapsed_status(painter, p)

    def _paint_collapsed_badge(self, painter: QPainter, p: dict):
        if self.token.kind != "goal" or not self._collapsed:
            return
        scale = self._collapsed_readability_scale
        rect = QRectF(self._node_width() - 34 * scale, self._node_height() - 30 * scale, 24 * scale, 20 * scale)
        painter.setPen(QPen(QColor(p["BORDER"]), 1.0))
        painter.setBrush(QColor(p["BG3"]))
        painter.drawRoundedRect(rect, 5 * scale, 5 * scale)
        font = mono_font(max(1, int(9 * scale)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(p["TEXT_DIM"]))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "▸")

    def _paint_collapsed_status(self, painter: QPainter, p: dict):
        if self.token.kind != "goal" or not self._collapsed or self.status == "idle":
            return
        scale = self._collapsed_readability_scale
        rect = QRectF(self._node_width() - 34 * scale, 8 * scale, 24 * scale, 20 * scale)
        colors = {
            "queued": ("#1e2430", p["TEXT_DIM"], p["BORDER"]),
            "thinking": ("#102b31", "#67e8f9", "#2b697c"),
            "planned": ("#112a20", "#86efac", "#2f6f4d"),
            "running": ("#102b31", "#67e8f9", "#2b697c"),
            "paused": ("#282415", "#facc15", "#806c27"),
            "changed": ("#32260f", "#fbbf24", "#5a4319"),
            "review": ("#211b2c", "#c4b5fd", "#4e3b79"),
            "done": (p["SUCCESS_BG"], p["SUCCESS"], p["SUCCESS_BORDER"]),
            "blocked": ("#35191d", "#f87171", "#5f252d"),
        }
        bg, fg, border = colors.get(self.status, (p["BG3"], p["TEXT_DIM"], p["BORDER"]))
        painter.setPen(QPen(QColor(border), 1.0))
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(rect, 6 * scale, 6 * scale)
        if self.status in {"running", "thinking"}:
            arc_rect = QRectF(rect.left() + 6 * scale, rect.top() + 4 * scale, 12 * scale, 12 * scale)
            painter.setPen(QPen(QColor(fg), max(1.2, 1.8 * scale), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(arc_rect, self._spin_angle * 16, 250 * 16)
            return
        if self.status == "done":
            painter.setPen(QPen(QColor(fg), max(1.2, 1.8 * scale), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(QPointF(rect.left() + 6 * scale, rect.center().y()), QPointF(rect.left() + 11 * scale, rect.bottom() - 6 * scale))
            painter.drawLine(QPointF(rect.left() + 11 * scale, rect.bottom() - 6 * scale), QPointF(rect.left() + 19 * scale, rect.top() + 6 * scale))
            return
        label = {
            "queued": "N",
            "planned": "P",
            "paused": "P",
            "changed": "C",
            "review": "R",
            "blocked": "!",
        }.get(self.status, self.status[:1].upper())
        font = mono_font(max(1, int(8 * scale)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(fg))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

    def _paint_collapsed_header(self, painter: QPainter, p: dict):
        if self.token.kind != "goal" or not self._collapsed:
            return
        scale = self._collapsed_readability_scale
        header = QRectF(10 * scale, 8 * scale, self._node_width() - 20 * scale, 18 * scale)
        painter.setPen(QPen(QColor(p["BORDER"]), 1.0))
        painter.setBrush(QColor(p["BG3"]))
        painter.drawRoundedRect(header, 7 * scale, 7 * scale)
        painter.setFont(mono_font(max(1, int(7 * scale))))
        painter.setPen(QColor(p["TEXT_DIM"]))
        painter.drawText(header, Qt.AlignmentFlag.AlignCenter, "Goal summary")
        if not self._collapsed_goal_children:
            return
        painter.setPen(QPen(QColor(p["BORDER"]), 1.0))
        painter.setBrush(QColor(p["BG"]))
        font = mono_font(max(1, int(6 * scale)))
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        y = 30.0 * scale
        painter.drawText(QRectF(12 * scale, y, 68 * scale, 12 * scale), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "Subgoals")
        y += 14.0 * scale
        box_width = self._node_width() - 24 * scale
        child_box_height = self.COLLAPSED_CHILD_BOX_HEIGHT * scale
        for title in self._collapsed_goal_children[:2]:
            label = self._elided_ascii(metrics, title, box_width - 10 * scale)
            rect = QRectF(12 * scale, y, box_width, child_box_height)
            painter.drawRoundedRect(rect, 5 * scale, 5 * scale)
            painter.drawText(rect.adjusted(5 * scale, 0, -5 * scale, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            y += child_box_height + 2 * scale
        remaining = len(self._collapsed_goal_children) - 2
        if remaining > 0:
            rect = QRectF(12 * scale, y, min(72 * scale, box_width), child_box_height)
            painter.drawRoundedRect(rect, 5 * scale, 5 * scale)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"+{remaining} more")

    def set_collapsed_goal_children(self, goal_titles: list[str] | tuple[str, ...]):
        titles = [str(title or "").strip() for title in goal_titles]
        titles = [title for title in titles if title]
        if titles == self._collapsed_goal_children:
            return
        self._collapsed_goal_children = titles
        self.update()

    def itemChange(self, change, value):
        if change == self.GraphicsItemChange.ItemPositionHasChanged:
            self._moved(self)
        elif change == self.GraphicsItemChange.ItemSelectedHasChanged and bool(value):
            self._selected(self)
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        action = self._control_action_at(event.pos()) if event.button() == Qt.MouseButton.LeftButton else None
        if action is not None:
            if not self._selected(self):
                event.accept()
                return
            self._run_requested(self, action)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._file_link_at(event.pos()):
            if not self._selected(self):
                event.accept()
                return
            self._file_open_requested(self)
            event.accept()
            return
        input_key = self._input_port_key_at(event.pos()) if event.button() == Qt.MouseButton.LeftButton else None
        if input_key is not None:
            self._connecting_port_direction = "input"
            self._connecting_port_key = input_key
            if not self._selected(self):
                self._connecting_port_direction = ""
                self._connecting_port_key = ""
                event.accept()
                return
            self._input_drag_started(self, event.scenePos(), input_key)
            event.accept()
            return
        output_key = self._output_port_key_at(event.pos()) if event.button() == Qt.MouseButton.LeftButton else None
        if output_key is not None:
            self._connecting_port_direction = "output"
            self._connecting_port_key = output_key
            if not self._selected(self):
                self._connecting_port_direction = ""
                self._connecting_port_key = ""
                event.accept()
                return
            self._output_drag_started(self, event.scenePos(), output_key)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        if not self._selected(self):
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._connecting_port_direction == "output":
            self._output_drag_moved(self, event.scenePos(), self._connecting_port_key)
            event.accept()
            return
        if self._connecting_port_direction == "input":
            self._input_drag_moved(self, event.scenePos(), self._connecting_port_key)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._connecting_port_direction == "output":
            port_key = self._connecting_port_key
            self._connecting_port_direction = ""
            self._connecting_port_key = ""
            self._output_drag_finished(self, event.scenePos(), port_key)
            event.accept()
            return
        if self._connecting_port_direction == "input":
            port_key = self._connecting_port_key
            self._connecting_port_direction = ""
            self._connecting_port_key = ""
            self._input_drag_finished(self, event.scenePos(), port_key)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._update_cursor(event.pos())
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self._activated(self)
        event.accept()

    def contextMenuEvent(self, event):
        self._menu_requested(self, event.screenPos())
        event.accept()

    def hoverMoveEvent(self, event):
        self._update_cursor(event.pos())
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event):
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().hoverLeaveEvent(event)

    def center_scene_pos(self) -> QPointF:
        return self.mapToScene(self.boundingRect().center())

    def input_scene_pos(self) -> QPointF:
        return self.input_port_scene_pos()

    def output_scene_pos(self) -> QPointF:
        return self.output_port_scene_pos()

    def input_port_scene_pos(self, key: str = "in") -> QPointF:
        return self.mapToScene(self._port_center(input_ports(self.token.kind), key, left=True))

    def output_port_scene_pos(self, key: str = "out") -> QPointF:
        return self.mapToScene(self._port_center(output_ports(self.token.kind), key, left=False))

    def set_token(self, token: CanvasToken):
        self.token = token
        self._update_tooltip()
        self.update()

    def set_agent(self, agent_id: str, agent_name: str = ""):
        self.agent_id = str(agent_id or "").strip()
        self.agent_name = str(agent_name or "").strip()
        self._update_tooltip()
        self.update()

    def set_root_goal(self, is_root_goal: bool):
        is_root_goal = bool(is_root_goal)
        if self.is_root_goal == is_root_goal:
            return
        self.is_root_goal = is_root_goal
        self.update()

    def set_unscoped(self, is_unscoped: bool):
        is_unscoped = bool(is_unscoped)
        if self.is_unscoped == is_unscoped:
            return
        self.is_unscoped = is_unscoped
        self._update_tooltip()
        self.update()

    def set_collapsed(self, collapsed: bool):
        collapsed = bool(collapsed)
        if self._collapsed == collapsed:
            return
        self.prepareGeometryChange()
        self._collapsed = collapsed
        self._update_tooltip()
        self.update()

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed_readability_scale(self, scale: float):
        scale = max(1.0, min(4.0, float(scale or 1.0)))
        if abs(scale - self._collapsed_readability_scale) < 0.01:
            return
        self.prepareGeometryChange()
        self._collapsed_readability_scale = scale
        self.update()

    def set_collapsed_frame_color(self, color: str):
        raw = str(color or "").strip()
        parsed = QColor(raw)
        normalized = parsed.name() if parsed.isValid() else ""
        if normalized == self._collapsed_frame_color:
            return
        self._collapsed_frame_color = normalized
        self.update()

    def set_runnable(self, can_run: bool):
        can_run = bool(can_run)
        if self._can_run == can_run:
            return
        self._can_run = can_run
        self._update_tooltip()
        self.update()

    def set_status(self, status: str, note: str = ""):
        status = status if status in NODE_STATUSES else "idle"
        if self.status == status and self._status_note == note:
            return
        self.status = status
        self._status_note = note
        self._update_tooltip()
        self.update()

    def advance_status_animation(self):
        if self.status not in {"running", "thinking"}:
            return
        self._spin_angle = (self._spin_angle + 38) % 360
        self.update(self._status_rect().adjusted(-4, -4, 4, 4))

    def _input_port_rect(self) -> QRectF:
        return self._port_rect(self._port_center(input_ports(self.token.kind), "in", left=True))

    def _output_port_rect(self) -> QRectF:
        return self._port_rect(self._port_center(output_ports(self.token.kind), "out", left=False))

    def _output_port_key_at(self, pos: QPointF) -> str | None:
        if self.token.kind == "goal" and self._collapsed:
            return None
        for port in output_ports(self.token.kind):
            if self._port_rect(self._port_center(output_ports(self.token.kind), port.key, left=False)).contains(pos):
                return port.key
        return None

    def _input_port_key_at(self, pos: QPointF) -> str | None:
        if self.token.kind == "goal" and self._collapsed:
            return None
        for port in input_ports(self.token.kind):
            if self._port_rect(self._port_center(input_ports(self.token.kind), port.key, left=True)).contains(pos):
                return port.key
        return None

    def _file_link_at(self, pos: QPointF) -> bool:
        return self._single_scope_ref() is not None and self._detail_rect().contains(pos)

    def _single_scope_ref(self) -> str | None:
        if self.token.kind != "scope":
            return None
        refs = [
            part.strip().lstrip("@").strip('"')
            for part in self.token.detail.replace(",", "\n").splitlines()
            if part.strip()
        ]
        return refs[0] if len(refs) == 1 else None

    def _paint_title(self, painter: QPainter, font: QFont):
        title_rect = self._title_rect()
        metrics = QFontMetrics(font)
        line_height = metrics.lineSpacing()
        for idx, line in enumerate(self._wrapped_elided_lines(self.token.title, font, title_rect.width(), 2)):
            line_rect = QRectF(
                title_rect.left(),
                title_rect.top() + idx * line_height,
                title_rect.width(),
                line_height,
            )
            painter.drawText(line_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)

    def _paint_detail(self, painter: QPainter, font: QFont):
        detail_rect = self._detail_rect()
        metrics = QFontMetrics(font)
        line_height = metrics.lineSpacing()
        max_lines = max(1, int(detail_rect.height() // line_height))
        text = self.token.detail or component_spec(self.token.kind).detail or self.token.kind
        for idx, line in enumerate(self._wrapped_elided_lines(text, font, detail_rect.width(), max_lines)):
            line_rect = QRectF(
                detail_rect.left(),
                detail_rect.top() + idx * line_height,
                detail_rect.width(),
                line_height,
            )
            painter.drawText(line_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)

    def _wrapped_elided_lines(self, text: str, font: QFont, width: float, max_lines: int = 2) -> list[str]:
        metrics = QFontMetrics(font)
        words = str(text or "").split()
        if not words or max_lines <= 0 or width <= 0:
            return []
        lines: list[str] = []
        idx = 0
        while idx < len(words) and len(lines) < max_lines:
            if len(lines) == max_lines - 1:
                lines.append(self._elided_ascii(metrics, " ".join(words[idx:]), width))
                break
            current = ""
            while idx < len(words):
                candidate = words[idx] if not current else f"{current} {words[idx]}"
                if metrics.horizontalAdvance(candidate) <= width:
                    current = candidate
                    idx += 1
                    continue
                break
            if current:
                lines.append(current)
                continue
            lines.append(self._elided_ascii(metrics, words[idx], width))
            idx += 1
        return lines

    @staticmethod
    def _elided_ascii(metrics: QFontMetrics, text: str, width: float) -> str:
        text = str(text or "")
        if metrics.horizontalAdvance(text) <= width:
            return text
        suffix = "..."
        if metrics.horizontalAdvance(suffix) > width:
            return ""
        low = 0
        high = len(text)
        while low < high:
            mid = (low + high + 1) // 2
            if metrics.horizontalAdvance(text[:mid].rstrip() + suffix) <= width:
                low = mid
            else:
                high = mid - 1
        return text[:low].rstrip() + suffix

    def _title_rect(self) -> QRectF:
        if self.token.kind == "goal" and self._collapsed:
            scale = self._collapsed_readability_scale
            if self._collapsed_goal_children:
                return QRectF(12 * scale, 52 * scale, self._node_width() - 24 * scale, self._node_height() - 64 * scale)
            return QRectF(12 * scale, 30 * scale, self._node_width() - 24 * scale, self._node_height() - 44 * scale)
        right_margin = 48 if self.status != "idle" else 82
        return QRectF(18, 27, self._node_width() - 18 - right_margin, 36)

    def _detail_rect(self) -> QRectF:
        top = 66
        width = self._node_width() - 36
        if self._is_runnable():
            return QRectF(18, top, width, self._node_height() - top - 34)
        return QRectF(18, top, width, self._node_height() - top - 20)

    def _status_rect(self) -> QRectF:
        return QRectF(self._node_width() - 38, 8, 26, 22)

    def _run_button_rect(self) -> QRectF:
        return QRectF(18, self._node_height() - 27, 52, 20)

    def _run_button_at(self, pos: QPointF) -> bool:
        return self._control_action_at(pos) is not None

    def _control_action_at(self, pos: QPointF) -> str | None:
        if not self._is_runnable():
            return None
        for action, rect in self._control_rects().items():
            if rect.contains(pos):
                return action
        return None

    def _control_rects(self) -> dict[str, QRectF]:
        rect = self._run_button_rect()
        if self.status == "running":
            return {
                "pause": QRectF(rect.left(), rect.top(), 24, rect.height()),
                "stop": QRectF(rect.left() + 28, rect.top(), 24, rect.height()),
            }
        if self.status == "paused":
            return {
                "run": QRectF(rect.left(), rect.top(), 24, rect.height()),
                "stop": QRectF(rect.left() + 28, rect.top(), 24, rect.height()),
            }
        return {"run": QRectF(rect.left(), rect.top(), 26, rect.height())}

    def _is_runnable(self) -> bool:
        return self._can_run

    def _update_cursor(self, pos: QPointF):
        if self._run_button_at(pos):
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            return
        if self._file_link_at(pos):
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            return
        if self._output_port_key_at(pos) is not None:
            self.setCursor(Qt.CursorShape.CrossCursor)
            return
        if self._input_port_key_at(pos) is not None:
            self.setCursor(Qt.CursorShape.CrossCursor)
            return
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def _update_tooltip(self):
        status = "" if self.status == "idle" else f" Status: {self.status}."
        collapsed = " Collapsed: subtree hidden." if self._collapsed else ""
        owner = f" Crew: {self.agent_name}." if self.agent_name else ""
        scope = " Unscoped: connect this to a goal before agents can use it." if self.is_unscoped else ""
        if self._single_scope_ref() is not None:
            self.setToolTip(
                f"Click the path to open it. Double-click to edit in inspector. Drag any port to connect.{status}{owner}{scope}{collapsed}"
            )
            return
        if self._is_runnable():
            self.setToolTip(
                f"Use the node controls to run, pause, or stop. Drag to move. Double-click to edit in inspector. Drag any port to connect.{status}{owner}{scope}{collapsed}"
            )
            return
        self.setToolTip(
            f"Drag to move. Double-click to edit in inspector. Drag any port to connect.{status}{owner}{scope}{collapsed}"
        )

    def _paint_ports(self, painter: QPainter, p: dict, accent: QColor, *, show_labels: bool):
        for ports, left in ((input_ports(self.token.kind), True), (output_ports(self.token.kind), False)):
            for port in ports:
                center = self._port_center(ports, port.key, left=left)
                rect = self._port_rect(center)
                self._paint_port(painter, p, accent, rect, port.label, left=left, show_label=show_labels)

    def _paint_port(
        self,
        painter: QPainter,
        p: dict,
        accent: QColor,
        rect: QRectF,
        label: str,
        *,
        left: bool,
        show_label: bool,
    ):
            painter.setPen(QPen(QColor(p["BORDER"]), 1.0))
            painter.setBrush(QColor(p["BG"]))
            painter.drawEllipse(rect)
            painter.setPen(accent)
            painter.setBrush(accent)
            painter.drawEllipse(rect.adjusted(4, 4, -4, -4))
            if not show_label:
                return
            painter.setPen(QColor(p["TEXT_DIM"]))
            font = mono_font(6)
            font.setBold(True)
            painter.setFont(font)
            metrics = QFontMetrics(font)
            text_width = min(58, max(24, metrics.horizontalAdvance(label) + 8))
            text_rect = QRectF(
                rect.left() - text_width - 4 if left else rect.right() + 4,
                rect.top() + 1,
                text_width,
                10,
            )
            align = Qt.AlignmentFlag.AlignRight if left else Qt.AlignmentFlag.AlignLeft
            painter.drawText(text_rect, align, label)

    def _paint_kind_badge(self, painter: QPainter, p: dict):
        label = component_spec(self.token.kind).title
        if self.token.kind == "goal" and self._collapsed:
            label = f"{label} (collapsed)"
        font = mono_font(7)
        font.setBold(True)
        metrics = QFontMetrics(font)
        width = min(86, max(42, metrics.horizontalAdvance(label) + 14))
        left = 18 if self.status != "idle" else self._node_width() - width - 12
        rect = QRectF(left, 9, width, 18)
        painter.setPen(QPen(QColor(p["BORDER"]), 1.0))
        painter.setBrush(QColor(p["BG3"]))
        painter.drawRoundedRect(rect, 6, 6)
        painter.setFont(font)
        painter.setPen(QColor(p["TEXT_DIM"]))
        painter.drawText(rect.adjusted(6, 1, -6, -1), Qt.AlignmentFlag.AlignCenter, label)

    def _paint_status(self, painter: QPainter, p: dict):
        if self.status == "idle":
            return
        rect = self._status_rect()
        colors = {
            "queued": ("#1e2430", p["TEXT_DIM"], p["BORDER"]),
            "thinking": ("#102b31", "#67e8f9", "#2b697c"),
            "planned": ("#112a20", "#86efac", "#2f6f4d"),
            "running": ("#102b31", "#67e8f9", "#2b697c"),
            "paused": ("#282415", "#facc15", "#806c27"),
            "changed": ("#32260f", "#fbbf24", "#5a4319"),
            "review": ("#211b2c", "#c4b5fd", "#4e3b79"),
            "done": (p["SUCCESS_BG"], p["SUCCESS"], p["SUCCESS_BORDER"]),
            "blocked": ("#35191d", "#f87171", "#5f252d"),
        }
        bg, fg, border = colors.get(self.status, (p["BG3"], p["TEXT_DIM"], p["BORDER"]))
        painter.setPen(QPen(QColor(border), 1.0))
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(rect, 6, 6)
        if self.status in {"running", "thinking"}:
            arc_rect = QRectF(rect.left() + 7, rect.top() + 5, 12, 12)
            painter.setPen(QPen(QColor(fg), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(arc_rect, self._spin_angle * 16, 250 * 16)
            return
        elif self.status == "done":
            painter.setPen(QPen(QColor(fg), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(QPointF(rect.left() + 7, rect.center().y()), QPointF(rect.left() + 12, rect.bottom() - 7))
            painter.drawLine(QPointF(rect.left() + 12, rect.bottom() - 7), QPointF(rect.left() + 20, rect.top() + 7))
            return
        else:
            label = {
                "queued": "NEXT",
                "thinking": "THINK",
                "planned": "PLAN",
                "paused": "PAUSE",
                "changed": "CHG",
                "review": "REV",
                "blocked": "!",
            }.get(self.status, self.status.upper()[:4])
            text_rect = rect.adjusted(3, 2, -3, -2)
            align = Qt.AlignmentFlag.AlignCenter
        font = mono_font(7)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(fg))
        painter.drawText(text_rect, align, label[:3])

    def _paint_run_button(self, painter: QPainter, p: dict, accent: QColor):
        if not self._is_runnable():
            return
        for action, rect in self._control_rects().items():
            if action == "stop":
                fg = "#f87171"
                border = "#5f252d"
                bg = "#35191d"
            elif action == "pause":
                fg = "#facc15"
                border = "#806c27"
                bg = "#282415"
            else:
                fg = "#67e8f9" if self.status == "paused" else accent.name()
                border = "#2b697c" if self.status == "paused" else accent.name()
                bg = "#102b31" if self.status == "paused" else p["BG3"]
            painter.setPen(QPen(QColor(border), 1.0))
            painter.setBrush(QColor(bg))
            painter.drawRoundedRect(rect, 5, 5)
            painter.setPen(QPen(QColor(fg), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(QColor(fg))
            self._paint_control_icon(painter, rect, action)

    def _paint_control_icon(self, painter: QPainter, rect: QRectF, action: str):
        c = rect.center()
        if action == "run":
            painter.drawPolygon(
                QPolygonF(
                    [
                        QPointF(c.x() - 4, c.y() - 6),
                        QPointF(c.x() - 4, c.y() + 6),
                        QPointF(c.x() + 6, c.y()),
                    ]
                )
            )
            return
        if action == "pause":
            painter.drawRect(QRectF(c.x() - 5, c.y() - 6, 3, 12))
            painter.drawRect(QRectF(c.x() + 2, c.y() - 6, 3, 12))
            return
        painter.drawRect(QRectF(c.x() - 5, c.y() - 5, 10, 10))

    def _paint_agent_assignment(self, painter: QPainter, p: dict):
        if self.token.kind != "operation" or not self.agent_name:
            return
        rect = QRectF(
            self._run_button_rect().right() + 8,
            self._node_height() - 27,
            max(0.0, self._node_width() - 100),
            18,
        )
        font = mono_font(7)
        font.setBold(True)
        metrics = QFontMetrics(font)
        text = metrics.elidedText(f"@{self.agent_name}", Qt.TextElideMode.ElideRight, int(rect.width()))
        painter.setFont(font)
        painter.setPen(QColor(p["TEXT_DIM"]))
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

    def _port_center(self, ports: tuple[PortSpec, ...], key: str, *, left: bool) -> QPointF:
        keys = [port.key for port in ports]
        idx = keys.index(key) if key in keys else 0
        if len(ports) <= 1:
            y = self._node_height() / 2
        else:
            top = 38.0
            bottom = self._node_height() - 20.0
            step = (bottom - top) / max(1, len(ports) - 1)
            y = top + step * idx
        return QPointF(0 if left else self._node_width(), y)

    @staticmethod
    def _port_rect(center: QPointF) -> QRectF:
        return QRectF(center.x() - 7, center.y() - 7, 14, 14)

    def _body_rect(self) -> QRectF:
        width, height = self._node_size()
        return QRectF(0, 0, width, height)

    def _colors(self, p: dict) -> tuple[QColor, QColor, QColor]:
        if self.token.kind == "goal" and self._collapsed and self._collapsed_frame_color:
            accent = QColor(self._collapsed_frame_color)
            bg = QColor(accent)
            bg = bg.darker(430)
            border = QColor(accent)
            border = border.lighter(115)
            return bg, border, QColor(accent.lighter(135))
        if self.token.kind == "goal" and (self.is_root_goal or self._collapsed):
            return QColor("#10291f"), QColor("#2fa66f"), QColor("#64d6a2")
        colors = {
            "goal": ("#181f2c", "#32425c", "#8ab4ff"),
            "operation": ("#132832", "#2b697c", "#67e8f9"),
            "context": ("#241f13", "#6d5420", "#fbbf24"),
            "decision": ("#251728", "#6a315f", "#f472b6"),
            "scope": ("#1f2527", "#59676d", "#b4c8d0"),
            "evidence": ("#211b2c", "#4e3b79", "#c4b5fd"),
            "dod": ("#10291f", "#2f8f62", "#34d399"),
        }
        bg, border, accent = colors[self.token.kind] if self.token.kind in colors else (p["BG3"], p["BORDER"], p["LINK"])
        if self.is_unscoped:
            return QColor("#15171c"), QColor("#3a3f4a"), QColor("#8a91a3")
        return QColor(bg), QColor(border), QColor(accent)


class _GraphEdge(QGraphicsPathItem):
    PAINT_PAD = 14

    def __init__(self, source_id: int, target_id: int, kind: str, menu_requested, parent=None):
        super().__init__(parent)
        self.source_id = source_id
        self.target_id = target_id
        self.kind = kind
        self._menu_requested = menu_requested
        self.setFlag(QGraphicsPathItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(0)

    def boundingRect(self):
        return super().boundingRect().adjusted(-self.PAINT_PAD, -self.PAINT_PAD, self.PAINT_PAD, self.PAINT_PAD)

    def contextMenuEvent(self, event):
        self._menu_requested(self, event.screenPos())
        event.accept()

    def paint(self, painter: QPainter, option, widget=None):
        super().paint(painter, option, widget)
        path = self.path()
        if path.isEmpty():
            return
        p = palette()
        color = QColor(self._edge_color(p))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        end = path.pointAtPercent(1.0)
        angle = math.radians(-path.angleAtPercent(1.0))
        size = 7.0
        back = QPointF(math.cos(angle) * size, math.sin(angle) * size)
        side = QPointF(math.cos(angle + math.pi / 2) * size * 0.55, math.sin(angle + math.pi / 2) * size * 0.55)
        painter.drawPolygon(QPolygonF([end, end - back + side, end - back - side]))

    def _edge_color(self, p: dict) -> str:
        return edge_color(self.kind, p)
