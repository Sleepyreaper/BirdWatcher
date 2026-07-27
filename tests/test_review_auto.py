from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from birdwatcher import review_auto
from birdwatcher.config import Config
from birdwatcher.database import Database


class _FakeClassifier:
    """Top-k always leads with the (wrong) current label, then a runner-up."""

    def classify_topk(self, crop, k=3):
        return [SimpleNamespace(species="Southern Flying Squirrel", confidence=0.9),
                SimpleNamespace(species="Raccoon", confidence=0.4)]


def _setup(tmp_path, rows):
    """Build a DB + real crop files; rows = [(species, rel_path), …]."""
    cfg = Config()
    cfg.paths.db = str(tmp_path / "bw.db")
    cfg.paths.captures = str(tmp_path / "captures")
    db = Database(cfg.paths.db_path())
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    for species, rel in rows:
        p = tmp_path / "captures" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(p), img)
        db.add_visit(species, 0.9, image_path=rel, source="creek")
    db.close()
    return cfg


@pytest.fixture(autouse=True)
def _stub_classifier(monkeypatch):
    monkeypatch.setattr(review_auto, "build_classifier", lambda cfg: _FakeClassifier())


def test_relabels_when_vision_disagrees(tmp_path, monkeypatch):
    cfg = _setup(tmp_path, [("Southern Flying Squirrel", "d/a.jpg")])
    monkeypatch.setattr(review_auto, "vision_adjudicate", lambda vc, crop, pool: "Raccoon")
    stats = review_auto.run_auto_review(cfg, source="creek", dry_run=False)
    assert stats["relabeled"] == 1
    db = Database(cfg.paths.db_path())
    assert db.recent_visits(5, source="creek")[0]["species"] == "Raccoon"
    db.close()


def test_confirms_when_vision_agrees(tmp_path, monkeypatch):
    cfg = _setup(tmp_path, [("Southern Flying Squirrel", "d/a.jpg")])
    monkeypatch.setattr(review_auto, "vision_adjudicate",
                        lambda vc, crop, pool: "Southern Flying Squirrel")
    stats = review_auto.run_auto_review(cfg, source="creek", dry_run=False)
    assert stats["confirmed"] == 1 and stats["relabeled"] == 0
    db = Database(cfg.paths.db_path())
    assert db.list_unverified(10) == []          # confirming marks it reviewed
    db.close()


def test_reject_unsure_hides_the_sighting(tmp_path, monkeypatch):
    cfg = _setup(tmp_path, [("Southern Flying Squirrel", "d/a.jpg")])
    monkeypatch.setattr(review_auto, "vision_adjudicate", lambda vc, crop, pool: None)
    stats = review_auto.run_auto_review(cfg, source="creek", dry_run=False, reject_unsure=True)
    assert stats["unsure"] == 1
    db = Database(cfg.paths.db_path())
    assert db.recent_visits(5, source="creek") == []   # rejected -> hidden everywhere
    db.close()


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    cfg = _setup(tmp_path, [("Southern Flying Squirrel", "d/a.jpg")])
    monkeypatch.setattr(review_auto, "vision_adjudicate", lambda vc, crop, pool: "Raccoon")
    stats = review_auto.run_auto_review(cfg, source="creek", dry_run=True)
    assert stats["relabeled"] == 1                # counted…
    db = Database(cfg.paths.db_path())
    assert db.recent_visits(5, source="creek")[0]["species"] == "Southern Flying Squirrel"
    assert len(db.list_unverified(10)) == 1       # …but nothing written
    db.close()


def test_missing_crop_counts_as_error(tmp_path, monkeypatch):
    cfg = _setup(tmp_path, [("Southern Flying Squirrel", "d/a.jpg")])
    (tmp_path / "captures" / "d" / "a.jpg").unlink()   # crop gone
    monkeypatch.setattr(review_auto, "vision_adjudicate", lambda vc, crop, pool: "Raccoon")
    stats = review_auto.run_auto_review(cfg, source="creek", dry_run=False)
    assert stats["errors"] == 1 and stats["relabeled"] == 0


def test_sightings_to_review_filters_by_source_and_species(tmp_path):
    db = Database(tmp_path / "bw.db")
    db.add_visit("Southern Flying Squirrel", 0.9, image_path="a.jpg", source="creek")
    db.add_visit("American Black Bear", 0.9, image_path="b.jpg", source="creek")
    db.add_visit("Northern Cardinal", 0.9, image_path="c.jpg", source="feeder")
    got = db.sightings_to_review(source="creek", species=["American Black Bear"])
    assert len(got) == 1 and got[0]["species"] == "American Black Bear"
    assert len(db.sightings_to_review(source="creek")) == 2
    db.close()
