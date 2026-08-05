from pathlib import Path

from main import (
    APP_NAME,
    _configure_application,
    _configure_macos_process_identity,
    _parse_args,
    _print_performance_summary,
    _set_macos_bundle_and_process_name,
    _set_macos_bundle_and_process_name_ctypes,
    _set_macos_process_name_cps,
)
from services.performance import PerformanceOperationSummary
from ui.main_window import _startup_workspace


def test_plain_launch_uses_current_directory_even_with_saved_workspace(tmp_path):
    launch = tmp_path / "launch"
    saved = tmp_path / "saved"
    launch.mkdir()
    saved.mkdir()

    workspace = _startup_workspace(
        {"workspace_path": str(saved)},
        launch_cwd=str(launch),
    )

    assert Path(workspace) == launch.resolve()


def test_last_workspace_opt_in_uses_saved_workspace(tmp_path):
    launch = tmp_path / "launch"
    saved = tmp_path / "saved"
    launch.mkdir()
    saved.mkdir()

    workspace = _startup_workspace(
        {"workspace_path": str(saved)},
        prefer_saved_workspace=True,
        launch_cwd=str(launch),
    )

    assert Path(workspace) == saved.resolve()


def test_explicit_workspace_wins_over_saved_workspace(tmp_path):
    explicit = tmp_path / "explicit"
    saved = tmp_path / "saved"
    explicit.mkdir()
    saved.mkdir()

    workspace = _startup_workspace(
        {"workspace_path": str(saved)},
        startup_workspace=str(explicit),
        prefer_saved_workspace=True,
    )

    assert Path(workspace) == explicit.resolve()


def test_parse_workspace_argument():
    workspace, last_workspace, performance_summary, summary_limit, qt_args = _parse_args(
        ["C:\\repo", "--platform", "windows"]
    )

    assert workspace == "C:\\repo"
    assert last_workspace is False
    assert performance_summary is False
    assert summary_limit == 10
    assert qt_args == ["--platform", "windows"]


def test_parse_workspace_option_and_last_workspace():
    workspace, last_workspace, performance_summary, summary_limit, qt_args = _parse_args(
        ["--workspace", "C:\\repo", "--last-workspace"],
    )

    assert workspace == "C:\\repo"
    assert last_workspace is True
    assert performance_summary is False
    assert summary_limit == 10
    assert qt_args == []


def test_parse_app_value_options_accept_equals_form():
    workspace, last_workspace, performance_summary, summary_limit, qt_args = _parse_args(
        [
            "--workspace=C:\\repo",
            "--performance-summary",
            "--performance-summary-limit=4",
            "--platform=offscreen",
        ],
    )

    assert workspace == "C:\\repo"
    assert last_workspace is False
    assert performance_summary is True
    assert summary_limit == 4
    assert qt_args == ["--platform=offscreen"]


def test_parse_performance_summary_args():
    workspace, last_workspace, performance_summary, summary_limit, qt_args = _parse_args(
        ["--performance-summary", "--performance-summary-limit", "3", "--platform", "offscreen"],
    )

    assert workspace is None
    assert last_workspace is False
    assert performance_summary is True
    assert summary_limit == 3
    assert qt_args == ["--platform", "offscreen"]


def test_print_performance_summary_outputs_ranked_rows(monkeypatch, capsys):
    monkeypatch.setattr(
        "main.slowest_logged_operations",
        lambda *, limit: [
            PerformanceOperationSummary(
                operation="git.apply",
                count=2,
                total_ms=130,
                max_ms=90,
                avg_ms=65,
                latest_detail="changes=9",
            )
        ],
    )

    _print_performance_summary(1)

    out = capsys.readouterr().out
    assert "Slow operations from performance.log" in out
    assert "git.apply\t2\t130.000\t90.000\t65.000\tchanges=9" in out


def test_print_performance_summary_handles_empty_log(monkeypatch, capsys):
    monkeypatch.setattr("main.slowest_logged_operations", lambda *, limit: [])

    _print_performance_summary(1)

    assert capsys.readouterr().out == "No slow performance events found.\n"


