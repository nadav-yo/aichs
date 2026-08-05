from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QSizePolicy,
)

from services.updates import UPGRADE_COMMAND, UpdateAvailability
from ui.theme import secondary_button_style, update_banner_style


class UpdateBanner(QFrame):
    """Non-modal strip announcing a newer PyPI release."""

    dismissed = pyqtSignal(str)
    remind_later = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("updateBanner")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._availability: UpdateAvailability | None = None

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 6, 8, 6)
        root.setSpacing(8)

        self._label = QLabel("")
        self._label.setObjectName("updateBannerLabel")
        self._label.setWordWrap(True)
        self._label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        root.addWidget(self._label, 1)

        self._copy_btn = QPushButton("Copy upgrade command")
        self._copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_btn.clicked.connect(self._copy_upgrade_command)
        root.addWidget(self._copy_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._later_btn = QPushButton("Later")
        self._later_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._later_btn.clicked.connect(self._on_later)
        root.addWidget(self._later_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._dismiss_btn = QPushButton("Dismiss")
        self._dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dismiss_btn.clicked.connect(self._on_dismiss)
        root.addWidget(self._dismiss_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self.hide()
        self.apply_appearance()

    def apply_appearance(self) -> None:
        self.setStyleSheet(update_banner_style())
        button_style = secondary_button_style()
        self._copy_btn.setStyleSheet(button_style)
        self._later_btn.setStyleSheet(button_style)
        self._dismiss_btn.setStyleSheet(button_style)

    def show_update(self, availability: UpdateAvailability) -> None:
        self._availability = availability
        self._label.setText(
            f"Update available: aichs {availability.latest} "
            f"(you have {availability.installed}). "
            f"Run `{availability.upgrade_command}` to upgrade."
        )
        self.show()

    def clear(self) -> None:
        self._availability = None
        self.hide()

    def _copy_upgrade_command(self) -> None:
        command = (
            self._availability.upgrade_command
            if self._availability is not None
            else UPGRADE_COMMAND
        )
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(command)

    def _on_later(self) -> None:
        self.hide()
        self.remind_later.emit()

    def _on_dismiss(self) -> None:
        version = self._availability.latest if self._availability is not None else ""
        self.hide()
        if version:
            self.dismissed.emit(version)
