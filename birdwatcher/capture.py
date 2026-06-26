"""RTSP stream reader with auto-reconnect, plus a motion gate.

The motion gate keeps the heavy detector idle while the feeder is empty:
frames flow continuously, but `frames()` only yields a frame for downstream
detection when (a) motion crossed the threshold, or (b) we're inside the
post-motion active window.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = None
    np = None

from .config import CameraConfig, MotionConfig


class MotionGate:
    """MOG2 background-subtraction motion detector over a downscaled frame."""

    def __init__(self, cfg: MotionConfig):
        self.cfg = cfg
        self._bg = cv2.createBackgroundSubtractorMOG2(
            varThreshold=cfg.var_threshold, detectShadows=False
        )
        self._active_until = 0.0

    def is_active(self, frame) -> bool:
        if not self.cfg.enabled:
            return True
        now = time.monotonic()
        small = cv2.resize(frame, (320, 180))
        mask = self._bg.apply(small)
        moved_frac = float((mask > 0).mean())
        if moved_frac >= self.cfg.min_area_frac:
            self._active_until = now + self.cfg.active_window
        return now < self._active_until


class RTSPCamera:
    """Wraps cv2.VideoCapture with TCP transport and reconnect handling."""

    def __init__(self, cam: CameraConfig, motion: MotionConfig):
        if cv2 is None:
            raise RuntimeError("opencv-python not installed; pip install opencv-python")
        self.cam = cam
        self.gate = MotionGate(motion)
        self._cap = None

    def _open(self):
        # FFmpeg options. rtsps:// is RTSP-over-TLS and still runs over TCP, so
        # forcing TCP transport is the reliable default. stimeout (microseconds)
        # makes a bad connection fail fast instead of hanging forever.
        opts = []
        if self.cam.use_tcp:
            opts.append("rtsp_transport;tcp")
        opts.append("stimeout;5000000")
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "|".join(opts)
        cap = cv2.VideoCapture(self.cam.rtsp_url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # always read the freshest frame
        return cap

    def grab_one(self, warmup: int = 10):
        """Open the stream and return one fresh frame (or None).

        Reads a few frames first so the decoder can sync. Used by `run.py test`
        to verify the camera without starting the full pipeline.
        """
        cap = self._open()
        try:
            if not cap.isOpened():
                return None
            frame = None
            for _ in range(max(1, warmup)):
                ok, f = cap.read()
                if ok and f is not None:
                    frame = f
            return frame
        finally:
            cap.release()

    def frames(self) -> Iterator["np.ndarray"]:
        """Yield frames that pass the motion gate; reconnect on failure forever."""
        while True:
            self._cap = self._open()
            if not self._cap.isOpened():
                print(f"[capture] cannot open stream, retrying in {self.cam.reconnect_delay}s")
                time.sleep(self.cam.reconnect_delay)
                continue

            print("[capture] stream connected")
            while True:
                ok, frame = self._cap.read()
                if not ok or frame is None:
                    print("[capture] stream dropped, reconnecting…")
                    break
                if self.gate.is_active(frame):
                    yield frame

            self._cap.release()
            time.sleep(self.cam.reconnect_delay)

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
