"""Pluggable bird species classification.

Backends (selected via config.classifier.backend):
  - "stub"  : no ML deps; labels everything "Unknown bird" (wiring/test path).
  - "tfhub" : Google aiy/birds_V1, ~964 species, fully local, no API key.
  - "claude": sends the crop to Claude vision; best accuracy, costs API tokens.

All backends implement: classify(crop_bgr) -> SpeciesResult
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass

from .config import ClassifierConfig


@dataclass
class SpeciesResult:
    species: str
    confidence: float


class SpeciesClassifier:
    def classify(self, crop) -> SpeciesResult:  # pragma: no cover - interface
        raise NotImplementedError


class StubClassifier(SpeciesClassifier):
    """Always returns a placeholder; lets the whole pipeline run without ML."""

    def classify(self, crop) -> SpeciesResult:
        return SpeciesResult("Unknown bird", 1.0)


class TFHubBirdClassifier(SpeciesClassifier):
    """Google's on-device bird classifier (~964 species)."""

    def __init__(self, cfg: ClassifierConfig):
        import csv
        import urllib.request

        import numpy as np  # noqa: F401  (used at classify time)
        import tensorflow as tf
        import tensorflow_hub as hub

        self.cfg = cfg
        self._tf = tf
        self._model = hub.load(cfg.tfhub_handle)
        # The model ships a labelmap; fetch + cache species names.
        labelmap_url = (
            "https://www.gstatic.com/aihub/tfhub/labelmaps/aiy_birds_V1_labelmap.csv"
        )
        with urllib.request.urlopen(labelmap_url) as fh:
            reader = csv.DictReader(line.decode("utf-8") for line in fh)
            self._labels = [row["name"] for row in reader]

    def classify(self, crop) -> SpeciesResult:
        import cv2
        import numpy as np

        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        img = cv2.resize(rgb, (224, 224)).astype("float32") / 255.0
        logits = self._model(self._tf.expand_dims(img, 0))
        probs = self._tf.nn.softmax(logits[0]).numpy()
        idx = int(np.argmax(probs))
        name = self._labels[idx] if idx < len(self._labels) else f"class_{idx}"
        return SpeciesResult(name, float(probs[idx]))


class ClaudeBirdClassifier(SpeciesClassifier):
    """Ask Claude vision to name the bird. Reads ANTHROPIC_API_KEY from env."""

    def __init__(self, cfg: ClassifierConfig):
        from anthropic import Anthropic

        self.cfg = cfg
        self._client = Anthropic()  # picks up ANTHROPIC_API_KEY

    def classify(self, crop) -> SpeciesResult:
        import json

        import cv2

        ok, buf = cv2.imencode(".jpg", crop)
        if not ok:
            return SpeciesResult("Unknown bird", 0.0)
        b64 = base64.standard_b64encode(buf.tobytes()).decode("ascii")
        msg = self._client.messages.create(
            model=self.cfg.claude_model,
            max_tokens=200,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Identify the bird species in this feeder photo. "
                                'Reply ONLY as JSON: {"species": "Common Name", '
                                '"confidence": 0.0-1.0}. If unsure, use '
                                '"Unknown bird" with low confidence.'
                            ),
                        },
                    ],
                }
            ],
        )
        text = "".join(b.text for b in msg.content if b.type == "text")
        try:
            data = json.loads(text[text.index("{") : text.rindex("}") + 1])
            return SpeciesResult(str(data["species"]), float(data["confidence"]))
        except (ValueError, KeyError):
            return SpeciesResult("Unknown bird", 0.0)


def build_classifier(cfg: ClassifierConfig) -> SpeciesClassifier:
    backend = (cfg.backend or "stub").lower()
    if backend == "stub":
        return StubClassifier()
    if backend == "tfhub":
        return TFHubBirdClassifier(cfg)
    if backend == "claude":
        return ClaudeBirdClassifier(cfg)
    raise ValueError(f"Unknown classifier backend: {cfg.backend!r}")
