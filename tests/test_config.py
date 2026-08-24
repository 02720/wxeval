import textwrap

import pytest

from wxeval.config import BUCKETS, load_settings, parse_location


def test_buckets_are_left_closed_right_open_and_ordered():
    for (lo, hi, label), (lo2, _hi2, _) in zip(BUCKETS, BUCKETS[1:], strict=False):
        assert hi == lo2
        assert lo < hi
        assert "/" not in label


def test_load_settings_default_config(tmp_path):
    cfg = tmp_path / "locations.yaml"
    cfg.write_text(
        textwrap.dedent(
            """\
            models:
              - ecmwf_ifs
            locations:
              - name: 梧州
                latitude: 23.477
                longitude: 111.279
                timezone: Asia/Shanghai
              - name: 万宁
                latitude: 18.795
                longitude: 110.389
                timezone: Asia/Shanghai
            """
        ),
        encoding="utf-8",
    )
    s = load_settings(cfg)
    assert [loc.name for loc in s.locations] == ["梧州", "万宁"]
    assert s.models == ["ecmwf_ifs"]
    assert s.min_pairs == 24


def test_missing_field_raises(tmp_path):
    with pytest.raises(ValueError, match="latitude"):
        parse_location({"name": "x", "longitude": 1.0, "timezone": "UTC"})


def test_latitude_out_of_range():
    with pytest.raises(ValueError, match="latitude"):
        parse_location({"name": "x", "latitude": 91.0, "longitude": 1.0, "timezone": "UTC"})


def test_duplicate_names_raise(tmp_path):
    cfg = tmp_path / "dup.yaml"
    cfg.write_text(
        textwrap.dedent(
            """\
            locations:
              - name: a
                latitude: 0
                longitude: 0
                timezone: UTC
              - name: a
                latitude: 1
                longitude: 1
                timezone: UTC
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_settings(cfg)


def test_empty_locations_raise(tmp_path):
    cfg = tmp_path / "empty.yaml"
    cfg.write_text("locations: []\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(cfg)


def test_invalid_timezone_rejected():
    with pytest.raises(ValueError, match="timezone"):
        parse_location({"name": "x", "latitude": 0, "longitude": 0, "timezone": "Mars/Olympus"})


def test_forecast_days_validation(tmp_path):
    cfg = tmp_path / "c.yaml"
    base = (
        "locations:\n  - name: a\n    latitude: 0\n    longitude: 0\n    timezone: UTC\n"
    )
    cfg.write_text(base + "forecast_days: 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="forecast_days"):
        load_settings(cfg)
    cfg.write_text(base + "forecast_days: 20\n", encoding="utf-8")
    with pytest.raises(ValueError, match="forecast_days"):
        load_settings(cfg)


def test_retention_must_exceed_horizon_plus_lag(tmp_path):
    cfg = tmp_path / "r.yaml"
    base = (
        "locations:\n  - name: a\n    latitude: 0\n    longitude: 0\n    timezone: UTC\n"
        "forecast_days: 16\n"
    )
    cfg.write_text(base + "retention_days: 18\n", encoding="utf-8")
    with pytest.raises(ValueError, match="retention_days"):
        load_settings(cfg)
