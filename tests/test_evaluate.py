from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from wxeval.config import Location
from wxeval.evaluate import (
    DAILY_CSV,
    HOURLY_CSV,
    bucket_label,
    run_scoring,
    score_capture,
)
from wxeval.store import (
    capture_key,
    list_captures,
    load_state,
    save_capture,
)

ISSUE = pd.Timestamp("2026-08-01T00:00", tz="UTC")
LOC = Location(name="梧州", latitude=23.477, longitude=111.279, timezone="Asia/Shanghai")


def make_series(issue: pd.Timestamp, periods: int) -> pd.DataFrame:
    idx = pd.date_range(issue, periods=periods, freq="h", tz="UTC", name="time")
    i = np.arange(periods)
    return pd.DataFrame(
        {
            "temperature_2m": 20.0 + 5 * np.sin(i / 24 * np.pi),
            "precipitation": np.where(i % 12 == 0, 2.5, 0.0),
        },
        index=idx,
    )


def store(root, issue=ISSUE, periods=384):
    save_capture(root, make_series(issue, periods), issue, "ecmwf_ifs", LOC.name)
    return list_captures(root)[0]


def read_csv(root, name):
    return pd.read_csv(root / "results" / name)


class TestBucketLabel:
    @pytest.mark.parametrize(
        ("lead", "expected"),
        [
            (0, "0-24h"),
            (23.999, "0-24h"),
            (24, "24-72h"),
            (71.5, "24-72h"),
            (72, "72-168h"),
            (167.2, "72-168h"),
            (168, "168-384h"),
            (383, "168-384h"),
        ],
    )
    def test_boundaries_left_closed(self, lead, expected):
        assert bucket_label(lead) == expected

    def test_out_of_range_returns_none(self):
        assert bucket_label(-1) is None
        assert bucket_label(384) is None


class TestScoreCapture:
    def test_perfect_forecast_all_buckets(self, tmp_path):
        cap = store(tmp_path, periods=384)
        obs = make_series(ISSUE, 384)
        ok, err = score_capture(
            cap, obs, root=tmp_path, location=LOC, min_pairs=24, now=ISSUE
        )
        assert ok and err is None
        hourly = read_csv(tmp_path, HOURLY_CSV)
        assert set(hourly["bucket"]) == {"0-24h", "24-72h", "72-168h", "168-384h"}
        by_bucket = hourly.set_index("bucket")
        assert by_bucket.loc["0-24h", "n_pairs"] == 24
        assert by_bucket.loc["24-72h", "n_pairs"] == 48
        assert by_bucket.loc["72-168h", "n_pairs"] == 96
        assert by_bucket.loc["168-384h", "n_pairs"] == 216
        assert (hourly["temp_mae"].abs() < 1e-6).all()
        assert (hourly["temp_acc2"] == 100.0).all()
        assert ((hourly["precip_ts"] - 1.0).abs() < 1e-6).all()

    def test_daily_rows_use_local_midnight_lead(self, tmp_path):
        cap = store(tmp_path, periods=384)
        obs = make_series(ISSUE, 384)
        score_capture(cap, obs, root=tmp_path, location=LOC, min_pairs=24, now=ISSUE)
        daily = read_csv(tmp_path, DAILY_CSV)
        assert len(daily) > 0
        day_two_start_utc = pd.Timestamp("2026-08-02T00:00").tz_localize(
            LOC.timezone
        ).tz_convert("UTC")
        lead_day_two = (day_two_start_utc - ISSUE).total_seconds() / 3600
        expected = bucket_label(lead_day_two)
        assert expected == "0-24h"
        assert expected in set(daily["bucket"])
        valid_ets = daily["precip_ets"].dropna()
        assert ((valid_ets >= -1) & (valid_ets <= 1)).all()

    def test_negative_lead_local_day_excluded(self, tmp_path):
        cap = store(tmp_path, periods=384)
        obs = make_series(ISSUE, 384)
        score_capture(cap, obs, root=tmp_path, location=LOC, min_pairs=2, now=ISSUE)
        daily = read_csv(tmp_path, DAILY_CSV)
        first_local_day_start = pd.Timestamp("2026-08-01T00:00").tz_localize(
            LOC.timezone
        ).tz_convert("UTC")
        assert (first_local_day_start - ISSUE).total_seconds() < 0
        assert set(daily["bucket"]) == {"0-24h", "24-72h", "72-168h", "168-384h"}
        assert len(daily) == 4

    def test_idempotent_no_duplicate_rows(self, tmp_path):
        cap = store(tmp_path, periods=200)
        obs = make_series(ISSUE, 100)
        for _ in range(2):
            score_capture(cap, obs, root=tmp_path, location=LOC, min_pairs=24, now=ISSUE)
        hourly = read_csv(tmp_path, HOURLY_CSV)
        assert len(hourly.drop_duplicates(subset=["issue_utc", "model", "location", "bucket"])) == len(hourly)
        assert len(hourly) == 3

    def test_incremental_obs_extends_not_duplicates(self, tmp_path):
        cap = store(tmp_path, periods=300)
        obs_short = make_series(ISSUE, 60)
        score_capture(cap, obs_short, root=tmp_path, location=LOC, min_pairs=24, now=ISSUE)
        h1 = read_csv(tmp_path, HOURLY_CSV)
        assert len(h1) == 2
        assert h1.set_index("bucket").loc["24-72h", "n_pairs"] == 36
        obs_long = make_series(ISSUE, 120)
        score_capture(cap, obs_long, root=tmp_path, location=LOC, min_pairs=24, now=ISSUE)
        h2 = read_csv(tmp_path, HOURLY_CSV)
        assert len(h2) == 3
        assert h2.set_index("bucket").loc["24-72h", "n_pairs"] == 48
        assert h2.set_index("bucket").loc["72-168h", "n_pairs"] == 48

    def test_min_pairs_skips_small_buckets(self, tmp_path):
        cap = store(tmp_path, periods=100)
        obs = make_series(ISSUE, 30)
        score_capture(cap, obs, root=tmp_path, location=LOC, min_pairs=24, now=ISSUE)
        hourly = read_csv(tmp_path, HOURLY_CSV)
        assert set(hourly["bucket"]) == {"0-24h"}

    def test_state_tracks_scored_through(self, tmp_path):
        cap = store(tmp_path, periods=100)
        obs = make_series(ISSUE, 50)
        score_capture(cap, obs, root=tmp_path, location=LOC, min_pairs=2, now=ISSUE)
        state = load_state(tmp_path)
        key = capture_key(ISSUE, "ecmwf_ifs", LOC.name)
        assert state[key] == "2026-08-03T01:00:00Z"

    def test_empty_obs_is_noop(self, tmp_path):
        cap = store(tmp_path)
        empty = make_series(ISSUE, 0)
        ok, err = score_capture(cap, empty, root=tmp_path, location=LOC, min_pairs=24, now=ISSUE)
        assert not ok and err is None


