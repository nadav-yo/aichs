from __future__ import annotations

from services.updates import UpdateAvailability
from ui.main_window import MainWindow
from ui.widgets.update_banner import UpdateBanner


def test_update_banner_shows_and_copies_command(qapp):
    banner = UpdateBanner()
    clipboard = qapp.clipboard()
    original = clipboard.text()
    try:
        availability = UpdateAvailability(installed="0.5.1", latest="0.5.2")
        banner.show_update(availability)
        assert not banner.isHidden()
        assert "0.5.2" in banner._label.text()
        banner._copy_upgrade_command()
        assert clipboard.text() == "pipx upgrade aichs"
    finally:
        clipboard.setText(original)
        banner.deleteLater()


def test_update_banner_dismiss_emits_version(qapp):
    banner = UpdateBanner()
    seen = []
    banner.dismissed.connect(seen.append)
    banner.show_update(UpdateAvailability(installed="0.5.1", latest="0.5.2"))
    banner._on_dismiss()
    assert seen == ["0.5.2"]
    assert banner.isHidden()
    banner.deleteLater()


def test_main_window_update_check_shows_banner(qapp, workspace, monkeypatch):
    monkeypatch.setattr("services.updates.should_run_network_check", lambda **_kwargs: True)

    class _ThreadStub:
        def __init__(self, parent=None):
            self._done_slot = None
            self._finished_slots = []

        @property
        def done(self):
            class _Sig:
                def connect(_self, slot):
                    self._done_slot = slot

            return _Sig()

        @property
        def finished(self):
            class _Sig:
                def connect(_self, slot):
                    self._finished_slots.append(slot)

            return _Sig()

        def start(self):
            if self._done_slot is not None:
                self._done_slot(UpdateAvailability(installed="0.5.1", latest="9.9.9"))
            for slot in list(self._finished_slots):
                slot()

        def deleteLater(self):
            return None

    monkeypatch.setattr("ui.main_window._UpdateCheckThread", _ThreadStub)

    window = MainWindow(startup_workspace=str(workspace))
    try:
        window._maybe_check_for_updates()
        assert not window._update_banner.isHidden()
        assert "9.9.9" in window._update_banner._label.text()
        saved = window._settings.load()
        assert saved.get("update_last_checked", 0) > 0
        window._on_update_dismissed("9.9.9")
        assert window._update_banner.isHidden()
        assert window._settings.load().get("update_dismissed_version") == "9.9.9"
    finally:
        window.close()
        window.deleteLater()
