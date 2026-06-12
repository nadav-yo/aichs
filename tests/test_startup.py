from pathlib import Path

from main import _parse_args, _print_performance_summary
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
