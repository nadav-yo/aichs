from __future__ import annotations

import math
from datetime import datetime


def format_relative_ago(dt: datetime, *, now: datetime | None = None) -> str:
    """Coarse, rounded-up relative time for list captions (now, 4h, 1d, 1w, …)."""
    if now is None:
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    dt_cmp = _naive(dt)
    now_cmp = _naive(now)
    secs = (now_cmp - dt_cmp).total_seconds()
    if secs < 45:
        return "now"
    minutes = math.ceil(secs / 60)
    if minutes < 60:
        return f"{minutes}m"
    hours = math.ceil(secs / 3600)
    if hours < 24:
        return f"{hours}h"
    days = math.ceil(secs / 86400)
    if days < 7:
        return f"{days}d"
    weeks = math.ceil(days / 7)
    if weeks < 5:
        return f"{weeks}w"
    months = math.ceil(days / 30)
    if months < 12:
        return f"{months}mo"
    years = math.ceil(days / 365)
    return f"{years}y"


def _naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.replace(tzinfo=None)
