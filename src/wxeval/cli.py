from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

import wxeval
from wxeval.config import Location, Settings, load_settings
from wxeval.evaluate import run_scoring
from wxeval.observations import ObsClient
from wxeval.report import build_report
from wxeval.sources.base import ForecastSource, SourceError
from wxeval.sources.open_meteo import OpenMeteoForecastSource
from wxeval.store import list_captures, load_state, prune, save_capture, save_state

OBS_LOOKBACK_DAYS = 12
OBS_LAG_DAYS = 2


@dataclass
class StepResult:
    ok: bool
    detail: str = ""
    errors: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    capture: StepResult
    score: StepResult
    report: StepResult

    @property
    def all_failed(self) -> bool:
        return not (self.capture.ok or self.score.ok or self.report.ok)


def _default_config() -> Path:
    return Path("config/locations.yaml")


def _default_data_root(config_path: Path) -> Path:
    return config_path.parent.parent / "data"


def make_sources(settings: Settings) -> dict[str, ForecastSource]:
    return {model: OpenMeteoForecastSource(model) for model in settings.models}


def do_capture(
    settings: Settings,
    root: Path,
    sources: dict[str, ForecastSource],
    now: pd.Timestamp | None = None,
) -> StepResult:
    now = now or pd.Timestamp.now(tz="UTC")
    stored, skipped, errors = 0, 0, []
    for model, source in sources.items():
        for loc in settings.locations:
            try:
                fc = source.fetch(loc.latitude, loc.longitude, forecast_days=settings.forecast_days)
                if save_capture(root, fc.frame, fc.issue_utc, model, loc.name):
                    stored += 1
                else:
                    skipped += 1
            except Exception as exc:
                errors.append(f"capture {model}/{loc.name}: {exc}")
    detail = f"stored={stored} skipped={skipped} captures_total={len(list_captures(root))}"
    return StepResult(ok=stored > 0 or not errors, detail=detail, errors=errors)


def do_score(
    settings: Settings,
    root: Path,
    obs_client: ObsClient,
    now: pd.Timestamp | None = None,
) -> StepResult:
    now = now or pd.Timestamp.now(tz="UTC")
    start = (now - pd.Timedelta(days=OBS_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    end = (now - pd.Timedelta(days=OBS_LAG_DAYS)).strftime("%Y-%m-%d")

    def obs_loader(loc: Location) -> pd.DataFrame:
        return obs_client.fetch_update(loc.name, loc.latitude, loc.longitude, start, end)

    by_name = {loc.name: loc for loc in settings.locations}
    summary = run_scoring(
        root,
        by_name,
        sources={},
        obs_loader=obs_loader,
        now=now,
        min_pairs=settings.min_pairs,
    )
    detail = (
        f"considered={summary.considered} updated={summary.updated} "
        f"skipped={summary.skipped_no_new_obs}"
    )
    return StepResult(
        ok=not summary.errors or summary.updated > 0, detail=detail, errors=summary.errors
    )


def do_report(settings: Settings, root: Path) -> StepResult:
    errors: list[str] = []
    try:
        path = build_report(root)
        detail = f"report={path}"
    except Exception as exc:
        errors.append(f"report: {exc}")
        detail = "report failed"
        return StepResult(ok=False, detail=detail, errors=errors)
    return StepResult(ok=True, detail=detail, errors=errors)


def write_errors_file(root: Path, result: RunResult) -> None:
    errors_file = root / "results" / "lastrun_errors.json"
    errors_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "capture": {
            "ok": result.capture.ok,
            "detail": result.capture.detail,
            "errors": result.capture.errors,
        },
        "score": {
            "ok": result.score.ok,
            "detail": result.score.detail,
            "errors": result.score.errors,
        },
        "report": {
            "ok": result.report.ok,
            "detail": result.report.detail,
            "errors": result.report.errors,
        },
    }
    tmp = errors_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(errors_file)


def run_pipeline(
    settings: Settings,
    root: Path,
    *,
    source_factory: Callable[[Settings], dict[str, ForecastSource]] = make_sources,
    obs_client_factory: Callable[[Path], ObsClient] = lambda root: ObsClient(root),
    now: pd.Timestamp | None = None,
) -> RunResult:
    now = now or pd.Timestamp.now(tz="UTC")
    root.mkdir(parents=True, exist_ok=True)

    capture_result = do_capture(settings, root, source_factory(settings), now=now)
    obs_client = obs_client_factory(root)
    score_result = do_score(settings, root, obs_client, now=now)

    state = load_state(root)
    removed = prune(root, state, now=now, retention_days=settings.retention_days)
    save_state(root, state)

    report_result = do_report(settings, root)
    if removed:
        score_result.detail += f" pruned={removed}"
    result = RunResult(capture_result, score_result, report_result)
    write_errors_file(root, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wxeval",
        description=f"Weather forecast accuracy evaluation v{wxeval.__version__}",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_command(name: str, help_text: str) -> argparse.ArgumentParser:
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--config", type=Path, default=_default_config())
        cmd.add_argument("--data-root", type=Path, default=None)
        return cmd

    add_command("run", "capture + score + prune + report")
    add_command("capture", "fetch latest forecasts only")
    add_command("score", "score stored forecasts against observations only")
    add_command("report", "rebuild markdown report only")
    backfill = add_command("backfill", "reserved for future historical backtesting")
    backfill.add_argument("--start", required=False)
    args = parser.parse_args(argv)

    settings = load_settings(args.config)
    root = args.data_root or _default_data_root(args.config.resolve())

    if args.command == "backfill":
        print("backfill is reserved for a future release", file=sys.stderr)
        return 2

    if args.command == "run":
        result = run_pipeline(settings, root)
        print(f"[capture] {result.capture.detail}")
        print(f"[score]   {result.score.detail}")
        print(f"[report]  {result.report.detail}")
        all_errors = result.capture.errors + result.score.errors + result.report.errors
        for err in all_errors[:20]:
            print(f"[error]   {err}", file=sys.stderr)
        return 1 if result.all_failed else 0

    if args.command == "capture":
        result = do_capture(settings, root, make_sources(settings))
        print(result.detail)
        for err in result.errors[:20]:
            print(f"[error] {err}", file=sys.stderr)
        return 0 if result.ok else 1

    if args.command == "score":
        result = do_score(settings, root, ObsClient(root))
        print(result.detail)
        for err in result.errors[:20]:
            print(f"[error] {err}", file=sys.stderr)
        return 0 if result.ok else 1

    if args.command == "report":
        result = do_report(settings, root)
        print(result.detail)
        return 0 if result.ok else 1

    parser.error(f"unknown command {args.command}")
    return 2


__all__ = ["SourceError", "main", "run_pipeline"]
