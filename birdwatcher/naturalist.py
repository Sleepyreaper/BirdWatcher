"""The resident naturalist — a local-LLM layer over the sightings database.

Turns BirdWatcher's own data into compact context an Ollama model (Qwen3) can
reason over, then answers natural-language questions and writes a daily digest.
Read-only against the DB; talks to Ollama over HTTP. Any failure degrades to a
plain message so the dashboard never breaks.
"""

from __future__ import annotations

import json
import re
import sqlite3
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

from .config import NaturalistConfig

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


# --- context: the DB, summarized for the model -----------------------------
def _con(db_path: str):
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _species_line(rows: list[sqlite3.Row], limit: int = 14) -> str:
    parts = [f"{r['species']} ×{r['n']}" for r in rows[:limit]]
    if len(rows) > limit:
        parts.append(f"+{len(rows) - limit} more")
    return ", ".join(parts) if parts else "nothing yet"


def _certainty_tag(agreement, confidence) -> str | None:
    """A short reliability flag for one sighting, from temporal-vote agreement
    (how many frames of the visit agreed on the species). Falls back to the raw
    match confidence for older rows that predate voting. None = looks solid, no
    need to hedge."""
    if agreement is not None:
        if agreement < 0.55:
            return "very tentative"
        if agreement < 0.75:
            return "tentative"
        return None
    if confidence is not None and confidence < 0.55:
        return "tentative"
    return None


def build_context(db_path: str, region_name: str = "", recent: int = 18) -> str:
    """A compact, human-readable snapshot of recent activity for the LLM."""
    con = _con(db_path)
    today = date.today()
    week_start = today - timedelta(days=6)
    seen = ("COALESCE(verified_species, species)")
    kept = "COALESCE(rejected, 0) = 0"
    lines: list[str] = []

    now = datetime.now()
    lines.append(
        f"BirdWatcher wildlife log for {region_name or 'the backyard'}. "
        f"Current time: {now.strftime('%A %B %d, %Y, %I:%M %p').replace(' 0', ' ')}."
    )
    lines.append('Two cameras: "feeder" (bird feeder) and "creek" (wildlife/creek edge).')

    # today, per camera
    lines.append(f"\nTODAY ({today.isoformat()}) so far:")
    for cam in ("feeder", "creek"):
        rows = con.execute(
            f"SELECT {seen} AS species, COUNT(*) AS n FROM sightings "
            f"WHERE date(ts) = ? AND source = ? AND {kept} "
            "GROUP BY species ORDER BY n DESC",
            (today.isoformat(), cam),
        ).fetchall()
        lines.append(f"  {cam}: {_species_line(rows)}")

    # sightings today whose frames didn't strongly agree — the model should
    # treat these as unconfirmed rather than narrate them as vivid fact.
    unc = con.execute(
        f"SELECT ts, {seen} AS species, source, agreement AS ag FROM sightings "
        f"WHERE date(ts) = ? AND {kept} AND agreement IS NOT NULL AND agreement < 0.75 "
        "ORDER BY agreement ASC LIMIT 8",
        (today.isoformat(),),
    ).fetchall()
    if unc:
        lines.append("\nFLAGGED UNCERTAIN TODAY (frames disagreed — treat as tentative, not confirmed):")
        for r in unc:
            t = datetime.fromisoformat(r["ts"]).strftime("%I:%M%p").lstrip("0")
            lines.append(f"  {t} {r['species']} ({r['source']}) — only {round(r['ag'] * 100)}% frame agreement")

    # last 7 days
    wk = con.execute(
        f"SELECT {seen} AS species, COUNT(*) AS n FROM sightings "
        f"WHERE ts >= ? AND {kept} GROUP BY species ORDER BY n DESC",
        (week_start.isoformat(),),
    ).fetchall()
    wk_total = sum(r["n"] for r in wk)
    lines.append(
        f"\nLAST 7 DAYS: {len(wk)} species, {wk_total} visits. "
        f"Top: {_species_line(wk, 12)}."
    )

    # all-time footprint + first date
    alltime = con.execute(
        f"SELECT COUNT(DISTINCT {seen}) AS s, MIN(date(ts)) AS first FROM sightings WHERE {kept}"
    ).fetchone()
    lines.append(
        f"ON RECORD: {alltime['s']} species since {alltime['first'] or 'recently'}."
    )

    # most recent sightings, newest first — annotated with how well the frames
    # agreed, so the model can speak with calibrated confidence.
    rec = con.execute(
        f"SELECT ts, {seen} AS species, source, ROUND(confidence, 2) AS c, agreement AS ag "
        f"FROM sightings WHERE {kept} ORDER BY ts DESC LIMIT ?",
        (recent,),
    ).fetchall()
    lines.append("\nMOST RECENT:")
    for r in rec:
        t = datetime.fromisoformat(r["ts"]).strftime("%m-%d %I:%M%p").replace(" 0", " ")
        agree = f", {round(r['ag'] * 100)}% agree" if r["ag"] is not None else ""
        tag = _certainty_tag(r["ag"], r["c"])
        flag = f" [{tag}]" if tag else ""
        lines.append(f"  {t}  {r['species']} ({r['source']}{agree}){flag}")

    con.close()
    return "\n".join(lines)


