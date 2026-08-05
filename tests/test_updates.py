from __future__ import annotations

import json

from services.updates import (
    UpdateAvailability,
    check_for_update,
    dismiss_version,
    fetch_latest_version,
    installed_version,
    is_newer,
    mark_checked,
    parse_version_key,
    should_prompt,
    should_run_network_check,
)


def test_parse_and_compare_versions():
    assert parse_version_key("0.5.1") < parse_version_key("0.5.2")
    assert parse_version_key("0.5.2") == parse_version_key("v0.5.2")
    assert is_newer("0.5.2", "0.5.1")
    assert not is_newer("0.5.1", "0.5.1")
    assert not is_newer("0.5.0", "0.5.1")
    assert is_newer("1.0.0", "0.9.9")
    assert is_newer("0.5.2", "0.5.2a1")


def test_installed_version_falls_back_to_pyproject():
    version = installed_version(package="aichs-package-that-does-not-exist")
    assert version == "0.5.1"


def test_fetch_latest_version_reads_pypi_json(monkeypatch):
    payload = json.dumps({"info": {"version": "0.9.0"}}).encode("utf-8")

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return payload

    def fake_urlopen(request, timeout):
        assert "pypi.org/pypi/aichs/json" in request.full_url
        assert timeout == 8.0
        return _Response()

    monkeypatch.setattr("services.updates.urllib.request.urlopen", fake_urlopen)
    assert fetch_latest_version() == "0.9.0"


def test_check_for_update_returns_availability_when_newer():
    result = check_for_update(installed="0.5.1", fetch=lambda: "0.5.2")
    assert result == UpdateAvailability(installed="0.5.1", latest="0.5.2")
    assert result.upgrade_command == "pipx upgrade aichs"


def test_check_for_update_quiet_on_same_or_older_or_error():
    assert check_for_update(installed="0.5.2", fetch=lambda: "0.5.2") is None
    assert check_for_update(installed="0.5.2", fetch=lambda: "0.5.1") is None

    def boom():
        raise TimeoutError("offline")

    assert check_for_update(installed="0.5.1", fetch=boom) is None


def test_should_prompt_respects_dismissed_version():
    availability = UpdateAvailability(installed="0.5.1", latest="0.5.2")
    assert should_prompt(availability, "")
    assert should_prompt(availability, "0.5.0")
    assert not should_prompt(availability, "0.5.2")
    assert not should_prompt(None, "")


def test_should_run_network_check_throttle_and_flags():
    assert should_run_network_check(enabled=True, last_checked=0, frozen=False)
    assert not should_run_network_check(enabled=False, last_checked=0, frozen=False)
    assert not should_run_network_check(enabled=True, last_checked=0, frozen=True)
    assert not should_run_network_check(
        enabled=True,
        last_checked=1_000.0,
        now=1_000.0 + 60,
        interval_sec=3600,
        frozen=False,
    )
    assert should_run_network_check(
        enabled=True,
        last_checked=1_000.0,
        now=1_000.0 + 3600,
        interval_sec=3600,
        frozen=False,
    )


def test_mark_checked_and_dismiss_version_helpers():
    data = mark_checked({}, now=123.5)
    assert data["update_last_checked"] == 123.5
    dismissed = dismiss_version(data, "0.5.2")
    assert dismissed["update_dismissed_version"] == "0.5.2"
    assert data.get("update_dismissed_version") is None
