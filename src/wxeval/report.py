from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

CJK_FONT_CANDIDATES = [
    "Noto Sans SC",
    "Noto Sans CJK SC",
    "WenQuanYi Zen Hei",
    "Source Han Sans SC",
    "Microsoft JhengHei",
    "PingFang SC",
    "SimHei",
]


def _configure_fonts() -> None:
    available = {f.name for f in __import__("matplotlib").font_manager.fontManager.ttflist}
    for candidate in CJK_FONT_CANDIDATES:
        if candidate in available:
            plt.rcParams["font.sans-serif"] = [candidate, "DejaVu Sans"]
            break
    plt.rcParams["axes.unicode_minus"] = False


_configure_fonts()

BUCKET_ORDER = ["0-24h", "24-72h", "72-168h", "168-384h"]

COLORS = {"ecmwf_ifs": "#1f77b4", "ncep_gfs_global": "#ff7f0e", "dwd_icon_global": "#2ca02c"}


def _bar_chart(pivoted: pd.DataFrame, title: str, ylabel: str, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=120)
    order = [b for b in BUCKET_ORDER if b in pivoted.index]
    models = list(pivoted.columns)
    width = 0.8 / max(len(models), 1)
    x = range(len(order))
    for i, model in enumerate(models):
        values = [pivoted.loc[b, model] if b in pivoted.index else float("nan") for b in order]
        ax.bar([xi + i * width for xi in x], values, width, label=model)
    ax.set_xticks([xi + width * (len(models) - 1) / 2 for xi in x])
    ax.set_xticklabels(order)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


def _trend_chart(df: pd.DataFrame, out: Path, days: int = 30) -> None:
    sub = df[df["bucket"] == BUCKET_ORDER[0]].dropna(subset=["temp_mae"]).copy()
    sub["issue"] = pd.to_datetime(sub["issue_utc"], utc=True, format="mixed")
    cutoff = sub["issue"].max() - pd.Timedelta(days=days)
    sub = sub[sub["issue"] >= cutoff]
    grouped = (
        sub.groupby([sub["issue"].dt.date, "model"], observed=True)["temp_mae"]
        .mean()
        .unstack("model")
    )
    fig, ax = plt.subplots(figsize=(9, 4.5), dpi=120)
    for model in grouped.columns:
        ax.plot(grouped.index, grouped[model], marker="o", markersize=3, label=model)
    ax.set_ylabel("日级温度MAE (℃)")
    ax.set_title(f"近{days}天 温度MAE趋势 (0-24h桶, 跨城市均值)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


def make_charts(hourly: pd.DataFrame, daily: pd.DataFrame, charts_dir: Path) -> list[Path]:
    charts_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    if not hourly.empty and "temp_mae" in hourly:
        piv = (
            hourly.dropna(subset=["temp_mae"])
            .groupby(["bucket", "model"], observed=True)["temp_mae"]
            .mean()
            .unstack("model")
        )
        p = charts_dir / "temp_mae_by_bucket.png"
        _bar_chart(piv, "各模式温度MAE对比 (小时级, 跨城市均值)", "MAE (℃)", p)
        written.append(p)

    if not daily.empty and "precip_ts" in daily:
        piv = (
            daily.dropna(subset=["precip_ts"])
            .groupby(["bucket", "model"], observed=True)["precip_ts"]
            .mean()
            .unstack("model")
        )
        p = charts_dir / "precip_ts_by_bucket.png"
        _bar_chart(piv, "各模式降水TS评分对比 (日累计)", "TS", p)
        written.append(p)

    if not daily.empty and "temp_mae" in daily:
        p = charts_dir / "temp_mae_trend.png"
        _trend_chart(daily, p)
        written.append(p)
    return written


def _markdown_table(df: pd.DataFrame, floatfmt: str = ".2f") -> str:
    if df.empty:
        return "_暂无数据_\n"
    header = "| " + " | ".join([""] + [str(c) for c in df.columns]) + " |"
    sep = "|" + "---|" * (len(df.columns) + 1)
    lines = []
    for idx, row in df.iterrows():
        cells = [str(idx)]
        for c in df.columns:
            v = row[c]
            if pd.isna(v):
                cells.append("-")
            elif isinstance(v, float):
                cells.append(format(v, floatfmt))
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, sep] + lines) + "\n"


def build_report(root: Path, report_path: Path | None = None) -> Path:
    root = Path(root)
    results = root / "results"
    hourly = (
        pd.read_csv(results / "hourly.csv") if (results / "hourly.csv").exists() else pd.DataFrame()
    )
    daily = (
        pd.read_csv(results / "daily.csv") if (results / "daily.csv").exists() else pd.DataFrame()
    )

    charts_dir = root.parent / "reports" / "charts"
    chart_files = make_charts(hourly, daily, charts_dir)

    report_path = report_path or (root.parent / "reports" / "latest.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)

    parts: list[str] = ["# 天气预报准确度评测报告\n"]
    parts.append(f"_生成时间: {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}_\n")

    if hourly.empty and daily.empty:
        parts.append("\n暂无评分数据。系统需要运行数天后积累足够的观测配对样本。\n")

    if not hourly.empty and "temp_mae" in hourly:
        piv = (
            hourly.dropna(subset=["temp_mae"])
            .groupby(["bucket", "model"], observed=True)["temp_mae"]
            .mean()
            .unstack("model")
        )
        piv = piv.reindex(BUCKET_ORDER).dropna(how="all")
        parts.append("\n## 小时级温度MAE (跨城市均值, ℃)\n\n")
        parts.append(_markdown_table(piv))

    if not daily.empty and "precip_ts" in daily:
        piv = (
            daily.dropna(subset=["precip_ts"])
            .groupby(["bucket", "model"], observed=True)["precip_ts"]
            .mean()
            .unstack("model")
        )
        piv = piv.reindex(BUCKET_ORDER).dropna(how="all")
        parts.append("\n## 日累计降水TS评分 (跨城市均值)\n\n")
        parts.append(_markdown_table(piv))

    if not hourly.empty and "location" in hourly and "temp_acc2" in hourly:
        piv = (
            hourly.dropna(subset=["temp_acc2"])
            .groupby(["location", "model"], observed=True)["temp_acc2"]
            .mean()
            .unstack("model")
        )
        parts.append("\n## 分城市 ±2℃准确率 (小时级, 全部lead平均, %)\n\n")
        parts.append(_markdown_table(piv, ".1f"))

    for name in chart_files:
        parts.append(f"\n![{name.stem}](charts/{name.name})\n")

    errors_file = results / "lastrun_errors.json"
    if errors_file.exists():
        import json

        try:
            errors = json.loads(errors_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            errors = []
        if errors:
            parts.append("\n## 最近运行错误\n\n")
            parts.extend(f"- {e}\n" for e in errors[:20])

    report_path.write_text("".join(parts), encoding="utf-8")
    return report_path
