from __future__ import annotations

import time

from PyQt6.QtCore import QObject, QTimer

from services.performance import recorder


class EventLoopStallMonitor(QObject):
    def __init__(
        self,
        parent=None,
        *,
        interval_ms: int = 50,
        stall_ms: float = 100.0,
        clock=time.perf_counter,
    ):
        super().__init__(parent)
        self._interval_s = interval_ms / 1000.0
        self._stall_ms = float(stall_ms)
        self._clock = clock
        self._expected = self._clock() + self._interval_s
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        now = self._clock()
        delay_ms = max(0.0, (now - self._expected) * 1000.0)
        if delay_ms >= self._stall_ms:
            recorder.record(
                "event_loop.stall",
                delay_ms,
                detail=f"interval_ms={int(self._interval_s * 1000)}",
                thread="main",
            )
        self._expected = now + self._interval_s