class TestRunScoring:
    def test_skips_fully_scored_and_reports_errors(self, tmp_path):
        cap_old = ISSUE - pd.Timedelta(days=25)
        save_capture(tmp_path, make_series(cap_old, 384), cap_old, "ecmwf_ifs", LOC.name)
        old_cap = list_captures(tmp_path)[0]
        full_obs = make_series(cap_old, 500)
        score_capture(old_cap, full_obs, root=tmp_path, location=LOC, min_pairs=2, now=cap_old)

        save_capture(tmp_path, make_series(ISSUE, 384), ISSUE, "ecmwf_ifs", LOC.name)
        ghost_loc = Location(name="幽灵", latitude=1, longitude=1, timezone="UTC")
        save_capture(tmp_path, make_series(ISSUE, 48), ISSUE - pd.Timedelta(days=30), "ncep_gfs_global", ghost_loc.name)

        def obs_loader(loc):
            return make_series(ISSUE, 400) if loc.name == LOC.name else make_series(ISSUE, 10)

        summary = run_scoring(
            tmp_path,
            {LOC.name: LOC},
            sources={},
            obs_loader=obs_loader,
            now=ISSUE,
            min_pairs=2,
        )
        assert summary.errors and any("unknown location" in e for e in summary.errors)
        assert summary.skipped_no_new_obs >= 1
        assert summary.considered >= 1

    def test_scores_new_capture_via_loader(self, tmp_path):
        save_capture(tmp_path, make_series(ISSUE, 384), ISSUE, "dwd_icon_global", LOC.name)

        summary = run_scoring(
            tmp_path,
            {LOC.name: LOC},
            sources={},
            obs_loader=lambda loc: make_series(ISSUE, 384),
            now=ISSUE,
            min_pairs=24,
        )
        assert summary.considered == 1
        assert summary.updated == 1
        assert read_csv(tmp_path, HOURLY_CSV)["model"].iloc[0] == "dwd_icon_global"
