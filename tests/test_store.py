from __future__ import annotations

import pandas as pd

from wxeval.store import (
    capture_key,
    list_captures,
    load_capture,
    load_state,
    prune,
    save_capture,
    save_state,
)


def make_frame(start: str = "2026-08-01", periods: int = 48) -> pd.DataFrame:
    idx = pd.date_range(start, periods=periods, freq="h", tz="UTC", name="time")
    return pd.DataFrame(
        {
            "temperature_2m": [float(i % 5) for i in range(periods)],
            "precipitation": [0.0] * periods,
        },
        index=idx,
    )


def test_capture_roundtrip_and_dedupe(tmp_path):
    issue = pd.Timestamp("2026-08-24T00:00", tz="UTC")
    df = make_frame()
    assert save_capture(tmp_path, df, issue, "ecmwf_ifs", "梧州") is True
    assert save_capture(tmp_path, df, issue, "ecmwf_ifs", "梧州") is False
    captures = list_captures(tmp_path)
    assert len(captures) == 1
    cap = captures[0]
    assert cap.model == "ecmwf_ifs"
    assert cap.location == "梧州"
    loaded = load_capture(cap)
    pd.testing.assert_frame_equal(loaded, df, check_freq=False)
    expected_key = "20260824T0000Z|ecmwf_ifs|梧州"
    assert capture_key(issue, "ecmwf_ifs", "梧州") == expected_key


def test_list_captures_multiple(tmp_path):
    issue_a = pd.Timestamp("2026-08-24T00:00", tz="UTC")
    issue_b = pd.Timestamp("2026-08-24T06:00", tz="UTC")
    save_capture(tmp_path, make_frame(), issue_a, "ecmwf_ifs", "a")
    save_capture(tmp_path, make_frame(), issue_a, "dwd_icon_global", "b")
    save_capture(tmp_path, make_frame(), issue_b, "ecmwf_ifs", "a")
    caps = list_captures(tmp_path)
    assert {(c.issue_utc.strftime("%H"), c.model, c.location) for c in caps} == {
        ("00", "ecmwf_ifs", "a"),
        ("00", "dwd_icon_global", "b"),
        ("06", "ecmwf_ifs", "a"),
    }


def test_prune_removes_only_expired_and_syncs_state(tmp_path):
    now = pd.Timestamp("2026-08-24T12:00", tz="UTC")
    old_issue = now - pd.Timedelta(days=30)
    fresh_issue = now - pd.Timedelta(days=2)
    save_capture(tmp_path, make_frame(), old_issue, "ecmwf_ifs", "a")
    save_capture(tmp_path, make_frame(), fresh_issue, "ecmwf_ifs", "a")
    state = {
        capture_key(old_issue, "ecmwf_ifs", "a"): "2026-08-10T00:00:00+00:00",
        capture_key(fresh_issue, "ecmwf_ifs", "a"): "2026-08-20T00:00:00+00:00",
    }
    removed = prune(tmp_path, state, now=now, retention_days=21)
    assert removed == 1
    remaining = list_captures(tmp_path)
    assert len(remaining) == 1
    assert remaining[0].issue_utc == fresh_issue
    assert set(state) == {capture_key(fresh_issue, "ecmwf_ifs", "a")}


def test_state_roundtrip(tmp_path):
    state = {"20260824T0000Z|m|l": "2026-08-25T00:00:00+00:00"}
    save_state(tmp_path, state)
    assert load_state(tmp_path) == state


def test_load_state_corrupted_json_returns_empty(tmp_path):
    from wxeval.store import state_path

    path = state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    assert load_state(tmp_path) == {}


def test_prune_spares_undone_captures_within_grace(tmp_path):
    from wxeval.store import DONE_SENTINEL
    from wxeval.store import capture_key as ck

    now = pd.Timestamp("2026-08-24T12:00", tz="UTC")
    stuck_issue = now - pd.Timedelta(days=23)
    save_capture(tmp_path, make_frame(), stuck_issue, "ecmwf_ifs", "a")
    done_issue = now - pd.Timedelta(days=23)
    save_capture(tmp_path, make_frame(), done_issue, "dwd_icon_global", "a")
    state = {
        ck(stuck_issue, "ecmwf_ifs", "a"): "2026-08-05T00:00:00Z",
        ck(done_issue, "dwd_icon_global", "a"): DONE_SENTINEL,
    }
    removed = prune(tmp_path, state, now=now, retention_days=21)
    assert removed == 1
    remaining = {c.model for c in list_captures(tmp_path)}
    assert remaining == {"ecmwf_ifs"}
    assert ck(stuck_issue, "ecmwf_ifs", "a") in state


def test_prune_hard_cutoff_removes_even_undone(tmp_path):
    from wxeval.store import capture_key as ck

    now = pd.Timestamp("2026-08-24T12:00", tz="UTC")
    ancient = now - pd.Timedelta(days=40)
    save_capture(tmp_path, make_frame(), ancient, "ecmwf_ifs", "a")
    state = {ck(ancient, "ecmwf_ifs", "a"): "2026-08-01T00:00:00Z"}
    removed = prune(tmp_path, state, now=now, retention_days=21)
    assert removed == 1
    assert list_captures(tmp_path) == []
    assert state == {}
