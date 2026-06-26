"""Bird detection via Ultralytics YOLO.

We only care about the COCO `bird` class (id 14). The detector answers
"is there a bird, and where?" and returns a padded crop for the classifier.
"""

from __future__ import annotations

from dataclasses import dataclass

try:
    from ultralytics import YOLO
except ImportError:  # pragma: no cover
    YOLO = None

from .config import DetectorConfig

COCO_BIRD_CLASS = 14


@dataclass
class Detection:
    confidence: float
    box: tuple[int, int, int, int]  # x1, y1, x2, y2
    crop: "object"  # np.ndarray (BGR)


class BirdDetector:
    def __init__(self, cfg: DetectorConfig):
        if YOLO is None:
            raise RuntimeError("ultralytics not installed; pip install ultralytics")
        self.cfg = cfg
        self.model = YOLO(cfg.model)

    def detect(self, frame) -> list[Detection]:
        """Return bird detections (highest confidence first)."""
        h, w = frame.shape[:2]
        results = self.model.predict(
            frame, classes=[COCO_BIRD_CLASS], conf=self.cfg.min_confidence, verbose=False
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
