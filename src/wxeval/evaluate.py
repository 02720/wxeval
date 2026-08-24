from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd

from wxeval.config import BUCKETS, Location
from wxeval.metrics import (
    precip_daily_metrics,
    precip_hourly_metrics,
    temp_metrics,
)
from wxeval.sources.base import (
    PRECIPITATION_COL,
    TEMPERATURE_COL,
    TIME_COL,
    ForecastSource,
)
from wxeval.store import (
    Capture,
    capture_key,
    list_captures,
    load_capture,
    load_state,
    save_state,
)

logger = logging.getLogger(__name__)

HOURLY_CSV = "hourly.csv"
DAILY_CSV = "daily.csv"

KEY_COLUMNS = ["issue_utc", "model", "location", "bucket"]

MAX_LEAD_HOURS = BUCKETS[-1][1]

TEMP_KEYS = ("temp_mae", "temp_rmse", "temp_acc1", "temp_acc2")
PRECIP_HOURLY_KEYS = ("precip_binary_acc", "precip_ts", "precip_far", "precip_mar")
PRECIP_DAILY_KEYS = PRECIP_HOURLY_KEYS + ("precip_ets", "precip_bias")


@dataclass
class ScoringSummary:
    considered: int = 0
    updated: int = 0
    skipped_no_new_obs: int = 0
    errors: list[str] = field(default_factory=list)


def bucket_label(lead_hours: float) -> str | None:
    for lo, hi, label in BUCKETS:
        if lo <= lead_hours < hi:
            return label
    return None


def _lead_hours(valid: pd.DatetimeIndex, issue: pd.Timestamp) -> np.ndarray:
    return np.asarray(
        (valid.tz_convert("UTC") - issue) / pd.Timedelta(hours=1), dtype=float
    )


def _aligned_pairs(
    fcst: pd.DataFrame, obs: pd.DataFrame
) -> tuple[pd.DatetimeIndex, dict[str, np.ndarray], dict[str, np.ndarray]]:
    joined = fcst.join(obs, how="inner", rsuffix="_obs").dropna()
    idx = pd.DatetimeIndex(joined.index)
    fcst_vals = {
        TEMPERATURE_COL: joined[TEMPERATURE_COL].to_numpy(dtype=float),
        PRECIPITATION_COL: joined[PRECIPITATION_COL].to_numpy(dtype=float),
    }
    obs_vals = {
        TEMPERATURE_COL: joined[f"{TEMPERATURE_COL}_obs"].to_numpy(dtype=float),
        PRECIPITATION_COL: joined[f"{PRECIPITATION_COL}_obs"].to_numpy(dtype=float),
    }
    return idx, obs_vals, fcst_vals


def _row(
    capture: Capture,
    bucket: str,
    metrics: dict[str, float],
    n_pairs: int,
    generated_at: str,
) -> dict[str, object]:
    row: dict[str, object] = {
        "issue_utc": capture.issue_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": capture.model,
        "location": capture.location,
        "bucket": bucket,
        "n_pairs": n_pairs,
        "generated_at": generated_at,
    }
    row.update(metrics)
    return row


def _has_any_value(metrics: dict[str, float], keys: tuple[str, ...]) -> bool:
    return any(not np.isnan(metrics[k]) for k in keys)


def _day_start_utc(day: str, timezone: str) -> pd.Timestamp:
    return cast(
        pd.Timestamp,
        cast(pd.Timestamp, pd.Timestamp(f"{day}T00:00:00").tz_localize(timezone)).tz_convert("UTC"),
    )


