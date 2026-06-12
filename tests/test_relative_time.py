from datetime import datetime, timedelta

import pytest

from services.relative_time import format_relative_ago


def _ago(**kwargs) -> str:
    now = datetime(2026, 6, 11, 12, 0, 0)
    then = now - timedelta(**kwargs)
    return format_relative_ago(then, now=now)


@pytest.mark.parametrize(
    "delta,expected",
    [
        ({"seconds": 10}, "now"),
        ({"minutes": 1}, "1m"),
        ({"minutes": 4, "seconds": 1}, "5m"),
        ({"minutes": 59, "seconds": 1}, "1h"),
        ({"hours": 4}, "4h"),
        ({"hours": 23, "minutes": 1}, "1d"),
        ({"days": 1}, "1d"),
        ({"days": 6, "hours": 23}, "1w"),
        ({"days": 13}, "2w"),
        ({"days": 40}, "2mo"),
        ({"days": 400}, "2y"),
    ],
)
def test_format_relative_ago_rounds_up(delta, expected):
    assert _ago(**delta) == expected
