from __future__ import annotations

import json
from typing import Any

import pandas as pd
import pytest

from wxeval.sources.base import SourceError, get_with_retry
from wxeval.sources.open_meteo import OpenMeteoForecastSource, parse_forecast_payload


def make_payload(hours: int = 48, null_at: int | None = None) -> dict[str, Any]:
    times = pd.date_range("2026-08-24", periods=hours, freq="h").strftime("%Y-%m-%dT%H:%M").tolist()
    temp: list[float | None] = [20.0 + i * 0.1 for i in range(hours)]
    precip: list[float | None] = [0.0] * hours
    if null_at is not None:
        temp[null_at] = None
        precip[null_at] = None
    return {"hourly": {"time": times, "temperature_2m": temp, "precipitation": precip}}


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests as _r

            raise _r.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, params: Any = None, timeout: Any = None) -> FakeResponse:
        self.calls.append({"url": url, "params": params})
        return FakeResponse(self.payload, self.status)


def test_parse_payload_builds_utc_frame_with_attrs():
    fc = parse_forecast_payload(make_payload(null_at=5), model="ecmwf_ifs")
    assert fc.model == "ecmwf_ifs"
    idx = pd.DatetimeIndex(fc.frame.index)
    assert idx.tz is not None
    assert fc.frame.index.name == "time"
    assert list(fc.frame.columns) == ["temperature_2m", "precipitation"]
    assert len(fc.frame) == 48
    assert abs(fc.frame["temperature_2m"].iloc[0] - 20.0) < 1e-9
    assert abs(fc.frame["temperature_2m"].iloc[10] - 21.0) < 1e-9
    assert abs(fc.frame["precipitation"].iloc[10] - 0.0) < 1e-9
    assert pd.isna(fc.frame["temperature_2m"].iloc[5])
    assert pd.isna(fc.frame["precipitation"].iloc[5])
    assert fc.issue_utc == pd.Timestamp("2026-08-24T00:00", tz="UTC")


def test_parse_error_payload_raises():
    with pytest.raises(SourceError, match="boom"):
        parse_forecast_payload({"error": True, "reason": "boom"}, model="gfs")


def test_parse_empty_series_raises():
    payload = {"hourly": {"time": [], "temperature_2m": [], "precipitation": []}}
    with pytest.raises(SourceError, match="empty"):
        parse_forecast_payload(payload, model="icon_global")


def test_fetch_passes_model_and_coords():
    session = FakeSession(make_payload(24))
    src = OpenMeteoForecastSource("ncep_gfs_global", session=session)
    fc = src.fetch(latitude=23.477, longitude=111.279, forecast_days=16)
    p = session.calls[0]["params"]
    assert p["models"] == "ncep_gfs_global"
    assert p["latitude"] == pytest.approx(23.477)
    assert p["timezone"] == "GMT"
    assert p["forecast_days"] == 16
    assert len(fc.frame) == 24


def test_retry_then_success():
    attempts = {"n": 0}

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("net down")
        return "ok"

    sleeps: list[float] = []
    assert get_with_retry(flaky, sleep=sleeps.append) == "ok"
    assert attempts["n"] == 3
    assert len(sleeps) == 2


def test_retry_exhaustion_raises_source_error():
    def always_fail() -> None:
        raise TimeoutError("slow")

    with pytest.raises(SourceError):
        get_with_retry(always_fail, retries=2, base_delay=0.0)


def test_payload_json_roundtrip():
    payload = make_payload()
    assert json.loads(json.dumps(payload))["hourly"]["time"][0].startswith("2026-08-24")
