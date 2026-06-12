from __future__ import annotations

import json
import queue
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

import config


DEFAULT_SLOW_MS = 100.0
MAX_RECENT_EVENTS = 500
DEFAULT_LOG_SUMMARY_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class PerformanceEvent:
    operation: str
    elapsed_ms: float
    detail: str = ""
    thread: str = ""
    created_at: str = ""


@dataclass(frozen=True)
class PerformanceOperationSummary:
    operation: str
    count: int
    total_ms: float
    max_ms: float
    avg_ms: float
    latest_detail: str = ""
    latest_at: str = ""


class PerformanceRecorder:
    def __init__(self, log_path: Path | None = None, *, writer_idle_s: float = 0.25):
        self._log_path = log_path
        self._writer_idle_s = float(writer_idle_s)
        self._events: list[PerformanceEvent] = []
        self._lock = threading.Lock()
        self._write_queue: queue.Queue[PerformanceEvent | None] = queue.Queue()
        self._writer_lock = threading.Lock()
        self._writer: threading.Thread | None = None

    @property
    def log_path(self) -> Path:
        return self._log_path or (config.AICHS_HOME / "performance.log")

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def recent(self) -> list[PerformanceEvent]:
        with self._lock:
            return list(self._events)

    def slowest_operations(self, limit: int = 10) -> list[PerformanceOperationSummary]:
        with self._lock:
            events = list(self._events)
        return summarize_events(events, limit=limit)

    def record(
        self,
        operation: str,
        elapsed_ms: float,
        *,
        detail: str = "",
        thread: str = "",
    ) -> PerformanceEvent:
        event = PerformanceEvent(
            operation=str(operation),
            elapsed_ms=round(float(elapsed_ms), 3),
            detail=str(detail or ""),
            thread=str(thread or threading.current_thread().name),
            created_at=datetime.now().isoformat(),
        )
        with self._lock:
            self._events.append(event)
            del self._events[:-MAX_RECENT_EVENTS]
        self._enqueue_write(event)
        return event

    def flush(self) -> None:
        self._write_queue.join()

    def close(self) -> None:
        with self._writer_lock:
            writer = self._writer
            if writer is not None and not writer.is_alive():
                self._writer = None
                writer = None
        if writer is None:
            return
        self._write_queue.put(None)
        self._write_queue.join()
        writer.join(timeout=1.0)
        with self._writer_lock:
            if self._writer is writer:
                self._writer = None

    def _enqueue_write(self, event: PerformanceEvent) -> None:
        self._ensure_writer()
        self._write_queue.put(event)

    def _ensure_writer(self) -> None:
        with self._writer_lock:
            if self._writer is not None and self._writer.is_alive():
                return
            self._writer = threading.Thread(
                target=self._write_loop,
                name="aichs-performance-writer",
                daemon=True,
            )
            self._writer.start()

    def _write_loop(self) -> None:
        while True:
            try:
                event = self._write_queue.get(timeout=self._writer_idle_s)
            except queue.Empty:
                with self._writer_lock:
                    if (
                        self._write_queue.empty()
                        and self._writer is threading.current_thread()
                    ):
                        self._writer = None
                        return
                continue
            try:
                if event is None:
                    return
                self._write(event)
            finally:
                self._write_queue.task_done()

    def _write(self, event: PerformanceEvent) -> None:
        path = self.log_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")


recorder = PerformanceRecorder()


@contextmanager
def time_operation(
    operation: str,
    *,
    detail: str = "",
    slow_ms: float = DEFAULT_SLOW_MS,
) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if elapsed_ms >= slow_ms:
            recorder.record(operation, elapsed_ms, detail=detail)


def summarize_events(
    events: list[PerformanceEvent],
    *,
    limit: int = 10,
) -> list[PerformanceOperationSummary]:
    grouped: dict[str, list[PerformanceEvent]] = {}
    for event in events:
        grouped.setdefault(event.operation, []).append(event)

    summaries = []
    for operation, operation_events in grouped.items():
        total_ms = sum(event.elapsed_ms for event in operation_events)
        max_ms = max(event.elapsed_ms for event in operation_events)
        latest = operation_events[-1]
        count = len(operation_events)
        summaries.append(
            PerformanceOperationSummary(
                operation=operation,
                count=count,
                total_ms=round(total_ms, 3),
                max_ms=round(max_ms, 3),
                avg_ms=round(total_ms / count, 3),
                latest_detail=latest.detail,
                latest_at=latest.created_at,
            )
        )

    summaries.sort(key=lambda item: (-item.total_ms, -item.max_ms, item.operation))
    return summaries[:max(0, int(limit))]


def slowest_logged_operations(
    log_path: Path | None = None,
    *,
    limit: int = 10,
    max_bytes: int = DEFAULT_LOG_SUMMARY_BYTES,
) -> list[PerformanceOperationSummary]:
    path = log_path or recorder.log_path
    return summarize_events(_read_logged_events(path, max_bytes=max_bytes), limit=limit)


def _read_logged_events(path: Path, *, max_bytes: int) -> list[PerformanceEvent]:
    if max_bytes <= 0 or not path.exists():
        return []
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            f.readline()
        raw = f.read(max_bytes)

    events: list[PerformanceEvent] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        try:
            operation = str(data["operation"])
            elapsed_ms = float(data["elapsed_ms"])
        except (KeyError, TypeError, ValueError):
            continue
        events.append(
            PerformanceEvent(
                operation=operation,
                elapsed_ms=round(elapsed_ms, 3),
                detail=str(data.get("detail") or ""),
                thread=str(data.get("thread") or ""),
                created_at=str(data.get("created_at") or ""),
            )
        )
    return events