def score_capture(
    capture: Capture,
    obs: pd.DataFrame,
    *,
    root: Path,
    location: Location,
    min_pairs: int,
    now: pd.Timestamp,
) -> tuple[bool, str | None]:
    if obs.empty:
        return False, None

    state = load_state(root)
    key = capture_key(capture.issue_utc, capture.model, capture.location)
    scored_through_raw = state.get(key)
    max_obs = cast(pd.Timestamp, pd.DatetimeIndex(obs.index).max())
    if scored_through_raw is not None:
        scored_through = pd.Timestamp(scored_through_raw)
        if max_obs <= scored_through:
            return False, None

    try:
        fcst = load_capture(capture)
    except Exception as exc:
        return False, f"failed loading capture {capture.path.name}: {exc}"

    scorable_mask = np.asarray(
        pd.DatetimeIndex(fcst.index).tz_convert("UTC") <= max_obs, dtype=bool
    )
    fcst = fcst.loc[scorable_mask]
    if fcst.empty:
        return False, None

    times, obs_vals, fcst_vals = _aligned_pairs(fcst, obs)
    temp_o = obs_vals[TEMPERATURE_COL]
    prec_o = obs_vals[PRECIPITATION_COL]
    temp_f = fcst_vals[TEMPERATURE_COL]
    prec_f = fcst_vals[PRECIPITATION_COL]

    leads = _lead_hours(times, capture.issue_utc)
    generated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    hourly_rows: list[dict[str, object]] = []
    daily_rows: list[dict[str, object]] = []

    for lo, hi, label in BUCKETS:
        mask = (leads >= lo) & (leads < hi)
        n = int(mask.sum())
        if n == 0 or n < min_pairs:
            continue
        tm = temp_metrics(temp_o[mask], temp_f[mask])
        pm = precip_hourly_metrics(prec_o[mask], prec_f[mask])
        merged = {**tm, **pm}
        if _has_any_value(merged, TEMP_KEYS) or _has_any_value(merged, PRECIP_HOURLY_KEYS):
            hourly_rows.append(_row(capture, label, merged, n, generated_at))

    days = np.array([ts.tz_convert(location.timezone).date() for ts in times], dtype=object)
    by_day: dict[object, list[int]] = {}
    for i, d in enumerate(days):
        by_day.setdefault(d, []).append(i)

    min_daily_pairs = max(2, min_pairs // 8)
    pooled_by_bucket: dict[str, list[int]] = {}
    for d, indices in by_day.items():
        lead = (_day_start_utc(str(d), location.timezone) - capture.issue_utc).total_seconds() / 3600.0
        label = bucket_label(lead)
        if label is None:
            continue
        pooled_by_bucket.setdefault(label, []).extend(indices)

    for label, idx_list in sorted(pooled_by_bucket.items()):
        idx_arr = np.array(idx_list)
        tm = temp_metrics(temp_o[idx_arr], temp_f[idx_arr])
        pm = precip_daily_metrics(prec_o[idx_arr], prec_f[idx_arr])
        merged = {**tm, **pm}
        if len(idx_arr) < min_daily_pairs:
            continue
        if _has_any_value(merged, TEMP_KEYS) or _has_any_value(merged, PRECIP_DAILY_KEYS):
            daily_rows.append(_row(capture, label, merged, len(idx_arr), generated_at))

    _write_results(root, HOURLY_CSV, hourly_rows)
    _write_results(root, DAILY_CSV, daily_rows)

    new_through = (
        cast(pd.Timestamp, pd.DatetimeIndex(times).max()) if len(times) else None
    )
    updated_state = load_state(root)
    if new_through is not None:
        updated_state[key] = new_through.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
        save_state(root, updated_state)

    return bool(hourly_rows or daily_rows), None


def _write_results(root: Path, filename: str, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    path = Path(root) / "results" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = pd.read_csv(path) if path.exists() else pd.DataFrame()
    combined = pd.concat([existing, pd.DataFrame(rows)], ignore_index=True)
    combined = combined.drop_duplicates(subset=KEY_COLUMNS, keep="last")
    combined = combined.sort_values(KEY_COLUMNS).reset_index(drop=True)
    tmp = path.with_suffix(".tmp")
    combined.to_csv(tmp, index=False)
    tmp.replace(path)


def run_scoring(
    root: Path,
    locations_by_name: dict[str, Location],
    sources: dict[str, ForecastSource],
    obs_loader,
    *,
    now: pd.Timestamp | None = None,
    min_pairs: int = 24,
) -> ScoringSummary:
    now = now or pd.Timestamp.now(tz="UTC")
    summary = ScoringSummary()
    state = load_state(root)

    captures = list_captures(root)
    obs_cache: dict[str, pd.DataFrame] = {}

    for capture in captures:
        loc = locations_by_name.get(capture.location)
        if loc is None:
            summary.errors.append(f"unknown location {capture.location!r}, skipping {capture.path.name}")
            continue
        key = capture_key(capture.issue_utc, capture.model, capture.location)
        horizon_end = capture.issue_utc + pd.Timedelta(hours=MAX_LEAD_HOURS)
        scored_through = state.get(key)
        fully_scored = (
            scored_through is not None
            and pd.Timestamp(scored_through) >= horizon_end - pd.Timedelta(hours=1)
        )
        if fully_scored:
            summary.skipped_no_new_obs += 1
            continue
        if capture.location not in obs_cache:
            try:
                obs_cache[capture.location] = obs_loader(loc)
            except Exception as exc:
                summary.errors.append(f"obs load failed for {capture.location}: {exc}")
                continue
        summary.considered += 1
        try:
            updated, error = score_capture(
                capture,
                obs_cache[capture.location],
                root=root,
                location=loc,
                min_pairs=min_pairs,
                now=now,
            )
        except Exception as exc:
            summary.errors.append(f"scoring failed for {capture.path.name}: {exc}")
            continue
        if error:
            summary.errors.append(error)
        if updated:
            summary.updated += 1

    return summary


__all__ = [
    "Capture",
    "DAILY_CSV",
    "HOURLY_CSV",
    "KEY_COLUMNS",
    "ScoringSummary",
    "TIME_COL",
    "bucket_label",
    "run_scoring",
    "score_capture",
]
