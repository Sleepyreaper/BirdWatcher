"""Read-only reader for BirdNET-Go's SQLite DB (acoustic detections).

Maps BirdNET-Go detections (by scientific name) onto our species catalog so the
dashboard can show what was *heard* alongside what was *seen*. Opens the DB
read-only so it never interferes with BirdNET-Go's writes; any error yields an
empty result (the audio layer simply doesn't appear).

BirdNET-Go schema (relevant bits):
  detections(detected_at INTEGER epoch, confidence, label_id, unlikely, ...)
  labels(id, scientific_name, ...)
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path


class BirdnetGoReader:
    def __init__(self, db_path: str | Path | None):
        self.db_path = Path(db_path) if db_path else None

    def available(self) -> bool:
        return bool(self.db_path) and self.db_path.exists() and self.db_path.is_file()

    def _connect(self):
        if not self.available():
            return None
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=2)

    @staticmethod
    def _safe_dt(epoch) -> datetime | None:
        try:
            return datetime.fromtimestamp(int(epoch))
        except (TypeError, ValueError, OSError, OverflowError):
            return None

    def _detections_between(self, start: datetime, end: datetime) -> list[tuple]:
        """Raw (epoch, scientific_name) rows in [start, end). [] on any error."""
        if not self.available():
            return []
        try:
            con = self._connect()
            if con is None:
                return []
            try:
                return con.execute(
                    "SELECT d.detected_at, l.scientific_name FROM detections d "
                    "JOIN labels l ON l.id = d.label_id "
                    "WHERE d.detected_at >= ? AND d.detected_at < ? "
                    "AND COALESCE(d.unlikely, 0) = 0",
                    (int(start.timestamp()), int(end.timestamp())),
                ).fetchall()
            finally:
                con.close()
        except Exception:
            return []

    def heard_week(self, week_start: date, sci_to_common: dict[str, str]) -> dict[str, list[int]]:
        """Return {catalog common_name: [7 daily heard-counts]} for the week.

        Only species present in our catalog (via sci_to_common) are kept.
        """
        if not self.available():
            return {}
        start = datetime.combine(week_start, time.min)
        end = start + timedelta(days=7)
        try:
            con = self._connect()
            if con is None:
                return {}
            try:
                rows = con.execute(
                    "SELECT d.detected_at, l.scientific_name FROM detections d "
                    "JOIN labels l ON l.id = d.label_id "
                    "WHERE d.detected_at >= ? AND d.detected_at < ? "
                    "AND COALESCE(d.unlikely, 0) = 0",
                    (int(start.timestamp()), int(end.timestamp())),
                ).fetchall()
            finally:
                con.close()
        except Exception:
            return {}

        out: dict[str, list[int]] = {}
        for epoch, sci in rows:
            if not isinstance(sci, str):
                continue
            common = sci_to_common.get(sci)
            if not common:
                continue
            dt = self._safe_dt(epoch)
            if dt is None:
                continue
            day = (dt.date() - week_start).days
            if 0 <= day < 7:
                out.setdefault(common, [0] * 7)[day] += 1
        return out

    def heard_week_all(self, week_start: date) -> dict[str, list[int]]:
        """Like heard_week but UNFILTERED — every species heard, keyed by
        scientific name (no catalog mapping). The caller resolves display names.
        """
        start = datetime.combine(week_start, time.min)
        out: dict[str, list[int]] = {}
        for epoch, sci in self._detections_between(start, start + timedelta(days=7)):
            if not isinstance(sci, str):
                continue
            dt = self._safe_dt(epoch)
            if dt is None:
                continue
            day = (dt.date() - week_start).days
            if 0 <= day < 7:
                out.setdefault(sci, [0] * 7)[day] += 1
        return out

    def heard_day_hours(self, day: date) -> dict[str, list[int]]:
        """Per-scientific hourly heard counts (0..23) for one day, unfiltered —
        powers the 'heard by hour' grid in the day view."""
        start = datetime.combine(day, time.min)
        out: dict[str, list[int]] = {}
        for epoch, sci in self._detections_between(start, start + timedelta(days=1)):
            if not isinstance(sci, str):
                continue
            dt = self._safe_dt(epoch)
            if dt is None:
                continue
            out.setdefault(sci, [0] * 24)[dt.hour] += 1
        return out

    def heard_total(self, scientific_name: str) -> int:
        """All-time detection count for a scientific name (0 if none/unavailable)."""
        if not self.available() or not scientific_name:
            return 0
        try:
            con = self._connect()
            if con is None:
                return 0
            try:
                row = con.execute(
                    "SELECT COUNT(*) FROM detections d JOIN labels l ON l.id = d.label_id "
                    "WHERE l.scientific_name = ? AND COALESCE(d.unlikely, 0) = 0",
                    (scientific_name,),
                ).fetchone()
                return int(row[0]) if row else 0
            finally:
                con.close()
        except Exception:
            return 0

    def recent(self, sci_to_common: dict[str, str], limit: int = 14) -> list[dict]:
        """Most recent heard detections (newest first), mapped to our catalog."""
        if not self.available():
            return []
        try:
            con = self._connect()
            if con is None:
                return []
            try:
                rows = con.execute(
                    "SELECT d.detected_at, d.confidence, l.scientific_name FROM detections d "
                    "JOIN labels l ON l.id = d.label_id "
                    "WHERE COALESCE(d.unlikely, 0) = 0 "
                    "ORDER BY d.detected_at DESC LIMIT ?",
                    (max(1, limit) * 5,),
                ).fetchall()
            finally:
                con.close()
        except Exception:
            return []

        out: list[dict] = []
        for epoch, conf, sci in rows:
            if not isinstance(sci, str):
                continue
            common = sci_to_common.get(sci)
            if not common:
                continue
            dt = self._safe_dt(epoch)
            if dt is None:
                continue
            try:
                conf_f = float(conf or 0)
            except (TypeError, ValueError):
                conf_f = 0.0
            out.append({
                "name": common,
                "scientific": sci,
                "confidence": conf_f,
                "ts": dt.isoformat(timespec="seconds"),
            })
            if len(out) >= limit:
                break
        return out
