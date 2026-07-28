"""The watch loop: RTSP frame -> motion gate -> bird detect -> track into visits.

Birds are matched across frames by bounding-box overlap, so a single bird becomes
one "visit" no matter how many frames it appears in. While a visit is open we keep
the *N sharpest* crops (variance-of-Laplacian x detector confidence). When the bird
leaves (no sighting for `visit_timeout`), we classify those frames and let them
*vote* on the species — the consensus wins, and how strongly the frames agreed
becomes an honest confidence signal. Voting kills the "one lucky-but-wrong frame
decides the visit" failure; see voting.py. Set pipeline.vote_max_samples=1 (or
vote_enabled=false) to fall back to the old single-best-crop behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .capture import RTSPCamera
from .classifier import StubClassifier, build_classifier
from .config import Config
from .database import Database, PERSON_SPECIES
from .detector import BirdDetector
from .vision import adjudicate as vision_adjudicate
from .voting import tally_votes

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
class _Sample:
    score: float          # sharpness × detector confidence — higher = crisper crop
    crop: object
    det_conf: float
    label: str            # detector class ("bird", "person", …)


@dataclass
class _Visit:
    box: Box
    first_seen: datetime
    last_seen: datetime
    frames: int
    samples: list         # the top-N _Sample by score (sharpest first) — voted on


def _keep_top(samples: list, sample: _Sample, k: int) -> None:
    """Add a sample to a visit, keeping only the k sharpest (best first)."""
    samples.append(sample)
    samples.sort(key=lambda s: s.score, reverse=True)
    del samples[k:]


class Pipeline:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db = Database(cfg.paths.db_path())
        self.camera = RTSPCamera(cfg.camera, cfg.motion)
        self.detector = BirdDetector(cfg.detector)
        # Point the classifier at the reference library (few-shot prototypes).
        if not cfg.classifier.library_dir:
            cfg.classifier.library_dir = str(cfg.paths.captures_path().parent / "library")
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

    def _sample_cap(self) -> int:
        """How many frames per visit to keep for voting (1 = old behavior)."""
        if not self.cfg.pipeline.vote_enabled:
            return 1
        return max(1, self.cfg.pipeline.vote_max_samples)

    def process_frame(self, frame) -> None:
        now = datetime.now()
        k = self._sample_cap()
        for det in self.detector.detect(frame):
            score = _sharpness(det.crop) * (0.5 + det.confidence)
            sample = _Sample(score, det.crop, det.confidence, det.label)
            vid = self._match(det.box)
            if vid is None:
                self._open[self._next_id] = _Visit(det.box, now, now, 1, [sample])
                self._next_id += 1
            else:
                v = self._open[vid]
                v.box, v.last_seen, v.frames = det.box, now, v.frames + 1
                _keep_top(v.samples, sample, k)
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
        if v.frames < self.cfg.pipeline.min_visit_frames or not v.samples:
            return
        best = v.samples[0]   # sharpest frame (samples kept sorted, best first)
        agreement: float | None = None
        # People bypass BioCLIP entirely — recorded as "Homo sapiens" (never
        # toss; security matters), using the detector's confidence + sharpest crop.
        if best.label == "person":
            species, conf, crop, det_conf = (
                PERSON_SPECIES, best.det_conf, best.crop, best.det_conf,
            )
        else:
            decided = self._vote_species(v)
            if decided is None:
                return   # classify failed, or the frames couldn't agree
            species, conf, crop, det_conf, agreement = decided
        # Known-false-positive labels for this camera (e.g. the creek's IR-lit
        # moths that read as "flying squirrel") never reach the DB.
        if species in self.cfg.pipeline.reject_species:
            print(f"[pipeline] {v.first_seen:%H:%M:%S}  dropped {species} — on "
                  f"{self.cfg.camera.source}'s reject list (known false positive here)")
            return
        try:
            if self.cfg.pipeline.ingest_url:
                self._post_visit(v, species, conf, crop, det_conf, agreement)
            else:
                rel = self._save_crop(crop, species, v.first_seen)
                self.db.add_visit(
                    species=species,
                    confidence=conf,
                    image_path=rel,
                    detector_conf=det_conf,
                    first_ts=v.first_seen,
                    last_ts=v.last_seen,
                    frames=v.frames,
                    source=self.cfg.camera.source,
                    agreement=agreement,
                )
        except Exception as e:
            print(f"[pipeline] record failed: {e}")
            return
        dur = (v.last_seen - v.first_seen).total_seconds()
        agree_str = f" agree={agreement:.0%}" if agreement is not None else ""
        print(f"[pipeline] {v.first_seen:%H:%M:%S}  visit: {species}  "
              f"frames={v.frames} dur={dur:.0f}s id={conf:.2f}{agree_str}")

    def _vote_species(self, v: _Visit):
        """Classify the visit's kept frames, let them vote, and — when the vote
        is shaky — get a vision model's second opinion.

        Returns (species, confidence, crop, det_conf, agreement), or None if
        classification failed or the frames couldn't agree well enough to trust.
        """
        ballots = []   # list[(list[SpeciesResult] top-k, _Sample)]
        for s in v.samples:
            try:
                topk = self.classifier.classify_topk(s.crop, 3)
            except Exception as e:
                print(f"[pipeline] classify failed: {e}")
                continue
            if topk:
                ballots.append((topk, s))
        if not ballots:
            return None
        outcome = tally_votes([tk[0] for tk, _ in ballots])
        if outcome is None:
            return None

        vcfg = self.cfg.vision
        low_agree = outcome.agreement < self.cfg.pipeline.vote_min_agreement
        # A vision second-opinion is worth it when the frames disagreed, or when
        # a rarely-seen species won (a surprising "bear" every frame agreed on).
        if vcfg.enabled and (outcome.agreement < vcfg.trigger_agreement
                             or self._is_rare(outcome.species, vcfg.rare_max_prior)):
            best = v.samples[0]
            verdict = vision_adjudicate(vcfg, best.crop, self._candidate_pool(ballots))
            if verdict:
                print(f"[pipeline] {v.first_seen:%H:%M:%S}  vision tiebreaker: "
                      f"{outcome.species} ({outcome.votes}/{outcome.total}) -> {verdict}")
                # Vision is authoritative; it reads as a confident, clean ID.
                return verdict, vcfg.confidence, best.crop, best.det_conf, vcfg.confidence
            if low_agree:
                print(f"[pipeline] {v.first_seen:%H:%M:%S}  tossed {outcome.species} "
                      f"— frames disagreed ({outcome.votes}/{outcome.total}), vision unsure")
                return None
            # Rare-but-agreed and vision couldn't confirm: keep the vote below.

        # The frames couldn't agree and vision didn't (or couldn't) rescue it.
        if low_agree:
            print(f"[pipeline] {v.first_seen:%H:%M:%S}  tossed {outcome.species} "
                  f"— frames disagreed ({outcome.votes}/{outcome.total} agreed)")
            return None
        # Represent the visit with the winning species' most-confident frame, so
        # the saved crop and the stored confidence come from the same evidence.
        winners = [(tk[0], s) for (tk, s) in ballots if tk[0].species == outcome.species]
        rep_r, rep_s = max(winners, key=lambda rs: rs[0].confidence)
        conf = rep_r.confidence
        # Toss weak matches outright (a squirrel tail scored 0.40 as a titmouse).
        if conf < self.cfg.pipeline.min_confidence:
            print(f"[pipeline] {v.first_seen:%H:%M:%S}  tossed {outcome.species} "
                  f"({conf:.2f} < {self.cfg.pipeline.min_confidence:.2f}) — not a clean match")
            return None
        species = outcome.species
        if conf < self.cfg.classifier.min_confidence:
            species = "Unknown bird"
        return species, conf, rep_s.crop, rep_s.det_conf, outcome.agreement

    def _is_rare(self, species: str, max_prior: int) -> bool:
        """True if this species has few prior records — worth a vision check."""
        try:
            return self.db.species_count(species) <= max_prior
        except Exception:
            return False

    def _candidate_pool(self, ballots) -> list[str]:
        """Distinct species the frames considered (each frame's top-k), ranked by
        votes then best confidence — the shortlist the vision model chooses from.
        Includes runner-ups, so vision can pick a species the vote didn't."""
        from collections import defaultdict

        votes: dict[str, int] = defaultdict(int)
        best_conf: dict[str, float] = defaultdict(float)
        order: list[str] = []
        for topk, _ in ballots:
            for rank, r in enumerate(topk):
                if r.species not in best_conf:
                    order.append(r.species)
                best_conf[r.species] = max(best_conf[r.species], r.confidence)
                if rank == 0:
                    votes[r.species] += 1
        order.sort(key=lambda s: (votes[s], best_conf[s]), reverse=True)
        return order[: self.cfg.vision.max_candidates]

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

    def _post_visit(self, v: _Visit, species: str, conf: float, crop,
                    det_conf: float, agreement: float | None) -> None:
        """Send the visit (metadata + best crop) to a remote dashboard's ingest API."""
        import base64
        import json
        import urllib.request

        import cv2

        ok, buf = cv2.imencode(".jpg", crop)
        payload = json.dumps({
            "token": self.cfg.pipeline.ingest_token,
            "species": species,
            "confidence": conf,
            "detector_conf": det_conf,
            "first_ts": v.first_seen.isoformat(timespec="seconds"),
            "last_ts": v.last_seen.isoformat(timespec="seconds"),
            "frames": v.frames,
            "agreement": agreement,
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
                    self._reap(datetime.now())
                else:
                    try:
                        self.process_frame(frame)
                    except Exception as e:
                        print(f"[pipeline] frame processing failed: {e}")
        except KeyboardInterrupt:
            print("\n[pipeline] stopping…")
        finally:
            self._reap(datetime.now(), flush=True)
            self.camera.release()
            self.db.close()
