from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

from wxeval.observations import ObsClient, parse_archive_payload
from wxeval.sources.base import SourceError


def archive_payload(start: str, hours: int) -> dict[str, Any]:
    times = pd.date_range(start, periods=hours, freq="h", tz="UTC").strftime("%Y-%m-%dT%H:%M").tolist()
    return {
        "hourly": {
            "time": times,
            "temperature_2m": [float(i % 10) for i in range(hours)],
            "precipitation": [0.0 if i % 3 else 1.5 for i in range(hours)],
        }
    }


class FakeSession:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = payloads
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, params: Any = None, timeout: Any = None) -> Any:
        self.calls.append(dict(params))
        return FakeResponse(self.payloads.pop(0))


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self._payload


def test_parse_and_merge_dedupe(tmp_path):
    session = FakeSession([archive_payload("2026-08-01", 48), archive_payload("2026-08-02", 48)])
    client = ObsClient(tmp_path, session=session)
    df1 = client.fetch_update("梧州", 23.4, 111.2, "2026-08-01", "2026-08-02")
    assert len(df1) == 48
    df2 = client.fetch_update("梧州", 23.4, 111.2, "2026-08-02", "2026-08-03")
    assert len(df2) == 72
    assert df2.index.is_monotonic_increasing
    assert not df2.index.duplicated().any()


def test_persisted_cache_reload(tmp_path):
    session = FakeSession([archive_payload("2026-08-01", 24)])
    client = ObsClient(tmp_path, session=session)
    client.fetch_update("博白", 22.2, 109.9, "2026-08-01", "2026-08-01")
    client2 = ObsClient(tmp_path, session=FakeSession([archive_payload("2026-08-05", 24)]))
    merged = client2.fetch_update("博白", 22.2, 109.9, "2026-08-05", "2026-08-05")
    assert len(merged) == 48
    cached = client2.load("博白")
    pd.testing.assert_frame_equal(cached, merged)


def test_null_values_become_nan():
    payload = {
        "hourly": {
            "time": ["2026-08-01T00:00", "2026-08-01T01:00"],
            "temperature_2m": [20.0, None],
            "precipitation": [None, 0.0],
        }
    }
    df = parse_archive_payload(payload)
    assert np.isnan(df["temperature_2m"].iloc[1])
    assert np.isnan(df["precipitation"].iloc[0])


def test_error_payload_raises():
    with pytest.raises(SourceError):
        parse_archive_payload({"error": True, "reason": "quota"})


def test_empty_payload_returns_empty_frame():
    df = parse_archive_payload({"hourly": {"time": [], "temperature_2m": [], "precipitation": []}})
    assert df.empty
