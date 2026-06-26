"""The watch loop: RTSP frame -> motion gate -> bird detect -> classify -> store.

Repeat sightings of the same species inside `visit_cooldown` are collapsed into
one visit, so the weekly grid counts visits rather than frames.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .capture import RTSPCamera
from .classifier import build_classifier
from .config import Config
from .database import Database
from .detector import BirdDetector


class Pipeline:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db = Database(cfg.paths.db_path())
        self.camera = RTSPCamera(cfg.camera, cfg.motion)
        self.detector = BirdDetector(cfg.detector)
        self.classifier = build_classifier(cfg.classifier)
        self.captures_dir = cfg.paths.captures_path()
        self.captures_dir.mkdir(parents=True, exist_ok=True)

    def _within_cooldown(self, species: str, now: datetime) -> bool:
        last = self.db.last_seen(species)
        if last is None:
            return False
        return (now - last).total_seconds() < self.cfg.pipeline.visit_cooldown

    def _save_crop(self, crop, species: str, ts: datetime) -> str | None:
        if not self.cfg.pipeline.save_crops:
            return None
        import cv2

        day_dir = self.captures_dir / ts.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        slug = species.lower().replace(" ", "-").replace("/", "-")
        fname = f"{slug}_{ts.strftime('%H%M%S')}.jpg"
        out = day_dir / fname
        cv2.imwrite(str(out), crop)
        # store path relative to the captures dir, for the web server to resolve
        return str(out.relative_to(self.captures_dir)).replace("\\", "/")

    def process_frame(self, frame) -> None:
        for det in self.detector.detect(frame):
            result = self.classifier.classify(det.crop)
            species = result.species
            if result.confidence < self.cfg.classifier.min_confidence:
                species = "Unknown bird"

            now = datetime.now()
            if self._within_cooldown(species, now):
                continue  # same visitor still here — don't double count

            rel = self._save_crop(det.crop, species, now)
            self.db.add_sighting(
                species=species,
                confidence=result.confidence,
                image_path=rel,
                detector_conf=det.confidence,
                ts=now,
            )
            print(f"[pipeline] {now:%H:%M:%S}  {species}  "
                  f"(id={result.confidence:.2f} det={det.confidence:.2f})")

    def run(self) -> None:
        print(f"[pipeline] backend={self.cfg.classifier.backend} "
              f"watching {self.cfg.camera.rtsp_url}")
        try:
            for frame in self.camera.frames():
                self.process_frame(frame)
        except KeyboardInterrupt:
            print("\n[pipeline] stopping…")
        finally:
            self.camera.release()
            self.db.close()
