from __future__ import annotations

from typing import Any

import numpy as np
from cyeva import PrecipitationComparison, TemperatureComparison

METRIC_KEYS_TEMPERATURE = ("temp_mae", "temp_rmse", "temp_acc1", "temp_acc2")
METRIC_KEYS_PRECIP_HOURLY = (
    "precip_binary_acc",
    "precip_ts",
    "precip_far",
    "precip_mar",
)
METRIC_KEYS_PRECIP_DAILY = METRIC_KEYS_PRECIP_HOURLY + ("precip_ets", "precip_bias")


def _nan_dict(keys: tuple[str, ...]) -> dict[str, float]:
    return {k: float("nan") for k in keys}


def _round(value: Any) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return float("nan")
    if np.isnan(f):
        return f
    return round(f, 4)


def _to_fraction(value: Any) -> float:
    try:
        return float(value) / 100.0
    except (TypeError, ValueError):
        return float("nan")


def _pairs(obs: np.ndarray, fcst: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    obs = np.asarray(obs, dtype=float)
    fcst = np.asarray(fcst, dtype=float)
    if obs.shape != fcst.shape or obs.size == 0:
        return None
    mask = ~(np.isnan(obs) | np.isnan(fcst))
    if not mask.any():
        return None
    return obs[mask], fcst[mask]


def temp_metrics(obs: np.ndarray, fcst: np.ndarray) -> dict[str, float]:
    pairs = _pairs(obs, fcst)
    if pairs is None:
        return _nan_dict(METRIC_KEYS_TEMPERATURE)
    o, f = pairs
    try:
        comp = TemperatureComparison(o, f, unit="degC")
        result = {
            "temp_mae": _round(comp.calc_mae()),
            "temp_rmse": _round(comp.calc_rmse()),
            "temp_acc1": _round(comp.calc_diff_accuracy_ratio(limit=1)),
            "temp_acc2": _round(comp.calc_diff_accuracy_ratio(limit=2)),
        }
    except Exception:
        return _nan_dict(METRIC_KEYS_TEMPERATURE)
    return {k: (_round(v)) for k, v in result.items()}


def precip_hourly_metrics(obs: np.ndarray, fcst: np.ndarray) -> dict[str, float]:
    pairs = _pairs(obs, fcst)
    if pairs is None:
        return _nan_dict(METRIC_KEYS_PRECIP_HOURLY)
    o, f = pairs
    try:
        comp = PrecipitationComparison(o, f, unit="mm")
        return {
            "precip_binary_acc": _round(comp.calc_binary_accuracy_ratio()),
            "precip_ts": _round(comp.calc_ts(kind="1h")),
            "precip_far": _round(_to_fraction(comp.calc_false_alarm_ratio(kind="1h"))),
            "precip_mar": _round(_to_fraction(comp.calc_miss_ratio(kind="1h"))),
        }
    except Exception:
        return _nan_dict(METRIC_KEYS_PRECIP_HOURLY)


def precip_daily_metrics(obs: np.ndarray, fcst: np.ndarray) -> dict[str, float]:
    pairs = _pairs(obs, fcst)
    if pairs is None:
        return _nan_dict(METRIC_KEYS_PRECIP_DAILY)
    o, f = pairs
    try:
        comp = PrecipitationComparison(o, f, unit="mm")
        base = {
            "precip_binary_acc": comp.calc_binary_accuracy_ratio(),
            "precip_ts": comp.calc_ts(kind="24h"),
            "precip_far": _to_fraction(comp.calc_false_alarm_ratio(kind="24h")),
            "precip_mar": _to_fraction(comp.calc_miss_ratio(kind="24h")),
            "precip_ets": comp.calc_ets(kind="24h"),
            "precip_bias": comp.calc_bias_score(kind="24h"),
        }
        return {k: _round(v) for k, v in base.items()}
    except Exception:
        return _nan_dict(METRIC_KEYS_PRECIP_DAILY)
