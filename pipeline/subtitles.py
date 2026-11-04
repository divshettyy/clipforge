"""ClipForge pipeline: export subtitle files (.srt / .ass) for a clip from the
already-computed transcript.json. No extra deps — reuses the word-timing logic
in pipeline.render. Timestamps are rebased so the clip starts at 00:00.

    write_srt(job_dir, clip) -> absolute path to clips/<slug>.srt
    write_ass(job_dir, clip, index) -> absolute path to the burn-in ASS
"""
import os
import sys
import json

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pipeline import render as _render  # noqa: E402


def _srt_time(t: float) -> str:
    if t < 0:
        t = 0.0
    ms = int(round(t * 1000))
    h = ms // 3600000; ms -= h * 3600000
    m = ms // 60000;   ms -= m * 60000
    s = ms // 1000;    ms -= s * 1000
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def _load_transcript(job_dir):
    tpath = os.path.join(job_dir, "transcript.json")
    if not os.path.isfile(tpath):
        raise RuntimeError("transcript.json not found in %s" % job_dir)
    with open(tpath, "r", encoding="utf-8") as f:
        return json.load(f)


def write_srt(job_dir: str, clip: dict, words_per_line: int = 7) -> str:
    """Write a .srt for the clip window, grouping words into short lines."""
    start = float(clip["start"]); end = float(clip["end"])
    transcript = _load_transcript(job_dir)
    words = _render._collect_words(transcript, start, end)  # [(rs, re, text), ...] rebased

    clips_dir = os.path.join(job_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)
    slug = clip.get("slug") or "clip"
    out_path = os.path.join(clips_dir, "%s.srt" % slug)

    lines = []
    idx = 1
    for i in range(0, len(words), words_per_line):
        group = words[i:i + words_per_line]
        if not group:
            continue
        ls = group[0][0]
        le = max(g[1] for g in group)
        if le <= ls:
            le = ls + 0.4
        text = " ".join(g[2] for g in group).strip()
        lines.append("%d\n%s --> %s\n%s\n" % (idx, _srt_time(ls), _srt_time(le), text))
        idx += 1

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out_path


def write_ass(job_dir: str, clip: dict, index: int = 1) -> str:
    """Write the 4-word karaoke-style ASS (same look as burned captions)."""
    start = float(clip["start"]); end = float(clip["end"])
    transcript = _load_transcript(job_dir)
    clips_dir = os.path.join(job_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)
    slug = clip.get("slug") or ("clip%02d" % index)
    out_path = os.path.join(clips_dir, "%s.ass" % slug)
    _render._build_ass(transcript, start, end, out_path)
    return out_path
