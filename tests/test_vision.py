from __future__ import annotations

import json

from birdwatcher.config import VisionConfig
from birdwatcher.vision import _match_candidate, adjudicate

CANDS = ["Raccoon", "American Beaver", "River Otter"]


def test_match_exact_name():
    assert _match_candidate("Raccoon", CANDS) == "Raccoon"


def test_match_ignores_case_and_punctuation():
    assert _match_candidate('  "raccoon."\n', CANDS) == "Raccoon"


def test_match_single_name_inside_a_sentence():
    assert _match_candidate("This looks like a Raccoon to me", CANDS) == "Raccoon"


def test_match_none_literal_is_none():
    assert _match_candidate("none", CANDS) is None


def test_match_ambiguous_answer_is_none():
    # two candidates named — can't safely resolve
    assert _match_candidate("Either a Raccoon or an American Beaver", CANDS) is None


def test_match_strips_think_block():
    assert _match_candidate("<think>hmm the tail...</think>Raccoon", CANDS) == "Raccoon"


def test_adjudicate_disabled_returns_none():
    assert adjudicate(VisionConfig(enabled=False), object(), CANDS) is None


def test_adjudicate_no_candidates_returns_none():
    assert adjudicate(VisionConfig(enabled=True), object(), []) is None


def test_adjudicate_picks_from_candidates(monkeypatch):
    import numpy as np

    crop = np.zeros((8, 8, 3), dtype=np.uint8)   # real crop so cv2 can encode it

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"message": {"content": "Raccoon"}}).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp())
    out = adjudicate(VisionConfig(enabled=True), crop, CANDS)
    assert out == "Raccoon"


def test_adjudicate_network_failure_returns_none(monkeypatch):
    import numpy as np

    crop = np.zeros((8, 8, 3), dtype=np.uint8)
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert adjudicate(VisionConfig(enabled=True), crop, CANDS) is None
