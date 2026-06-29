from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from birdwatcher.config import Config
from birdwatcher.pipeline import Pipeline, _Visit


class _FakeDB:
    def __init__(self):
        self.rows = []

    def add_visit(self, **kwargs):
        self.rows.append(kwargs)

    def close(self):
        pass


class _FakeCamera:
    def release(self):
        pass


class _FakeDetector:
    def detect(self, frame):
        return []


class _GoodClassifier:
    def classify(self, crop):
        return SimpleNamespace(species="Northern Cardinal", confidence=0.9)


class _BoomClassifier:
    def classify(self, crop):
        raise RuntimeError("boom")


@pytest.fixture()
def pipe(monkeypatch, tmp_path):
    cfg = Config()
    cfg.paths.db = str(tmp_path / "birdwatcher.db")
    cfg.paths.captures = str(tmp_path / "captures")
    monkeypatch.setattr("birdwatcher.pipeline.Database", lambda path: _FakeDB())
    monkeypatch.setattr("birdwatcher.pipeline.RTSPCamera", lambda *a, **k: _FakeCamera())
    monkeypatch.setattr("birdwatcher.pipeline.BirdDetector", lambda *a, **k: _FakeDetector())
    monkeypatch.setattr("birdwatcher.pipeline.build_classifier", lambda cfg: _GoodClassifier())
    return Pipeline(cfg)


def _visit():
    ts = datetime(2026, 6, 29, 8, 0, 0)
    return _Visit((0, 0, 10, 10), ts, ts, 3, 1.0, object(), 0.8)


def test_record_classifier_failure_does_not_raise(pipe):
    pipe.classifier = _BoomClassifier()
    pipe._record(_visit())
    assert pipe.db.rows == []


def test_record_save_failure_does_not_raise(pipe, monkeypatch):
    monkeypatch.setattr(pipe, "_save_crop", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk")))
    pipe._record(_visit())
    assert pipe.db.rows == []


def test_post_visit_network_failure_is_swallowed(pipe, monkeypatch):
    import numpy as np

    # Real crop so cv2.imencode succeeds and we actually exercise the network
    # path this test is about (a sentinel object would fail before urlopen).
    ts = datetime(2026, 6, 29, 8, 0, 0)
    visit = _Visit((0, 0, 10, 10), ts, ts, 3, 1.0, np.zeros((10, 10, 3), dtype=np.uint8), 0.8)
    pipe.cfg.pipeline.ingest_url = "http://example.test/api/ingest"
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    pipe._post_visit(visit, "Northern Cardinal", 0.9)


def test_classifier_init_falls_back_to_stub(monkeypatch, tmp_path):
    cfg = Config()
    cfg.paths.db = str(tmp_path / "birdwatcher.db")
    cfg.paths.captures = str(tmp_path / "captures")
    monkeypatch.setattr("birdwatcher.pipeline.Database", lambda path: _FakeDB())
    monkeypatch.setattr("birdwatcher.pipeline.RTSPCamera", lambda *a, **k: _FakeCamera())
    monkeypatch.setattr("birdwatcher.pipeline.BirdDetector", lambda *a, **k: _FakeDetector())
    monkeypatch.setattr("birdwatcher.pipeline.build_classifier", lambda cfg: (_ for _ in ()).throw(RuntimeError("init fail")))
    pipe = Pipeline(cfg)
    assert pipe.classifier.__class__.__name__ == "StubClassifier"
