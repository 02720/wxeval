from __future__ import annotations

from pathlib import Path

import pytest

from wxeval.cli import run_pipeline
from wxeval.config import load_settings

pytestmark = pytest.mark.network


def test_real_open_meteo_pipeline_smoke(tmp_path: Path):
    config = Path(__file__).parents[1] / "config" / "locations.yaml"
    settings = load_settings(config)
    root = tmp_path / "data"

    result = run_pipeline(settings, root)
    assert not result.all_failed, (
        f"capture errors={result.capture.errors} score errors={result.score.errors}"
    )
    captures = list((root / "forecasts").rglob("*.csv.gz"))
    assert len(captures) >= len(settings.models) * len(settings.locations)
    assert (tmp_path / "reports" / "latest.md").exists()
