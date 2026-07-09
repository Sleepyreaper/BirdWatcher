"""Object detection via Ultralytics YOLO — the "something's there, crop it" gate.

Which classes count is configurable (`detector.classes`): the feeder cam wants
just `bird`, while a wildlife/creek cam wants the broad animal set. The crop is
handed to the classifier (BioCLIP), which does the actual species ID — so the
detector only has to notice *an animal*, not name it. Class names are resolved
against whatever model is loaded, so this works for YOLO-COCO today and drops in
a MegaDetector (`animal`/`person`/`vehicle`) later with only a config change.
"""

from __future__ import annotations

from dataclasses import dataclass

try:
    from ultralytics import YOLO
except ImportError:  # pragma: no cover
    YOLO = None

from .config import DetectorConfig


@dataclass
class Detection:
    confidence: float
    box: tuple[int, int, int, int]  # x1, y1, x2, y2
    crop: "object"  # np.ndarray (BGR)


class Detector:
    def __init__(self, cfg: DetectorConfig):
        if YOLO is None:
            raise RuntimeError("ultralytics not installed; pip install ultralytics")
        self.cfg = cfg
        self.model = YOLO(cfg.model)
        # Resolve the configured class NAMES to this model's indices, so the same
        # config works across models with different label maps. Unknown names are
        # dropped; if none resolve, accept everything (single-class models).
        names = {str(n).lower(): i for i, n in self.model.names.items()}
        wanted = [c.lower() for c in (cfg.classes or [])]
        ids = [names[c] for c in wanted if c in names]
        self.class_ids = ids or None
        if wanted and not ids:
            print(f"[detector] none of {cfg.classes} in model labels; accepting all classes")

    def detect(self, frame) -> list[Detection]:
        """Return detections for the configured classes (highest confidence first)."""
        h, w = frame.shape[:2]
        results = self.model.predict(
            frame, classes=self.class_ids, conf=self.cfg.min_confidence, verbose=False
        )
        out: list[Detection] = []
        for res in results:
            for b in res.boxes:
                conf = float(b.conf[0])
                x1, y1, x2, y2 = (int(v) for v in b.xyxy[0])
                # pad the box for context, clamped to frame
                pad_x = int((x2 - x1) * self.cfg.crop_pad_frac)
                pad_y = int((y2 - y1) * self.cfg.crop_pad_frac)
                cx1, cy1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
                cx2, cy2 = min(w, x2 + pad_x), min(h, y2 + pad_y)
                crop = frame[cy1:cy2, cx1:cx2].copy()
                out.append(Detection(conf, (x1, y1, x2, y2), crop))
        out.sort(key=lambda d: d.confidence, reverse=True)
        return out


# Back-compat alias (pipeline + tests import BirdDetector).
BirdDetector = Detector
