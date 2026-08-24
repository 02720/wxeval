from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from wxeval.sources.base import (
    PRECIPITATION_COL,
    TEMPERATURE_COL,
    TIME_COL,
    SourceError,
    get_with_retry,
)

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


class ObsClient:
    def __init__(self, root: Path, session: Any = None, url: str = ARCHIVE_URL) -> None:
        self.root = Path(root)
        self.obs_dir = self.root / "observations"
        self.obs_dir.mkdir(parents=True, exist_ok=True)
        if session is None:
            import requests

            session = requests.Session()
        self.session = session
        self.url = url

    def _cache_path(self, name: str) -> Path:
        return self.obs_dir / f"{name}.csv.gz"

    def load(self, name: str) -> pd.DataFrame:
        path = self._cache_path(name)
        if not path.exists():
            return _empty_obs()
        df = pd.read_csv(path, index_col=TIME_COL, parse_dates=[TIME_COL])
        return _ensure_utc(df)

    def fetch_update(
        self,
        name: str,
        latitude: float,
        longitude: float,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        def do_get() -> dict[str, Any]:
            resp = self.session.get(
                self.url,
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "start_date": start,
                    "end_date": end,
                    "hourly": f"{TEMPERATURE_COL},{PRECIPITATION_COL}",
                    "timezone": "GMT",
                    "temperature_unit": "celsius",
                    "precipitation_unit": "mm",
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()

        payload = get_with_retry(do_get)
        fresh = parse_archive_payload(payload)
        cache = self.load(name)
        merged = pd.concat([cache, fresh]) if not fresh.empty else cache
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        _write_cache(merged, self._cache_path(name))
        return merged


def _empty_obs() -> pd.DataFrame:
    idx = pd.DatetimeIndex([], name=TIME_COL, tz="UTC")
    return pd.DataFrame(
        {
            TEMPERATURE_COL: pd.Series(dtype=float),
            PRECIPITATION_COL: pd.Series(dtype=float),
        },
        index=idx,
    )


def _ensure_utc(df: pd.DataFrame) -> pd.DataFrame:
    df.index = pd.DatetimeIndex(pd.to_datetime(df.index, utc=True), name=TIME_COL)
    return df


def _write_cache(df: pd.DataFrame, path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    df.to_csv(tmp, compression={"method": "gzip"})
    tmp.replace(path)


def parse_archive_payload(payload: dict[str, Any]) -> pd.DataFrame:
    if payload.get("error"):
        raise SourceError(f"archive error: {payload.get('reason')}")
    hourly = payload.get("hourly") or {}
    times_raw = hourly.get(TIME_COL) or []
    if len(times_raw) == 0:
        logger.warning("archive returned no rows")
        return _empty_obs()
    times = pd.to_datetime(times_raw, format="ISO8601", utc=True)
    return pd.DataFrame(
        {
            TEMPERATURE_COL: pd.to_numeric(
                pd.Series(hourly.get(TEMPERATURE_COL)), errors="coerce"
            ).to_numpy(),
            PRECIPITATION_COL: pd.to_numeric(
                pd.Series(hourly.get(PRECIPITATION_COL)), errors="coerce"
            ).to_numpy(),
        },
        index=pd.DatetimeIndex(times, name=TIME_COL),
    )
