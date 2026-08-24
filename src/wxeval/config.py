from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

BUCKETS: tuple[tuple[int, int, str], ...] = (
    (0, 24, "0-24h"),
    (24, 72, "24-72h"),
    (72, 168, "72-168h"),
    (168, 384, "168-384h"),
)

DEFAULT_MODELS = ["ecmwf_ifs", "ncep_gfs_global", "dwd_icon_global"]


@dataclass(frozen=True)
class Location:
    name: str
    latitude: float
    longitude: float
    timezone: str


@dataclass(frozen=True)
class Settings:
    models: list[str]
    locations: list[Location]
    min_pairs: int
    retention_days: int
    forecast_days: int


def _require(d: dict, key: str) -> Any:
    if key not in d:
        raise ValueError(f"missing required field: {key}")
    return d[key]


def parse_location(raw: dict) -> Location:
    name = str(_require(raw, "name"))
    lat = float(_require(raw, "latitude"))
    lon = float(_require(raw, "longitude"))
    tz = str(_require(raw, "timezone"))
    if not name:
        raise ValueError("location name must be non-empty")
    if not -90 <= lat <= 90 or math.isnan(lat):
        raise ValueError(f"latitude out of range for {name}: {lat}")
    if not -180 <= lon <= 180 or math.isnan(lon):
        raise ValueError(f"longitude out of range for {name}: {lon}")
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"invalid IANA timezone for {name}: {tz}") from None
    return Location(name=name, latitude=lat, longitude=lon, timezone=tz)


def load_settings(path: str | Path) -> Settings:
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    locs_raw = raw.get("locations")
    if not isinstance(locs_raw, list) or not locs_raw:
        raise ValueError("config must define a non-empty 'locations' list")
    locations = [parse_location(item) for item in locs_raw]
    names = [loc.name for loc in locations]
    if len(set(names)) != len(names):
        raise ValueError("duplicate location names in config")
    models = [str(m) for m in raw.get("models", DEFAULT_MODELS)]
    if not models:
        raise ValueError("'models' must be non-empty when provided")
    forecast_days = int(raw.get("forecast_days", 16))
    retention_days = int(raw.get("retention_days", 21))
    min_pairs = int(raw.get("min_pairs", 24))
    if forecast_days <= 0 or forecast_days > 16:
        raise ValueError(f"'forecast_days' must be in 1..16, got {forecast_days}")
    if retention_days < forecast_days + 5:
        raise ValueError(
            f"'retention_days' ({retention_days}) must exceed forecast_days+obs lag "
            f"(>= {forecast_days + 5})"
        )
    if min_pairs < 2:
        raise ValueError(f"'min_pairs' must be >= 2, got {min_pairs}")
    return Settings(
        models=models,
        locations=locations,
        min_pairs=min_pairs,
        retention_days=retention_days,
        forecast_days=forecast_days,
    )
