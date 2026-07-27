from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from birdwatcher.config import Config
from birdwatcher.pipeline import Pipeline, _Sample, _Visit


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


class _LowClassifier:
    def classify(self, crop):
        return SimpleNamespace(species="Tufted Titmouse", confidence=0.42)


class _MapClassifier:
    """Returns a preset (species, confidence) per crop object identity."""

    def __init__(self, mapping):
        self.mapping = mapping

    def classify(self, crop):
        species, conf = self.mapping[id(crop)]
        return SimpleNamespace(species=species, confidence=conf)


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


def _visit(samples=None, label="bird"):
    ts = datetime(2026, 6, 29, 8, 0, 0)
    if samples is None:
        samples = [_Sample(1.0, object(), 0.8, label)]
    return _Visit((0, 0, 10, 10), ts, ts, 3, samples)


def test_record_classifier_failure_does_not_raise(pipe):
    pipe.classifier = _BoomClassifier()
    pipe._record(_visit())
    assert pipe.db.rows == []


def test_low_confidence_visit_is_tossed(pipe):
    """A weak match (below pipeline.min_confidence) is discarded, not recorded."""
    pipe.classifier = _LowClassifier()          # 0.42, default floor is 0.70
    pipe._record(_visit())
    assert pipe.db.rows == []


def test_person_recorded_without_classifier(pipe):
    """A person is tagged 'Homo sapiens' via the detector label — BioCLIP never runs."""
    pipe.classifier = _BoomClassifier()   # would raise if called
    pipe._record(_visit([_Sample(1.0, object(), 0.8, "person")]))
    assert len(pipe.db.rows) == 1
    assert pipe.db.rows[0]["species"] == "Homo sapiens"


def test_voting_overrides_a_single_wrong_frame(pipe):
    """The sharpest frame reads 'American Beaver', but four others agree on
    'Raccoon' — voting logs the consensus, not the lucky-but-wrong sharp frame."""
    beaver = object()
    raccoons = [object() for _ in range(4)]
    mapping = {id(beaver): ("American Beaver", 0.95)}
    for r in raccoons:
        mapping[id(r)] = ("Raccoon", 0.80)
    pipe.classifier = _MapClassifier(mapping)
    # sharpest first (score 100) is the wrong beaver frame
    samples = [_Sample(100.0, beaver, 0.9, "animal")]
    samples += [_Sample(90.0 - i, r, 0.9, "animal") for i, r in enumerate(raccoons)]
    pipe._record(_visit(samples))
    assert len(pipe.db.rows) == 1
    row = pipe.db.rows[0]
    assert row["species"] == "Raccoon"
    assert row["agreement"] == pytest.approx(0.8)   # 4 of 5 frames agreed


def test_voting_tosses_a_visit_the_frames_cant_agree_on(pipe):
    """No species clears the agreement floor (2 beaver / 2 raccoon / 1 otter) —
    a confused visit is dropped rather than logged as a coin-flip winner."""
    crops = [object() for _ in range(5)]
    mapping = {
        id(crops[0]): ("American Beaver", 0.9), id(crops[1]): ("American Beaver", 0.9),
        id(crops[2]): ("Raccoon", 0.9), id(crops[3]): ("Raccoon", 0.9),
        id(crops[4]): ("River Otter", 0.9),
    }
    pipe.classifier = _MapClassifier(mapping)
    samples = [_Sample(100.0 - i, c, 0.9, "animal") for i, c in enumerate(crops)]
    pipe._record(_visit(samples))
    assert pipe.db.rows == []


def test_agreement_is_recorded_for_a_clean_visit(pipe):
    """A unanimous visit stores agreement=1.0 alongside the species."""
    pipe.classifier = _GoodClassifier()
    crops = [object() for _ in range(3)]
    samples = [_Sample(100.0 - i, c, 0.9, "bird") for i, c in enumerate(crops)]
    pipe._record(_visit(samples))
    assert len(pipe.db.rows) == 1
    assert pipe.db.rows[0]["species"] == "Northern Cardinal"
    assert pipe.db.rows[0]["agreement"] == pytest.approx(1.0)


def test_record_save_failure_does_not_raise(pipe, monkeypatch):
    monkeypatch.setattr(pipe, "_save_crop", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk")))
    pipe._record(_visit())
    assert pipe.db.rows == []


def test_post_visit_network_failure_is_swallowed(pipe, monkeypatch):
    import numpy as np

    # Real crop so cv2.imencode succeeds and we actually exercise the network
    # path this test is about (a sentinel object would fail before urlopen).
    crop = np.zeros((10, 10, 3), dtype=np.uint8)
    visit = _visit([_Sample(1.0, crop, 0.8, "bird")])
    pipe.cfg.pipeline.ingest_url = "http://example.test/api/ingest"
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    pipe._post_visit(visit, "Northern Cardinal", 0.9, crop, 0.8, 1.0)


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
