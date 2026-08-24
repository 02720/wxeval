from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import requests

from wxeval.sources.base import (
    PRECIPITATION_COL,
    TEMPERATURE_COL,
    TIME_COL,
    Forecast,
    SourceError,
    get_with_retry,
)

logger = logging.getLogger(__name__)

API_URL = "https://api.open-meteo.com/v1/forecast"

HOURLY_VARIABLES = [TEMPERATURE_COL, PRECIPITATION_COL]


class OpenMeteoForecastSource:
    def __init__(
        self,
        model: str,
        session: Any = None,
        url: str = API_URL,
        timeout: float = 30.0,
    ) -> None:
        self.model = model
        self.session = session or requests.Session()
        self.url = url
        self.timeout = timeout

    def _params(self, latitude: float, longitude: float, forecast_days: int) -> dict[str, Any]:
        return {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": ",".join(HOURLY_VARIABLES),
            "models": self.model,
            "forecast_days": forecast_days,
            "timezone": "GMT",
            "temperature_unit": "celsius",
            "precipitation_unit": "mm",
        }

    def fetch(self, latitude: float, longitude: float, forecast_days: int = 16) -> Forecast:
        params = self._params(latitude, longitude, forecast_days)

        def do_get() -> dict[str, Any]:
            resp = self.session.get(self.url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()

        payload = get_with_retry(do_get)
        return parse_forecast_payload(payload, self.model)


def parse_forecast_payload(payload: dict[str, Any], model: str) -> Forecast:
    if payload.get("error"):
        raise SourceError(f"open-meteo error for {model}: {payload.get('reason')}")
    hourly = payload.get("hourly")
    if not hourly or TIME_COL not in hourly:
        raise SourceError(f"missing hourly payload for {model}")
    times = pd.to_datetime(hourly[TIME_COL], format="ISO8601", utc=True)
    if len(times) == 0:
        raise SourceError(f"empty hourly series for {model}")
    frame = pd.DataFrame(
        {
            TEMPERATURE_COL: pd.to_numeric(
                pd.Series(hourly.get(TEMPERATURE_COL, [])), errors="coerce"
            ),
            PRECIPITATION_COL: pd.to_numeric(
                pd.Series(hourly.get(PRECIPITATION_COL, [])), errors="coerce"
            ),
        },
        index=pd.DatetimeIndex(times, name=TIME_COL),
    )
    issue = times[0]
    return Forecast(model=model, issue_utc=issue, frame=frame)
