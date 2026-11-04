"""ClipForge pipeline: Claude-powered creative assists for a clip.

Uses the shared `llm` backend (subscription CLI by default — no API credits).
Each function reads the clip's transcript window and returns structured JSON the
UI renders. Everything degrades to an empty-but-valid shape on failure so the
endpoint never 500s on a model hiccup.

    broll_shots(job_dir, clip)     -> {"shots":[{"line","idea","search":[...]}]}
    music_keywords(job_dir, clip)  -> {"mood","genres":[...],"search":[...],"cues":[...]}
    thumbnail_text(job_dir, clip)  -> {"options":[{"text","style"}]}
"""
import os
import sys
import json

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from pipeline.metadata import _strip_fences, _transcript_excerpt  # noqa: E402


def _ask(prompt, system, max_tokens=900):
    import llm
    reply = llm.complete(prompt, system=system, max_tokens=max_tokens)
    try:
        return json.loads(_strip_fences(reply))
    except Exception:
        return None


def _ctx(job_dir, clip):
    excerpt = _transcript_excerpt(job_dir, float(clip["start"]), float(clip["end"]), limit=1400)
    title = clip.get("title") or clip.get("slug") or ""
    return title, excerpt


def broll_shots(job_dir: str, clip: dict) -> dict:
    title, excerpt = _ctx(job_dir, clip)
    system = (
        "You are a short-form video editor. Given a clip's transcript, propose "
        "concrete B-roll shots to cut over specific lines, with stock-site search "
        "terms (Pexels/Pixabay/Mixkit). Return ONLY JSON."
    )
    prompt = (
        'Clip title: "%s"\nTranscript:\n"""%s"""\n\n'
        "Return JSON exactly:\n"
        '{"shots":[{"line":"the spoken line this covers","idea":"what to show",'
        '"search":["term1","term2"]}]}\n'
        "6-9 shots, tight and literal, search terms 2-4 words."
        % (title, excerpt)
    )
    data = _ask(prompt, system)
    if not isinstance(data, dict) or not isinstance(data.get("shots"), list):
        return {"shots": []}
    return {"shots": data["shots"][:9]}


def music_keywords(job_dir: str, clip: dict) -> dict:
    title, excerpt = _ctx(job_dir, clip)
    system = (
        "You are a music supervisor for short-form video. Suggest royalty-free "
        "music direction and where to place energy changes. Return ONLY JSON."
    )
    prompt = (
        'Clip title: "%s"\nTranscript:\n"""%s"""\n\n'
        "Return JSON exactly:\n"
        '{"mood":"one phrase","genres":["..."],'
        '"search":["term for YouTube Audio Library / Pixabay"],'
        '"cues":["where to drop/raise music, e.g. hit hard on the punchline"]}\n'
        "genres/search 3-6 items, cues 2-4 items."
        % (title, excerpt)
    )
    data = _ask(prompt, system)
    if not isinstance(data, dict):
        return {"mood": "", "genres": [], "search": [], "cues": []}
    for k in ("genres", "search", "cues"):
        if not isinstance(data.get(k), list):
            data[k] = []
    data["mood"] = data.get("mood") or ""
    return data


def thumbnail_text(job_dir: str, clip: dict) -> dict:
    title, excerpt = _ctx(job_dir, clip)
    system = (
        "You write punchy thumbnail overlay text for YouTube Shorts. 2-4 words, "
        "high curiosity, ALL-CAPS friendly. Return ONLY JSON."
    )
    prompt = (
        'Clip title: "%s"\nTranscript:\n"""%s"""\n\n'
        "Return JSON exactly:\n"
        '{"options":[{"text":"2-4 WORDS","style":"why it works in a few words"}]}\n'
        "5 options."
        % (title, excerpt)
    )
    data = _ask(prompt, system)
    if not isinstance(data, dict) or not isinstance(data.get("options"), list):
        return {"options": []}
    return {"options": data["options"][:5]}
