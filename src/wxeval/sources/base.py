from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

import pandas as pd

T = TypeVar("T")

TEMPERATURE_COL = "temperature_2m"
PRECIPITATION_COL = "precipitation"
TIME_COL = "time"


class SourceError(Exception):
    pass


@dataclass
class Forecast:
    model: str
    issue_utc: pd.Timestamp
    frame: pd.DataFrame

    @property
    def last_valid_utc(self) -> pd.Timestamp:
        return self.frame.index.max()


class ForecastSource(Protocol):
    model: str

    def fetch(self, latitude: float, longitude: float, forecast_days: int = 16) -> Forecast: ...


def get_with_retry(
    fn: Callable[[], T],
    *,
    retries: int = 3,
    base_delay: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt < retries - 1:
                sleep(base_delay * (2**attempt))
    raise SourceError(f"request failed after {retries} attempts") from last_exc
