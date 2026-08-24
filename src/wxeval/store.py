from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from wxeval.sources.base import PRECIPITATION_COL, TEMPERATURE_COL, TIME_COL

logger = logging.getLogger(__name__)

STATE_FILENAME = "state.json"
CAPTURES_SUBDIR = "forecasts"
DONE_SENTINEL = "done"
PRUNE_GRACE_DAYS = 7


@dataclass(frozen=True)
class Capture:
    issue_utc: pd.Timestamp
    model: str
    location: str
    path: Path


def capture_key(issue_utc: pd.Timestamp, model: str, location: str) -> str:
    return f"{issue_utc.strftime('%Y%m%dT%H%M')}Z|{model}|{location}"


def captures_root(root: Path) -> Path:
    return Path(root) / CAPTURES_SUBDIR


def results_root(root: Path) -> Path:
    return Path(root) / "results"


def _capture_path(root: Path, issue_utc: pd.Timestamp, model: str, location: str) -> Path:
    return captures_root(root) / issue_utc.strftime("%Y%m%dT%H") / model / f"{location}.csv.gz"


def save_capture(
    root: Path,
    frame: pd.DataFrame,
    issue_utc: pd.Timestamp,
    model: str,
    location: str,
) -> bool:
    path = _capture_path(Path(root), issue_utc, model, location)
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    frame.to_csv(tmp, compression={"method": "gzip"}, index_label=TIME_COL)
    tmp.replace(path)
    return True


def list_captures(root: Path) -> list[Capture]:
    root = Path(root)
    out: list[Capture] = []
    base = captures_root(root)
    if not base.exists():
        return out
    for issue_dir in sorted(base.iterdir()):
        if not issue_dir.is_dir():
            continue
        try:
            issue_utc = pd.to_datetime(issue_dir.name, format="%Y%m%dT%H", utc=True)
        except ValueError:
            continue
        for model_dir in sorted(issue_dir.iterdir()):
            if not model_dir.is_dir():
                continue
            for loc_file in sorted(model_dir.glob("*.csv.gz")):
                out.append(
                    Capture(
                        issue_utc=issue_utc,
                        model=model_dir.name,
                        location=loc_file.stem.removesuffix(".csv"),
                        path=loc_file,
                    )
                )
    return out


def load_capture(capture: Capture) -> pd.DataFrame:
    df = pd.read_csv(capture.path, index_col=TIME_COL, parse_dates=[TIME_COL])
    df.index = pd.DatetimeIndex(pd.to_datetime(df.index, utc=True), name=TIME_COL)
    return df


def state_path(root: Path) -> Path:
    return results_root(root) / STATE_FILENAME


def load_state(root: Path) -> dict[str, str]:
    path = state_path(Path(root))
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError:
        logger.warning("state.json corrupted, starting fresh")
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def save_state(root: Path, state: dict[str, str]) -> None:
    path = state_path(Path(root))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )
    tmp.replace(path)


def prune(root: Path, state: dict[str, str], *, now: pd.Timestamp, retention_days: int) -> int:
    root = Path(root)
    cutoff = now - pd.Timedelta(days=retention_days)
    hard_cutoff = now - pd.Timedelta(days=retention_days + PRUNE_GRACE_DAYS)
    removed = 0
    for capture in list_captures(root):
        key = capture_key(capture.issue_utc, capture.model, capture.location)
        done = state.get(key) == DONE_SENTINEL
        past_retention = capture.issue_utc <= cutoff
        past_hard = capture.issue_utc <= hard_cutoff
        if not (past_hard or (done and past_retention)):
            continue
        capture.path.unlink(missing_ok=True)
        state.pop(key, None)
        removed += 1
    base = captures_root(root)
    if base.exists():
        for issue_dir in sorted(base.iterdir()):
            if issue_dir.is_dir() and not any(issue_dir.iterdir()):
                issue_dir.rmdir()
        for model_dir in base.glob("*/*"):
            if model_dir.is_dir() and not any(model_dir.iterdir()):
                model_dir.rmdir()
    return removed


__all__ = [
    "Capture",
    "PRECIPITATION_COL",
    "TEMPERATURE_COL",
    "capture_key",
    "captures_root",
    "list_captures",
    "load_capture",
    "load_state",
    "prune",
    "results_root",
    "save_capture",
    "save_state",
    "state_path",
]
