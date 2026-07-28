"""Typed configuration loaded from config.yaml (with env-var overrides).

Usage:
    from birdwatcher.config import load_config
    cfg = load_config()            # reads ./config.yaml if present
    print(cfg.camera.rtsp_url)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

try:
    import yaml  # PyYAML
except ImportError:  # pragma: no cover - yaml is optional for the stub path
    yaml = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@dataclass
class CameraConfig:
    # Full RTSP URL from UniFi Protect, e.g. rtsp://192.168.1.1:7447/abcd1234
    rtsp_url: str = "rtsp://CHANGE_ME"
    # Force TCP transport — far more reliable than UDP for RTSP over wifi/NVR.
    use_tcp: bool = True
    # Seconds to wait before reconnecting after the stream drops.
    reconnect_delay: float = 5.0
    # Which camera this watcher is — tags every sighting so the dashboard can
    # tell the feeder cam from the creek/wildlife cam. Override per watcher.
    source: str = "feeder"


@dataclass
class MotionConfig:
    enabled: bool = True
    # Fraction of the frame (0..1) that must change to count as motion.
    min_area_frac: float = 0.003
    # MOG2 background subtractor sensitivity.
    var_threshold: int = 40
    # After motion, run detection for this many seconds before re-arming the gate.
    active_window: float = 4.0


@dataclass
class DetectorConfig:
    # Ultralytics model. yolov8n.pt is the small/fast default; auto-downloads.
    # Point at MegaDetector weights for a proper wildlife cam.
    model: str = "yolov8n.pt"
    # Minimum YOLO confidence to accept a detection.
    min_confidence: float = 0.35
    # Pad the bounding box by this fraction before cropping (context helps ID).
    crop_pad_frac: float = 0.15
    # Class NAMES to accept (resolved against the loaded model's labels). Feeder
    # cam = ["bird"]; wildlife cam = the broad animal set. BioCLIP does the real
    # species ID on the crop, so the detector just has to spot an animal.
    classes: list[str] = field(default_factory=lambda: ["bird"])
    # Inference image size. 0 = model default (~640). MegaDetector's -1280
    # variants want 1280 to catch small/distant animals in a wide scene.
    imgsz: int = 0


@dataclass
class ClassifierConfig:
    # One of: "tfhub" (local), "claude" (API), "stub" (no ML, for wiring tests).
    backend: str = "stub"
    # Minimum confidence to record a species; below this we store "Unknown bird".
    min_confidence: float = 0.25
    # TF-Hub model handle (only used when backend == "tfhub").
    tfhub_handle: str = "https://tfhub.dev/google/aiy/vision/classifier/birds_V1/1"
    # Claude model id (only used when backend == "claude"). Reads ANTHROPIC_API_KEY.
    claude_model: str = "claude-opus-4-8"
    # BioCLIP model handle (open_clip). bioclip-2 is ~18% more accurate than v1;
    # falls back to v1 automatically if it can't be loaded.
    bioclip_model: str = "hf-hub:imageomics/bioclip-2"
    # Few-shot: blend the reference library into classification. For each species
    # with verified crops in <library_dir>/<slug>/, its class embedding becomes
    # the mean of those crops instead of a generic text prompt — tuning IDs to
    # YOUR actual birds. Species with no crops keep the zero-shot text embedding.
    # Rebuild happens at watcher startup, so restart the watcher after a review
    # session to pick up new examples.
    use_library: bool = True
    library_dir: str = ""   # set by the pipeline from paths (data/library)


@dataclass
class PipelineConfig:
    # A visit stays open while the same bird (matched across frames by box overlap)
    # keeps being seen. It closes after this many seconds with no sighting; then we
    # record ONE row with the best frame. Counts therefore mean visits, not frames.
    visit_timeout: float = 60.0
    # Ignore blips: only record a visit seen in at least this many frames.
    min_visit_frames: int = 2
    # Toss a whole visit if the classifier's best match is below this — filters
    # junk like a squirrel tail that YOLO flagged as a bird and BioCLIP guessed
    # at 0.40. Set 0 to keep everything (falls back to the classifier's
    # "Unknown bird" threshold). Heads-up: mammals can score a touch lower than
    # birds against the bird-heavy label set, so lower this if real critters get
    # tossed.
    min_confidence: float = 0.70
    # Temporal voting: instead of trusting the single sharpest frame, classify
    # several frames of a visit and let them vote on the species. Kills the
    # "one lucky-but-wrong frame decides" failure (a woodpecker mid-turn logged
    # as a beaver) and yields an honest agreement signal. Applies to birds and
    # critters alike; people bypass it (recorded straight from the detector).
    vote_enabled: bool = True
    # How many frames of a visit to classify and vote (the top-N sharpest).
    # Cheap on a GPU — each is one BioCLIP image encode. 1 disables voting.
    vote_max_samples: int = 7
    # Toss a visit whose winning species didn't get at least this fraction of
    # the votes — i.e. the frames couldn't agree, so it's not a clean ID.
    vote_min_agreement: float = 0.5
    # Per-camera known-false-positive labels: drop any visit whose final species
    # lands on one of these, before it's recorded. Some misreads are systematic
    # and location-specific — the creek's IR illuminator lights up moths that
    # BioCLIP confidently calls "Southern Flying Squirrel" (and dark blurs it
    # calls "American Black Bear"), species that never actually occur here. No
    # downstream signal (brightness, sharpness, detector confidence) separates
    # those bug-blobs from real animals, so we blocklist the labels themselves.
    # Empty = record everything.
    reject_species: list[str] = field(default_factory=list)
    # Save the single best cropped bird image per visit.
    save_crops: bool = True
    # If set, POST each visit to this URL (a remote dashboard's /api/ingest)
    # instead of writing to a local DB. Lets a beefy PC do detection while the
    # Pi serves the UI + storage. e.g. "http://192.168.1.138:8000/api/ingest"
    ingest_url: str = ""
    # Shared secret for the ingest endpoint: the sender includes it, the
    # receiver (web app, same field in its config) checks it.
    ingest_token: str = ""


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    debug: bool = False


@dataclass
class Paths:
    db: str = "data/birdwatcher.db"
    captures: str = "data/captures"

    def db_path(self) -> Path:
        return _resolve(self.db)

    def captures_path(self) -> Path:
        return _resolve(self.captures)


@dataclass
class AudioConfig:
    # Read-only path to BirdNET-Go's SQLite DB. Empty = audio integration off.
    birdnet_db: str = ""


@dataclass
class WeatherConfig:
    # Per-hour weather row above the day-drill view (Open-Meteo, no API key).
    enabled: bool = True
    latitude: float = 33.94    # Cobb County, GA (Marietta) — matches the catalog region
    longitude: float = -84.55


@dataclass
class VisionConfig:
    # Vision-LLM tiebreaker: when a visit's frames can't agree on a species (low
    # temporal-vote agreement) or the winner is a rarely-seen species, hand the
    # best crop to a local vision model for a second opinion instead of tossing
    # or trusting a shaky guess. Fully on-box (Ollama) to honor the LAN-only
    # setup — the crop never leaves the network. Off until a vision model is
    # pulled (e.g. `ollama pull qwen2.5vl:7b`) and this is enabled.
    enabled: bool = False
    # Ollama base URL + a *vision-capable* model (qwen2.5vl, llama3.2-vision, …).
    ollama_url: str = "http://ollama:11434"
    model: str = "qwen2.5vl:7b"
    timeout: float = 60.0
    # Fire when frame agreement is below this (the "frames disagreed" case).
    trigger_agreement: float = 0.75
    # Also fire when the voted species has this many or fewer prior records —
    # catches a surprising ID (a "bear") even when every frame agreed on it.
    rare_max_prior: int = 2
    # Cap on how many candidate species (BioCLIP's top guesses across the visit's
    # frames) to offer the vision model to choose among.
    max_candidates: int = 6
    # Stored confidence/agreement when the vision model confirms an ID — a
    # vision-confirmed sighting should read as trustworthy, not tentative.
    confidence: float = 0.85


@dataclass
class NaturalistConfig:
    # The resident naturalist: a local LLM (Ollama) that answers questions about
    # your sightings and writes a daily digest. Off by default until pointed at
    # a running Ollama.
    enabled: bool = False
    # Ollama base URL. Cross-stack, so use the container's network name (put this
    # watcher/web on Ollama's network) or a host-published port.
    ollama_url: str = "http://ollama:11434"
    model: str = "qwen3:14b"
    temperature: float = 0.4
    timeout: float = 120.0


@dataclass
class Config:
    camera: CameraConfig = field(default_factory=CameraConfig)
    motion: MotionConfig = field(default_factory=MotionConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    classifier: ClassifierConfig = field(default_factory=ClassifierConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    web: WebConfig = field(default_factory=WebConfig)
    paths: Paths = field(default_factory=Paths)
    audio: AudioConfig = field(default_factory=AudioConfig)
    weather: WeatherConfig = field(default_factory=WeatherConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    naturalist: NaturalistConfig = field(default_factory=NaturalistConfig)


def _resolve(p: str | os.PathLike[str]) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def _merge(dc: Any, data: dict[str, Any]) -> Any:
    """Recursively overlay a plain dict onto a dataclass instance."""
    if not is_dataclass(dc):
        return data
    valid = {f.name: f for f in fields(dc)}
    for key, value in (data or {}).items():
        if key not in valid:
            continue
        current = getattr(dc, key)
        if is_dataclass(current) and isinstance(value, dict):
            _merge(current, value)
        else:
            setattr(dc, key, value)
    return dc


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load config from YAML; missing file → defaults. Env overrides: BW_CONFIG, RTSP_URL."""
    cfg = Config()
    cfg_path = Path(path) if path else Path(os.getenv("BW_CONFIG", str(DEFAULT_CONFIG_PATH)))
    if cfg_path.exists():
        if yaml is None:
            raise RuntimeError("PyYAML not installed but config.yaml exists; pip install pyyaml")
        with open(cfg_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        _merge(cfg, data)

    # Handy env overrides (don't commit secrets to yaml).
    if os.getenv("RTSP_URL"):
        cfg.camera.rtsp_url = os.environ["RTSP_URL"]

    return cfg