# --- the model ---------------------------------------------------------------
def _chat(cfg: NaturalistConfig, system: str, user: str) -> str:
    payload = json.dumps({
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": system + "\n/no_think"},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": cfg.temperature},
    }).encode()
    req = urllib.request.Request(
        cfg.ollama_url.rstrip("/") + "/api/chat",
        data=payload, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=cfg.timeout) as r:
        data = json.loads(r.read().decode())
    text = data.get("message", {}).get("content", "")
    return _THINK.sub("", text).strip()


_CALIBRATION = (
    "Each sighting is auto-identified by camera, so IDs can be wrong. A frame-"
    "agreement percentage shows how many frames of the visit agreed on the "
    "species; low agreement or a [tentative]/[very tentative] flag means the ID "
    "is shaky. Hedge on those — 'possibly', 'what may have been', 'unconfirmed' — "
    "and never state a tentative ID as fact. Be especially skeptical of a "
    "surprising species (a bear, a rare visitor, an implausible count) that is "
    "flagged uncertain; call it a likely misread rather than describing it vividly."
)

_ASK_SYSTEM = (
    "You are the resident naturalist for a backyard wildlife camera system. "
    "Answer using ONLY the log below — never invent sightings or numbers. If the "
    "log doesn't cover the question, say so plainly. Be concise, warm, and "
    "specific, like a knowledgeable birder friend. Two sentences unless more is "
    "genuinely needed.\n" + _CALIBRATION + "\n\nLOG:\n{context}"
)

_DIGEST_SYSTEM = (
    "You are the resident naturalist for a backyard wildlife camera system. "
    "From the log below, write a short daily field note (2-4 sentences): the "
    "standouts, anything unusual, and the feel of the day. Warm and specific, "
    "not a dry list. Use ONLY what's in the log.\n" + _CALIBRATION + "\n\nLOG:\n{context}"
)


def ask(cfg: NaturalistConfig, db_path: str, question: str, region_name: str = "") -> dict:
    """Answer a natural-language question grounded in the sightings log."""
    if not cfg.enabled:
        return {"ok": False, "answer": "The naturalist is switched off in config."}
    try:
        ctx = build_context(db_path, region_name)
        answer = _chat(cfg, _ASK_SYSTEM.format(context=ctx), question.strip())
        return {"ok": True, "answer": answer or "(no answer)"}
    except (urllib.error.URLError, OSError) as e:
        return {"ok": False, "answer": f"Couldn't reach the local model ({e}). Is Ollama up?"}
    except Exception as e:  # pragma: no cover - defensive
        return {"ok": False, "answer": f"Something went wrong: {e}"}


def digest(cfg: NaturalistConfig, db_path: str, region_name: str = "") -> dict:
    """A short written field-note summary of the day."""
    if not cfg.enabled:
        return {"ok": False, "text": ""}
    try:
        ctx = build_context(db_path, region_name)
        text = _chat(cfg, _DIGEST_SYSTEM.format(context=ctx), "Write today's field note.")
        return {"ok": True, "text": text, "generated": datetime.now().isoformat(timespec="seconds")}
    except Exception as e:
        return {"ok": False, "text": "", "error": str(e)}
