"""The watch loop: RTSP frame -> motion gate -> bird detect -> track into visits.

Birds are matched across frames by bounding-box overlap, so a single bird becomes
one "visit" no matter how many frames it appears in. While a visit is open we keep
the *sharpest* crop (variance-of-Laplacian x detector confidence). When the bird
leaves (no sighting for `visit_timeout`), we classify that one best crop and write
a single row. So the species classifier runs once per visit, not once per frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .capture import RTSPCamera
from .classifier import StubClassifier, build_classifier
from .config import Config
from .database import Database
from .detector import BirdDetector

Box = tuple[int, int, int, int]


def _iou(a: Box, b: Box) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


def _sharpness(crop) -> float:
    import cv2

    if crop is None or crop.size == 0:
        return 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


@dataclass
class _Visit:
    box: Box
    first_seen: datetime
    last_seen: datetime
    frames: int
    best_score: float
    best_crop: object
    best_det_conf: float


class Pipeline:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db = Database(cfg.paths.db_path())
        self.camera = RTSPCamera(cfg.camera, cfg.motion)
        self.detector = BirdDetector(cfg.detector)
        try:
            self.classifier = build_classifier(cfg.classifier)
        except Exception as e:
            print(f"[pipeline] classifier init failed ({e}); falling back to stub")
            self.classifier = StubClassifier()
        self.captures_dir = cfg.paths.captures_path()
        self.captures_dir.mkdir(parents=True, exist_ok=True)
        self._open: dict[int, _Visit] = {}
        self._next_id = 1

    # --- visit tracking ---------------------------------------------------
    def _match(self, box: Box) -> int | None:
        best_id, best_iou = None, 0.3
        for vid, v in self._open.items():
            score = _iou(box, v.box)
            if score >= best_iou:
                best_iou, best_id = score, vid
        return best_id

    def process_frame(self, frame) -> None:
        now = datetime.now()
        for det in self.detector.detect(frame):
            score = _sharpness(det.crop) * (0.5 + det.confidence)
            vid = self._match(det.box)
            if vid is None:
                self._open[self._next_id] = _Visit(
                    det.box, now, now, 1, score, det.crop, det.confidence
                )
                self._next_id += 1
            else:
                v = self._open[vid]
                v.box, v.last_seen, v.frames = det.box, now, v.frames + 1
                if score > v.best_score:
                    v.best_score, v.best_crop, v.best_det_conf = (
                        score, det.crop, det.confidence,
                    )
        self._reap(now)

    def _reap(self, now: datetime, flush: bool = False) -> None:
        timeout = self.cfg.pipeline.visit_timeout
        ended = [
            vid for vid, v in self._open.items()
            if flush or (now - v.last_seen).total_seconds() > timeout
        ]
        for vid in ended:
            self._record(self._open.pop(vid))

    def _record(self, v: _Visit) -> None:
        if v.frames < self.cfg.pipeline.min_visit_frames:
            return  # blip, not a real visit
        try:
            result = self.classifier.classify(v.best_crop)
        except Exception as e:
            print(f"[pipeline] classify failed: {e}")
            return
        species = result.species
        if result.confidence < self.cfg.classifier.min_confidence:
            species = "Unknown bird"
        try:
            if self.cfg.pipeline.ingest_url:
                self._post_visit(v, species, result.confidence)
            else:
                rel = self._save_crop(v.best_crop, species, v.first_seen)
                self.db.add_visit(
                    species=species,
                    confidence=result.confidence,
                    image_path=rel,
                    detector_conf=v.best_det_conf,
                    first_ts=v.first_seen,
                    last_ts=v.last_seen,
                    frames=v.frames,
                )
        except Exception as e:
            print(f"[pipeline] record failed: {e}")
            return
        dur = (v.last_seen - v.first_seen).total_seconds()
        print(f"[pipeline] {v.first_seen:%H:%M:%S}  visit: {species}  "
              f"frames={v.frames} dur={dur:.0f}s id={result.confidence:.2f}")

    def _save_crop(self, crop, species: str, ts: datetime) -> str | None:
        if not self.cfg.pipeline.save_crops:
            return None
        import cv2

        day_dir = self.captures_dir / ts.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        slug = species.lower().replace(" ", "-").replace("/", "-")
        out = day_dir / f"{slug}_{ts.strftime('%H%M%S')}.jpg"
        if crop is None or getattr(crop, "size", 0) == 0:
            return None
        if not cv2.imwrite(str(out), crop):
            raise RuntimeError(f"failed to write crop: {out}")
        return str(out.relative_to(self.captures_dir)).replace("\\", "/")

    def _post_visit(self, v: _Visit, species: str, conf: float) -> None:
        """Send the visit (metadata + best crop) to a remote dashboard's ingest API."""
        import base64
        import json
        import urllib.request

        import cv2

        ok, buf = cv2.imencode(".jpg", v.best_crop)
        payload = json.dumps({
            "token": self.cfg.pipeline.ingest_token,
            "species": species,
            "confidence": conf,
            "detector_conf": v.best_det_conf,
            "first_ts": v.first_seen.isoformat(timespec="seconds"),
            "last_ts": v.last_seen.isoformat(timespec="seconds"),
            "frames": v.frames,
            "image_b64": base64.b64encode(buf.tobytes()).decode() if ok else "",
        }).encode()
        req = urllib.request.Request(
            self.cfg.pipeline.ingest_url, data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                resp.read()
        except Exception as e:
            print(f"[pipeline] ingest POST failed: {e}")

    # --- run loop ---------------------------------------------------------
    def run(self) -> None:
        print(f"[pipeline] backend={self.cfg.classifier.backend} "
              f"watching {self.cfg.camera.rtsp_url}")
        try:
            for frame in self.camera.frames():
                if frame is None:
                    self._reap(datetime.now())  # idle heartbeat
                else:
                    try:
                        self.process_frame(frame)
                    except Exception as e:
                        print(f"[pipeline] frame processing failed: {e}")
        except KeyboardInterrupt:
            print("\n[pipeline] stopping…")
        finally:
            self._reap(datetime.now(), flush=True)  # record any open visits
            self.camera.release()
            self.db.close()
