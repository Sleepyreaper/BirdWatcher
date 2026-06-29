from __future__ import annotations

from pathlib import Path

import pytest

from birdwatcher.config import Config
from birdwatcher.web.app import create_app


@pytest.fixture()
def cfg(tmp_path: Path) -> Config:
    c = Config()
    c.paths.db = str(tmp_path / "birdwatcher.db")
    c.paths.captures = str(tmp_path / "captures")
    c.pipeline.ingest_token = "secret-token"
    c.weather.enabled = False
    return c


@pytest.fixture()
def app(cfg: Config):
    return create_app(cfg)


@pytest.fixture()
def client(app):
    return app.test_client()
