"""SQLite storage + the queries that power the weekly grid.

Each row in `sightings` is one *visit* (a bird present across one or more frames),
not one detection. The pipeline collapses frames into a visit and writes the best one.
"""

from __future__ import annotations

import sqlite3
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sightings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    species       TEXT NOT NULL,
    confidence    REAL NOT NULL,
    image_path    TEXT,
    detector_conf REAL,
    last_ts       TEXT,
    frames        INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_sightings_ts ON sightings(ts);
CREATE INDEX IF NOT EXISTS idx_sightings_species ON sightings(species);

CREATE TABLE IF NOT EXISTS library_examples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    species     TEXT NOT NULL,
    image_path  TEXT NOT NULL,
    sighting_id INTEGER,
    added_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_library_species ON library_examples(species);
"""

# Columns added after the first release; applied to existing DBs at startup.
_MIGRATIONS = [("last_ts", "TEXT"), ("frames", "INTEGER DEFAULT 1"),
               ("verified_species", "TEXT"), ("rejected", "INTEGER DEFAULT 0"),
               ("source", "TEXT DEFAULT 'feeder'")]


class Database:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL lets a separate writer (the watch container) and readers (the web
        # container) hit the same DB file without "database is locked" errors;
        # busy_timeout makes any remaining contention wait briefly instead of
        # failing. The RLock guards the single shared connection across Flask's
        # threaded server (multi-statement writes stay atomic).
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        have = {r["name"] for r in self._conn.execute("PRAGMA table_info(sightings)")}
        for name, decl in _MIGRATIONS:
            if name not in have:
                self._conn.execute(f"ALTER TABLE sightings ADD COLUMN {name} {decl}")

    def close(self) -> None:
        self._conn.close()

    # --- writes -----------------------------------------------------------
    def add_visit(
        self,
        species: str,
        confidence: float,
        image_path: str | None = None,
        detector_conf: float | None = None,
        first_ts: datetime | None = None,
        last_ts: datetime | None = None,
        frames: int = 1,
        source: str = "feeder",
    ) -> int:
        first_ts = first_ts or datetime.now()
        last_ts = last_ts or first_ts
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO sightings (ts, species, confidence, image_path, detector_conf,"
                " last_ts, frames, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    first_ts.isoformat(timespec="seconds"),
                    species,
                    confidence,
                    image_path,
                    detector_conf,
                    last_ts.isoformat(timespec="seconds"),
                    frames,
                    source,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    # --- human-in-the-loop verification ----------------------------------
    def list_unverified(self, limit: int = 40) -> list[dict]:
        """Recent visits awaiting review (least-confident first), with a crop."""
        rows = self._conn.execute(
            "SELECT id, ts, species, confidence, image_path FROM sightings "
            "WHERE verified_species IS NULL AND image_path IS NOT NULL "
            "AND COALESCE(rejected, 0) = 0 "
            "ORDER BY confidence ASC, ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def set_verified(self, sighting_id: int, species: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sightings SET verified_species = ? WHERE id = ?",
                (species, int(sighting_id)),
            )
            self._conn.commit()

    def review_counts(self) -> tuple[int, int]:
        """(verified, total) sightings — for a progress readout."""
        total = self._conn.execute(
            "SELECT COUNT(*) FROM sightings WHERE COALESCE(rejected, 0) = 0"
        ).fetchone()[0]
        ver = self._conn.execute(
            "SELECT COUNT(*) FROM sightings WHERE verified_species IS NOT NULL "
            "AND COALESCE(rejected, 0) = 0"
        ).fetchone()[0]
        return int(ver), int(total)

    def reject(self, sighting_id: int) -> None:
        """Hide a bad capture everywhere (kept in the DB, just flagged)."""
        with self._lock:
            self._conn.execute(
                "UPDATE sightings SET rejected = 1 WHERE id = ?", (int(sighting_id),)
            )
            self._conn.commit()

    # --- few-shot reference library (verified good crops) -----------------
    def add_library_example(self, species: str, image_path: str,
                            sighting_id: int | None = None) -> None:
        """Record a confirmed good crop as a reference example for `species`."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO library_examples (species, image_path, sighting_id, added_at) "
                "VALUES (?, ?, ?, ?)",
                (species, image_path, sighting_id, datetime.now().isoformat(timespec="seconds")),
            )
            self._conn.commit()

    def library_counts(self) -> dict[str, int]:
        """{species: number of saved reference crops}."""
        rows = self._conn.execute(
            "SELECT species, COUNT(*) AS c FROM library_examples GROUP BY species"
        ).fetchall()
        return {r["species"]: r["c"] for r in rows}

    # --- reads for the UI -------------------------------------------------
    def recent_visits(self, limit: int = 14) -> list[dict]:
        """Most recent visits (newest first) for the live activity feed."""
        rows = self._conn.execute(
            "SELECT id, ts, last_ts, COALESCE(verified_species, species) AS species, "
            "confidence, image_path, frames FROM sightings "
            "WHERE COALESCE(rejected, 0) = 0 ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def day_hours(self, day: date) -> dict:
        """Per-species hourly counts for one day (0..23), for the day-drill view."""
        start = datetime.combine(day, datetime.min.time())
        end = start + timedelta(days=1)
        rows = self._conn.execute(
            "SELECT ts, COALESCE(verified_species, species) AS species, image_path, confidence "
            "FROM sightings WHERE ts >= ? AND ts < ? AND COALESCE(rejected, 0) = 0 ORDER BY ts ASC",
            (start.isoformat(), end.isoformat()),
        ).fetchall()

        agg: dict[str, dict[int, list]] = defaultdict(lambda: defaultdict(list))
        for r in rows:
            ts = datetime.fromisoformat(r["ts"])
            agg[r["species"]][ts.hour].append((r["image_path"], r["confidence"]))

        species_payload = []
        for name, by_hour in agg.items():
            counts = [len(by_hour.get(h, [])) for h in range(24)]
            best = max(
                (item for hour in by_hour.values() for item in hour),
                key=lambda x: x[1], default=(None, 0),
            )
            species_payload.append({
                "name": name, "thumb": best[0], "total": sum(counts), "counts": counts,
            })
        species_payload.sort(key=lambda s: s["total"], reverse=True)
        return {"date": day.isoformat(), "hours": list(range(24)), "species": species_payload}

    def week_grid(self, week_start: date) -> dict:
        """Build the weekly grid payload (Sun..Sat). Each visit counts once."""
        week_end = week_start + timedelta(days=7)
        rows = self._conn.execute(
            "SELECT ts, COALESCE(verified_species, species) AS species, image_path, confidence FROM sightings"
            " WHERE ts >= ? AND ts < ? AND COALESCE(rejected, 0) = 0 ORDER BY ts ASC",
            (week_start.isoformat(), week_end.isoformat()),
        ).fetchall()

        agg: dict[str, dict[int, list]] = defaultdict(lambda: defaultdict(list))
        for r in rows:
            ts = datetime.fromisoformat(r["ts"])
            day_idx = (ts.date() - week_start).days
            if 0 <= day_idx < 7:
                t = ts.strftime("%I:%M %p").lstrip("0")  # %-I is non-portable (Windows)
                agg[r["species"]][day_idx].append(
                    (t, r["image_path"], r["confidence"], ts)
                )

        species_payload = []
        for name, by_day in agg.items():
            counts = [len(by_day.get(i, [])) for i in range(7)]
            times = [[t[0] for t in by_day.get(i, [])] for i in range(7)]
            items = [item for day in by_day.values() for item in day]
            best = max(items, key=lambda x: x[2], default=(None, None, 0, None))
            with_img = [it for it in items if it[1]]  # items carry (t, path, conf, ts)
            latest = max(with_img, key=lambda x: x[3], default=(None, None, 0, None))
            species_payload.append(
                {
                    "name": name,
                    "thumb": best[1],       # sharpest / most confident crop
                    "latest": latest[1],    # most recent crop
                    "total": sum(counts),
                    "counts": counts,
                    "times": times,
                }
            )

        species_payload.sort(key=lambda s: s["total"], reverse=True)
        return {
            "start": week_start.isoformat(),
            "days": [(week_start + timedelta(days=i)).isoformat() for i in range(7)],
            "species": species_payload,
        }

    def species_detail(self, name: str, photo_limit: int = 60) -> dict:
        """Everything about one species for its detail page: every photo, the
        hour-of-day activity pattern, daily counts, and first/last seen."""
        rows = self._conn.execute(
            "SELECT ts, confidence, image_path FROM sightings "
            "WHERE COALESCE(verified_species, species) = ? AND COALESCE(rejected, 0) = 0 "
            "ORDER BY ts DESC",
            (name,),
        ).fetchall()

        hourly = [0] * 24
        by_date: dict[str, int] = {}
        photos: list[dict] = []
        best = 0.0
        first_ts = last_ts = None
        for r in rows:
            ts = datetime.fromisoformat(r["ts"])
            hourly[ts.hour] += 1
            d = ts.date().isoformat()
            by_date[d] = by_date.get(d, 0) + 1
            best = max(best, r["confidence"] or 0.0)
            if last_ts is None:
                last_ts = r["ts"]          # rows are newest-first
            first_ts = r["ts"]             # ...so the final row is the earliest
            if r["image_path"] and len(photos) < photo_limit:
                photos.append({
                    "image_path": r["image_path"],
                    "ts": r["ts"],
                    "confidence": r["confidence"],
                })

        return {
            "name": name,
            "total": len(rows),
            "first_ts": first_ts,
            "last_ts": last_ts,
            "best_confidence": best,
            "hourly": hourly,
            "by_date": by_date,
            "photos": photos,
        }


def week_start_for(d: date) -> date:
    """Sunday that begins the week containing date d (Sun=start, Sat=end)."""
    return d - timedelta(days=(d.weekday() + 1) % 7)
