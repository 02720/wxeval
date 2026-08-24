from __future__ import annotations

from pathlib import Path

import pandas as pd

from wxeval.report import build_report


def write_results(root: Path, name: str, rows: list[dict]) -> None:
    path = root / "results"
    path.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path / name, index=False)


def sample_hourly() -> list[dict]:
    rows = []
    for model in ["ecmwf_ifs", "ncep_gfs_global"]:
        for bucket in ["0-24h", "24-72h"]:
            for loc in ["梧州", "万宁"]:
                rows.append(
                    {
                        "issue_utc": "2026-08-20T00:00:00Z",
                        "model": model,
                        "location": loc,
                        "bucket": bucket,
                        "n_pairs": 48,
                        "temp_mae": 1.5 if model == "ecmwf_ifs" else 2.0,
                        "temp_acc2": 80.0,
                    }
                )
    return rows


def sample_daily() -> list[dict]:
    rows = []
    for day in range(5):
        for model in ["ecmwf_ifs", "dwd_icon_global"]:
            rows.append(
                {
                    "issue_utc": f"2026-08-{10 + day:02d}T00:00:00Z",
                    "model": model,
                    "location": "梧州",
                    "bucket": "0-24h",
                    "n_pairs": 24,
                    "temp_mae": 1.0 + day * 0.1,
                    "precip_ts": 0.4,
                }
            )
    return rows


def test_build_report_with_data(tmp_path):
    data_root = tmp_path / "data"
    write_results(data_root, "hourly.csv", sample_hourly())
    write_results(data_root, "daily.csv", sample_daily())

    report = build_report(data_root)
    assert report.exists()
    text = report.read_text(encoding="utf-8")
    assert "# 天气预报准确度评测报告" in text
    assert "ecmwf_ifs" in text
    assert "小时级温度MAE" in text
    assert "降水TS评分" in text
    assert "±2℃准确率" in text

    charts_dir = tmp_path / "reports" / "charts"
    pngs = sorted(p.name for p in charts_dir.glob("*.png"))
    assert pngs == ["precip_ts_by_bucket.png", "temp_mae_by_bucket.png", "temp_mae_trend.png"]
    for p in charts_dir.glob("*.png"):
        assert p.stat().st_size > 500


def test_build_report_empty(tmp_path):
    data_root = tmp_path / "data"
    report = build_report(data_root)
    text = report.read_text(encoding="utf-8")
    assert "暂无评分数据" in text


def test_build_report_with_errors_section(tmp_path):
    data_root = tmp_path / "data"
    results = data_root / "results"
    results.mkdir(parents=True)
    (results / "lastrun_errors.json").write_text('["boom one", "boom two"]', encoding="utf-8")
    report = build_report(data_root)
    text = report.read_text(encoding="utf-8")
    assert "最近运行错误" in text
    assert "boom one" in text
