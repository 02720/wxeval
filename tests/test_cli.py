from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from wxeval.cli import main, run_pipeline
from wxeval.config import Location, Settings


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        models=["ecmwf_ifs", "dwd_icon_global"],
        locations=[
            Location(name="梧州", latitude=23.477, longitude=111.279, timezone="Asia/Shanghai"),
            Location(name="万宁", latitude=18.795, longitude=110.389, timezone="Asia/Shanghai"),
        ],
        min_pairs=24,
        retention_days=21,
        forecast_days=16,
    )


def _issue_anchor() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC").floor("h") - pd.Timedelta(hours=48)


class FakeSource:
    def __init__(self, model: str) -> None:
        self.model = model

    def fetch(self, latitude: float, longitude: float, forecast_days: int = 16):
        from wxeval.sources.base import Forecast

        issue = _issue_anchor()
        idx = pd.date_range(
            issue, periods=min(forecast_days * 24, 384), freq="h", tz="UTC", name="time"
        )
        i = np.arange(len(idx))
        frame = pd.DataFrame(
            {
                "temperature_2m": 20 + (latitude % 5) + np.sin(i / 24 * np.pi),
                "precipitation": np.where(i % 12 == 0, 2.0, 0.0),
            },
            index=idx,
        )
        return Forecast(model=self.model, issue_utc=issue, frame=frame)


class FakeObs:
    def __init__(self, root: Path) -> None:
        self.root = root

    def fetch_update(self, name, latitude, longitude, start, end):
        idx = pd.date_range(_issue_anchor(), periods=200, freq="h", tz="UTC", name="time")
        i = np.arange(len(idx))
        return pd.DataFrame(
            {
                "temperature_2m": 20 + (latitude % 5) + np.sin(i / 24 * np.pi),
                "precipitation": np.where(i % 12 == 0, 2.0, 0.0),
            },
            index=idx,
        )


def fake_factories(settings: Settings):
    return (
        lambda s: {m: FakeSource(m) for m in s.models},
        lambda root: FakeObs(root),
    )


def test_run_pipeline_end_to_end(tmp_path, capsys):
    settings = make_settings(tmp_path)
    root = tmp_path / "data"
    src_f, obs_f = fake_factories(settings)

    result = run_pipeline(settings, root, source_factory=src_f, obs_client_factory=obs_f)
    assert not result.all_failed
    captures = list((root / "forecasts").rglob("*.csv.gz"))
    assert len(captures) == 4
    hourly = pd.read_csv(root / "results" / "hourly.csv")
    assert set(hourly["model"]) == {"ecmwf_ifs", "dwd_icon_global"}
    assert {"梧州", "万宁"} <= set(hourly["location"])
    assert (tmp_path / "reports" / "latest.md").exists()
    assert (root / "results" / "lastrun_errors.json").exists()
    assert (tmp_path / "reports" / "latest.md").exists()

    result2 = run_pipeline(settings, root, source_factory=src_f, obs_client_factory=obs_f)
    assert not result2.all_failed
    hourly2 = pd.read_csv(root / "results" / "hourly.csv")
    assert len(hourly2) == len(hourly)


def test_main_run_with_fake_config(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    data_root = tmp_path / "data"
    config_dir.mkdir()
    config = config_dir / "locations.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "models": ["ecmwf_ifs"],
                "locations": [
                    {
                        "name": "梧州",
                        "latitude": 23.477,
                        "longitude": 111.279,
                        "timezone": "Asia/Shanghai",
                    }
                ],
                "min_pairs": 24,
                "retention_days": 21,
                "forecast_days": 16,
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("wxeval.cli.make_sources", lambda s: {"ecmwf_ifs": FakeSource("ecmwf_ifs")})
    monkeypatch.setattr("wxeval.cli.ObsClient", FakeObs)

    rc = main(["--config", str(config), "--data-root", str(data_root), "run"])
    assert rc == 0
    assert (tmp_path / "reports" / "latest.md").exists()


def test_main_backfill_reserved(tmp_path, capsys):
    config = tmp_path / "cfg.yaml"
    config.write_text(
        "locations:\n  - name: a\n    latitude: 0\n    longitude: 0\n    timezone: UTC\n",
        encoding="utf-8",
    )
    rc = main(["--config", str(config), "--data-root", str(tmp_path / "d"), "backfill"])
    assert rc == 2
    assert "reserved" in capsys.readouterr().err
