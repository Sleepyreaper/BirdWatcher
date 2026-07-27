"""Vision-LLM tiebreaker — a second opinion for the shaky IDs.

Temporal voting (voting.py) decides most visits from BioCLIP alone. But when the
frames can't agree, or a surprising species wins, a single embedding model isn't
enough. This module hands the crop to a *vision* model running locally in Ollama
(qwen2.5vl, llama3.2-vision, …) and asks it to pick the best match from BioCLIP's
own candidate shortlist — a constrained choice, so it can't invent a species.

Local by design: the crop is posted to the on-box Ollama, never off the LAN.
Every failure degrades to None so the pipeline just falls back to the vote.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from .config import VisionConfig

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def _encode(crop) -> str | None:
    """BGR crop -> base64 JPEG (no data: prefix — Ollama wants raw base64)."""
    import base64

    import cv2

    if crop is None or getattr(crop, "size", 0) == 0:
        return None
    ok, buf = cv2.imencode(".jpg", crop)
    if not ok:
        return None
    return base64.b64encode(buf.tobytes()).decode()


def _match_candidate(reply: str, candidates: list[str]) -> str | None:
    """Resolve the model's free-text answer to one of the offered candidates.

    Accepts an exact name, or a reply that clearly contains exactly one
    candidate. Returns None for 'none', empties, or an ambiguous answer."""
    text = _THINK.sub("", reply or "").strip().strip(".\"' ")
    if not text or text.lower() == "none":
        return None
    low = text.lower()
    for c in candidates:                     # exact (case-insensitive) wins
        if low == c.lower():
            return c
    hits = [c for c in candidates if c.lower() in low]
    return hits[0] if len(hits) == 1 else None


def adjudicate(cfg: VisionConfig, crop, candidates: list[str]) -> str | None:
    """Ask the local vision model which candidate best matches the crop.

    Returns the chosen species (one of `candidates`) or None if the model
    couldn't decide, the call failed, or vision is disabled.
    """
    if not cfg.enabled or not candidates:
        return None
    b64 = _encode(crop)
    if b64 is None:
        return None
    options = "\n".join(f"- {c}" for c in candidates)
    prompt = (
        "You are identifying the main animal in a wildlife camera crop. "
        "Choose the SINGLE best match from this list. Reply with ONLY the exact "
        "name from the list and nothing else. If none clearly match, reply "
        f"exactly: none\n\nCandidates:\n{options}"
    )
    payload = json.dumps({
        "model": cfg.model,
        "messages": [{"role": "user", "content": prompt, "images": [b64]}],
        "stream": False,
        "options": {"temperature": 0.0},
    }).encode()
    req = urllib.request.Request(
        cfg.ollama_url.rstrip("/") + "/api/chat",
        data=payload, headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"[vision] tiebreaker call failed: {e}")
        return None
    reply = data.get("message", {}).get("content", "")
    return _match_candidate(reply, candidates)
