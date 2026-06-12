import json
import threading

from services.performance import (
    MAX_RECENT_EVENTS,
    PerformanceOperationSummary,
    PerformanceRecorder,
    slowest_logged_operations,
    time_operation,
)


def test_time_operation_records_slow_event(monkeypatch, tmp_path):
    import services.performance as performance

    local = PerformanceRecorder(tmp_path / "performance.log")
    monkeypatch.setattr(performance, "recorder", local)

    with time_operation("unit.slow", detail="case=1", slow_ms=0):
        pass

    events = local.recent()
    assert len(events) == 1
    assert events[0].operation == "unit.slow"
    assert events[0].detail == "case=1"
    assert events[0].elapsed_ms >= 0
    local.flush()
    assert "unit.slow" in (tmp_path / "performance.log").read_text(encoding="utf-8")


def test_event_loop_stall_monitor_records_delayed_tick(qapp, monkeypatch, tmp_path):
    import ui.performance_monitor as performance_monitor

    local = PerformanceRecorder(tmp_path / "performance.log")
    monkeypatch.setattr(performance_monitor, "recorder", local)
    times = iter([0.0, 0.2])
    monitor = performance_monitor.EventLoopStallMonitor(
        interval_ms=50,
        stall_ms=100,
        clock=lambda: next(times),
    )
    monitor._timer.stop()

    monitor._tick()

    events = local.recent()
    assert len(events) == 1
    assert events[0].operation == "event_loop.stall"
    assert events[0].elapsed_ms == 150.0
    assert events[0].thread == "main"


def test_performance_recorder_writes_log_off_caller_thread(tmp_path):
    local = PerformanceRecorder(tmp_path / "performance.log")
    write_started = threading.Event()
    release_write = threading.Event()
    writer_threads = []

    def slow_write(_event):
        write_started.set()
        release_write.wait(timeout=1)
        writer_threads.append(threading.current_thread().name)

    local._write = slow_write

    event = local.record("unit.async", 123, thread="caller")

    assert event in local.recent()
    assert write_started.wait(timeout=1)
    assert writer_threads == []

    release_write.set()
    local.flush()

    assert writer_threads == ["aichs-performance-writer"]


def test_performance_recorder_clear_and_recent_are_bounded(tmp_path):
    local = PerformanceRecorder(tmp_path / "performance.log")

    for idx in range(MAX_RECENT_EVENTS + 2):
        local.record(f"unit.{idx}", idx)
    local.flush()

    events = local.recent()
    assert len(events) == MAX_RECENT_EVENTS
    assert events[0].operation == "unit.2"

    local.clear()

    assert local.recent() == []
    assert local.slowest_operations() == []


def test_performance_recorder_summarizes_slowest_operations(tmp_path):
    local = PerformanceRecorder(tmp_path / "performance.log")
    local.record("git.apply", 40, detail="changes=3")
    local.record("workspace.apply", 75, detail="recent=12")
    local.record("git.apply", 90, detail="changes=9")
    local.record("markdown.render", 120.4567, detail="chars=5000")

    summaries = local.slowest_operations(limit=2)

    assert summaries == [
        PerformanceOperationSummary(
            operation="git.apply",
            count=2,
            total_ms=130.0,
            max_ms=90.0,
            avg_ms=65.0,
            latest_detail="changes=9",
            latest_at=summaries[0].latest_at,
        ),
        PerformanceOperationSummary(
            operation="markdown.render",
            count=1,
            total_ms=120.457,
            max_ms=120.457,
            avg_ms=120.457,
            latest_detail="chars=5000",
            latest_at=summaries[1].latest_at,
        ),
    ]

    assert local.slowest_operations(limit=0) == []


def test_slowest_logged_operations_summarizes_bounded_log_tail(tmp_path):
    log_path = tmp_path / "performance.log"
    early = json.dumps({
        "operation": "old.operation",
        "elapsed_ms": 999,
        "detail": "outside tail",
        "created_at": "2026-01-01T00:00:00",
    })
    malformed = "{not json"
    tail_lines = [
        json.dumps({
            "operation": "git.apply",
            "elapsed_ms": 40,
            "detail": "changes=3",
            "created_at": "2026-01-01T00:00:01",
        }),
        json.dumps({
            "operation": "git.apply",
            "elapsed_ms": 60,
            "detail": "changes=9",
            "created_at": "2026-01-01T00:00:02",
        }),
        json.dumps({
            "operation": "markdown.render",
            "elapsed_ms": 90.1254,
            "detail": "chars=5000",
            "created_at": "2026-01-01T00:00:03",
        }),
    ]
    tail = "\n".join(tail_lines) + "\n"
    log_path.write_text(f"{early}\n{malformed}\n{tail}", encoding="utf-8")

    summaries = slowest_logged_operations(
        log_path,
        limit=2,
        max_bytes=len(tail) + 4,
    )

    assert summaries == [
        PerformanceOperationSummary(
            operation="git.apply",
            count=2,
            total_ms=100.0,
            max_ms=60.0,
            avg_ms=50.0,
            latest_detail="changes=9",
            latest_at="2026-01-01T00:00:02",
        ),
        PerformanceOperationSummary(
            operation="markdown.render",
            count=1,
            total_ms=90.125,
            max_ms=90.125,
            avg_ms=90.125,
            latest_detail="chars=5000",
            latest_at="2026-01-01T00:00:03",
        ),
    ]


def test_slowest_logged_operations_handles_missing_or_zero_window(tmp_path):
    missing = tmp_path / "missing.log"

    assert slowest_logged_operations(missing) == []
    assert slowest_logged_operations(missing, max_bytes=0) == []


def test_performance_recorder_close_flushes_writer(tmp_path):
    local = PerformanceRecorder(tmp_path / "performance.log")

    local.close()
    local.record("unit.close", 1)
    local.close()

    assert "unit.close" in (tmp_path / "performance.log").read_text(encoding="utf-8")
