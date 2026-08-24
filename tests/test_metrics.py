from __future__ import annotations

import math

import numpy as np
import pytest

from wxeval.metrics import (
    METRIC_KEYS_PRECIP_DAILY,
    METRIC_KEYS_PRECIP_HOURLY,
    METRIC_KEYS_TEMPERATURE,
    precip_daily_metrics,
    precip_hourly_metrics,
    temp_metrics,
)


def close(a: float, b: float, tol: float = 1e-3) -> bool:
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return abs(a - b) <= tol


OBS = np.array([0.0, 1.0, 5.0])
FCST = np.array([0.0, 0.0, 4.0])


class TestTempMetrics:
    def test_known_answer(self):
        obs = np.array([10.0, 12.0, 14.0])
        fcst = np.array([11.0, 11.0, 15.0])
        m = temp_metrics(obs, fcst)
        assert set(m) == set(METRIC_KEYS_TEMPERATURE)
        assert close(m["temp_mae"], (1 + 1 + 1) / 3)
        assert close(m["temp_rmse"], math.sqrt((1 + 1 + 1) / 3))
        assert close(m["temp_acc2"], 100.0)

    def test_nan_inputs_dropped(self):
        obs = np.array([10.0, np.nan, 14.0])
        fcst = np.array([10.0, 20.0, 14.0])
        m = temp_metrics(obs, fcst)
        assert close(m["temp_mae"], 0.0)
        assert close(m["temp_acc2"], 100.0)

    def test_all_nan_returns_nan_dict(self):
        m = temp_metrics(np.array([np.nan]), np.array([np.nan]))
        assert all(math.isnan(v) for v in m.values())


class TestPrecipHourly:
    def test_known_answer(self):
        m = precip_hourly_metrics(OBS, FCST)
        assert set(m) == set(METRIC_KEYS_PRECIP_HOURLY)
        assert close(m["precip_ts"], 0.5)
        assert close(m["precip_far"], 0.0)
        assert close(m["precip_mar"], 0.5)
        assert close(m["precip_binary_acc"], 200 / 3)

    def test_no_rain_anywhere_gives_nan_ts_without_raise(self):
        zeros = np.zeros(30)
        m = precip_hourly_metrics(zeros, zeros.copy())
        assert math.isnan(m["precip_ts"])
        assert close(m["precip_binary_acc"], 100.0)

    def test_perfect_forecast(self):
        obs = np.array([0.0, 0.0, 2.0, 8.0])
        m = precip_hourly_metrics(obs, obs.copy())
        assert close(m["precip_ts"], 1.0)
        assert close(m["precip_far"], 0.0)
        assert close(m["precip_mar"], 0.0)


class TestPrecipDaily:
    def test_known_answer(self):
        m = precip_daily_metrics(OBS, FCST)
        assert set(m) == set(METRIC_KEYS_PRECIP_DAILY)
        assert close(m["precip_ts"], 0.5)
        h, mi, f, n = 1, 1, 0, 3
        hits_random = (h + mi) * (h + f) / n
        expected_ets = (h - hits_random) / (h + mi + f - hits_random)
        assert close(m["precip_ets"], expected_ets)
        assert close(m["precip_bias"], (h + f) / (h + mi))

    @pytest.mark.parametrize("fn", [precip_daily_metrics])
    def test_mismatched_shapes_return_nan(self, fn):
        out = fn(np.array([1.0, 2.0]), np.array([1.0]))
        assert all(math.isnan(v) for v in out.values())
