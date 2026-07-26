from __future__ import annotations

from datetime import datetime

from birdwatcher import naturalist
from birdwatcher.config import NaturalistConfig
from birdwatcher.database import Database


def test_context_summarizes_sightings(tmp_path):
    db = Database(tmp_path / "bw.db")
    now = datetime.now()
    db.add_visit("Northern Cardinal", 0.9, image_path="c.jpg", source="feeder", first_ts=now)
    db.add_visit("Raccoon", 0.8, image_path="r.jpg", source="creek", first_ts=now)
    db.close()

    ctx = naturalist.build_context(str(tmp_path / "bw.db"), "Test Yard")
    assert "Test Yard" in ctx
    assert "Northern Cardinal" in ctx and "Raccoon" in ctx
    assert "feeder" in ctx and "creek" in ctx


def test_ask_disabled_is_graceful(tmp_path):
    db = Database(tmp_path / "bw.db")
    db.close()
    r = naturalist.ask(NaturalistConfig(enabled=False), str(tmp_path / "bw.db"), "what's up?")
    assert r["ok"] is False and r["answer"]


def test_ask_grounds_answer_in_the_log(tmp_path, monkeypatch):
    db = Database(tmp_path / "bw.db")
    db.add_visit("Blue Jay", 0.9, image_path="j.jpg", first_ts=datetime.now())
    db.close()
    # capture what context the model is handed, and stub the model itself
    seen = {}
    def fake_chat(cfg, system, user):
        seen["system"] = system
        return "A Blue Jay stopped by this morning."
    monkeypatch.setattr(naturalist, "_chat", fake_chat)

    r = naturalist.ask(NaturalistConfig(enabled=True), str(tmp_path / "bw.db"), "what visited?")
    assert r["ok"] is True and "Blue Jay" in r["answer"]
    assert "Blue Jay" in seen["system"]   # the sighting was actually in the context