def test_main_performance_summary_exits_before_workspace_or_qt(monkeypatch, capsys):
    import main as main_module

    monkeypatch.setattr(
        main_module.sys,
        "argv",
        ["aichs", "--performance-summary", "missing-workspace"],
    )
    monkeypatch.setattr(main_module.multiprocessing, "freeze_support", lambda: None)
    monkeypatch.setattr(main_module, "slowest_logged_operations", lambda *, limit: [])
    monkeypatch.setattr(
        main_module,
        "_start_gui",
        lambda *_args: (_ for _ in ()).throw(AssertionError("gui should not start")),
    )

    main_module.main()

    assert capsys.readouterr().out == "No slow performance events found.\n"


def test_configure_application_sets_display_name(qapp):
    _configure_application(qapp)

    assert qapp.applicationName() == APP_NAME
    assert qapp.applicationDisplayName() == "Aichs"
    assert qapp.organizationName() == "aichs"
    assert qapp.organizationDomain() == "aichs.studio"
    assert qapp.desktopFileName() == "aichs"


def test_configure_macos_process_identity_is_noop_off_darwin(monkeypatch):
    monkeypatch.setattr("main.sys.platform", "linux")
    calls = []
    monkeypatch.setattr(
        "main._set_macos_bundle_and_process_name",
        lambda name: calls.append(("bundle", name)) or True,
    )
    monkeypatch.setattr(
        "main._set_macos_process_name_cps",
        lambda name: calls.append(("cps", name)) or True,
    )

    _configure_macos_process_identity("Aichs")

    assert calls == []


def test_configure_macos_process_identity_sets_bundle_and_cps(monkeypatch):
    monkeypatch.setattr("main.sys.platform", "darwin")
    calls = []
    monkeypatch.setattr(
        "main._set_macos_bundle_and_process_name",
        lambda name: calls.append(("bundle", name)) or True,
    )
    monkeypatch.setattr(
        "main._set_macos_process_name_cps",
        lambda name: calls.append(("cps", name)) or True,
    )

    _configure_macos_process_identity("Aichs")

    assert calls == [("bundle", "Aichs"), ("cps", "Aichs")]


def test_set_macos_bundle_and_process_name_updates_info_dictionary(monkeypatch):
    class FakeInfo(dict):
        def setObject_forKey_(self, value, key):
            self[key] = value

    class FakeBundle:
        def __init__(self, info):
            self._info = info

        def localizedInfoDictionary(self):
            return None

        def infoDictionary(self):
            return self._info

    class FakeProcessInfo:
        def __init__(self):
            self.name = None

        def setProcessName_(self, name):
            self.name = name

    info = FakeInfo()
    process_info = FakeProcessInfo()

    class FakeFoundation:
        class NSBundle:
            @staticmethod
            def mainBundle():
                return FakeBundle(info)

        class NSProcessInfo:
            @staticmethod
            def processInfo():
                return process_info

    import sys

    monkeypatch.setitem(sys.modules, "Foundation", FakeFoundation)

    assert _set_macos_bundle_and_process_name("Aichs") is True
    assert info["CFBundleName"] == "Aichs"
    assert info["CFBundleDisplayName"] == "Aichs"
    assert process_info.name == "Aichs"


def test_set_macos_bundle_falls_back_to_ctypes_without_pyobjc(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "Foundation":
            raise ImportError("no pyobjc")
        return real_import(name, *args, **kwargs)

    calls = []
    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(
        "main._set_macos_bundle_and_process_name_ctypes",
        lambda name: calls.append(name) or True,
    )

    assert _set_macos_bundle_and_process_name("Aichs") is True
    assert calls == ["Aichs"]


def test_set_macos_bundle_ctypes_returns_false_without_objc(monkeypatch):
    monkeypatch.setattr(
        "ctypes.util.find_library",
        lambda name: None if name == "objc" else f"/usr/lib/lib{name}.dylib",
    )
    assert _set_macos_bundle_and_process_name_ctypes("Aichs") is False


def test_set_macos_process_name_cps_returns_false_without_library(monkeypatch):
    monkeypatch.setattr("ctypes.util.find_library", lambda _name: None)
    assert _set_macos_process_name_cps("Aichs") is False
