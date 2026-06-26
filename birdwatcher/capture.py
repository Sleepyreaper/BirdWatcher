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
        if self.cam.use_tcp:
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
        cap = cv2.VideoCapture(self.cam.rtsp_url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # always read the freshest frame
        return cap

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
