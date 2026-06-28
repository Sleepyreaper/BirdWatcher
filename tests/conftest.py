"""Pytest fixtures for BirdWatcher tests.

Everything runs against a temp DB + a Flask test client — no camera, no network.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from birdwatcher.config import Config
from birdwatcher.database import Database


@pytest.fixture
def cfg(tmp_path):
    """A Config pointed entirely at a temp dir, with a known ingest token."""
    c = Config()
    c.paths.db = str(tmp_path / "test.db")
    c.paths.captures = str(tmp_path / "captures")
    c.pipeline.ingest_token = "s3cret"
    c.pipeline.max_image_bytes = 1_000_000
    c.web.max_content_length = 2_000_000
    c.weather.enabled = False
    c.audio.birdnet_db = ""
    return c


@pytest.fixture
def db(cfg):
    d = Database(cfg.paths.db_path())
    yield d
    d.close()


@pytest.fixture
def client(cfg):
    from birdwatcher.web.app import create_app

    app = create_app(cfg)
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)
