import threading

from services.performance import MAX_RECENT_EVENTS, PerformanceRecorder, time_operation


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


def test_performance_recorder_close_flushes_writer(tmp_path):
    local = PerformanceRecorder(tmp_path / "performance.log")

    local.close()
    local.record("unit.close", 1)
    local.close()

    assert "unit.close" in (tmp_path / "performance.log").read_text(encoding="utf-8")
