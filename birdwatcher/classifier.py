"""Pluggable bird species classification.

Backends (selected via config.classifier.backend):
  - "stub"    : no ML deps; labels everything "Unknown bird" (wiring/test path).
  - "bioclip" : local BioCLIP zero-shot against the species catalog (no API key).
  - "tfhub"   : Google aiy/birds_V1 (needs TensorFlow; no Python 3.14 wheels yet).
  - "claude"  : Claude vision (needs ANTHROPIC_API_KEY).

All backends implement: classify(crop_bgr) -> SpeciesResult
"""

from __future__ import annotations

import base64
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


class BioClipClassifier(SpeciesClassifier):
    """Local zero-shot species ID with BioCLIP, constrained to the catalog.

    Scores the crop against the Cobb/Cole's common names and returns the best
    match. Fully offline once the model is cached; no API key. Accuracy is high
    on clear birds but depends on crop quality — a bird behind feeder-cage bars
    or small in frame can still be misread.
    """

    def __init__(self, cfg: ClassifierConfig):
        import json

        import open_clip
        import torch

        from .config import PROJECT_ROOT

        self.cfg = cfg
        self.torch = torch
        catalog = json.loads(
            (PROJECT_ROOT / "data" / "species_catalog.json").read_text(encoding="utf-8")
        )
        self._names = [s["common_name"] for s in catalog["species"]]
        # BioCLIP 2 is a whole-tree-of-life model, so we also let it match common
        # backyard/creek mammals — a chipmunk scores as "Eastern Chipmunk" instead
        # of being forced into a bird label. The web layer files these under
        # "Critters" (any name present in critters.json).
        critters_path = PROJECT_ROOT / "data" / "critters.json"
        if critters_path.exists():
            crit = json.loads(critters_path.read_text(encoding="utf-8"))
            self._names += [c["common_name"] for c in crit.get("critters", [])]

        handle = cfg.bioclip_model or "hf-hub:imageomics/bioclip"
        try:
            model, _, preprocess = open_clip.create_model_and_transforms(handle)
            tokenizer = open_clip.get_tokenizer(handle)
        except Exception as e:  # bioclip-2 unavailable/incompatible -> fall back to v1
            print(f"[classifier] {handle} failed ({e}); falling back to bioclip v1")
            handle = "hf-hub:imageomics/bioclip"
            model, _, preprocess = open_clip.create_model_and_transforms(handle)
            tokenizer = open_clip.get_tokenizer(handle)
        print(f"[classifier] bioclip model: {handle}")
        model.eval()
        self.model, self.preprocess = model, preprocess
        with torch.no_grad():
            txt = model.encode_text(tokenizer([f"a photo of {n}." for n in self._names]))
            txt /= txt.norm(dim=-1, keepdim=True)
        self._txt = txt

    def classify(self, crop) -> SpeciesResult:
        import cv2
        from PIL import Image

        if crop is None or crop.size == 0:
            return SpeciesResult("Unknown bird", 0.0)
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        img = self.preprocess(Image.fromarray(rgb)).unsqueeze(0)
        with self.torch.no_grad():
            feat = self.model.encode_image(img)
            feat /= feat.norm(dim=-1, keepdim=True)
            probs = (100.0 * feat @ self._txt.T).softmax(dim=-1)[0]
        idx = int(probs.argmax())
        return SpeciesResult(self._names[idx], float(probs[idx]))


class TFHubBirdClassifier(SpeciesClassifier):
    """Google's on-device bird classifier (~964 species). Needs TensorFlow."""

    def __init__(self, cfg: ClassifierConfig):
        import csv
        import urllib.request

        import numpy as np  # noqa: F401
        import tensorflow as tf
        import tensorflow_hub as hub

        self.cfg = cfg
        self._tf = tf
        self._model = hub.load(cfg.tfhub_handle)
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
        self._client = Anthropic()

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
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64",
                     "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": (
                        "Identify the bird species in this feeder photo. Reply ONLY as "
                        'JSON: {"species": "Common Name", "confidence": 0.0-1.0}. '
                        'If unsure, use "Unknown bird" with low confidence.')},
                ],
            }],
        )
        text = "".join(b.text for b in msg.content if b.type == "text")
        try:
            data = json.loads(text[text.index("{"): text.rindex("}") + 1])
            return SpeciesResult(str(data["species"]), float(data["confidence"]))
        except (ValueError, KeyError):
            return SpeciesResult("Unknown bird", 0.0)


def build_classifier(cfg: ClassifierConfig) -> SpeciesClassifier:
    backend = (cfg.backend or "stub").lower()
    if backend == "stub":
        return StubClassifier()
    if backend == "bioclip":
        return BioClipClassifier(cfg)
    if backend == "tfhub":
        return TFHubBirdClassifier(cfg)
    if backend == "claude":
        return ClaudeBirdClassifier(cfg)
    raise ValueError(f"Unknown classifier backend: {cfg.backend!r}")
