"""ClipForge pipeline: pick viral highlight moments from a transcript via Claude."""
import os
import sys
import re
import json

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config


def _mmss(t: float) -> str:
    t = max(0, int(round(t)))
    return "%02d:%02d" % (t // 60, t % 60)


def _compact_transcript(transcript: dict) -> str:
    """One line per segment: `mm:ss  text` — compact context for the model."""
    lines = []
    for seg in transcript.get("segments", []):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        lines.append("%s  %s" % (_mmss(seg.get("start", 0.0)), text))
    return "\n".join(lines)


def _slugify(text: str, fallback: str = "clip") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s[:60] or fallback


def _extract_json_array(text: str):
    """Pull the first top-level JSON array out of the model's reply."""
    # Strip markdown fences if present.
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start = text.find("[")
    if start == -1:
        raise RuntimeError("no JSON array found in model response")
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise RuntimeError("unterminated JSON array in model response")


def pick_highlights(job_dir: str, n: int = 6, guidance: str = "") -> list:
    """Read transcript.json, ask Claude for `n` viral moments, write clips.json."""
    tpath = os.path.join(job_dir, "transcript.json")
    if not os.path.exists(tpath):
        raise RuntimeError("transcript.json not found in %s" % job_dir)
    with open(tpath, "r", encoding="utf-8") as f:
        transcript = json.load(f)

    compact = _compact_transcript(transcript)
    duration = transcript.get("duration", 0.0)

    import llm

    guidance_line = ""
    if guidance and guidance.strip():
        guidance_line = "\nUser steer (prioritise this): %s\n" % guidance.strip()

    system = (
        "You are a short-form video editor who finds viral moments in long "
        "videos for vertical clips (YouTube Shorts / Reels / TikTok). "
        "You pick self-contained moments that have a hook and a payoff."
    )
    prompt = (
        "Below is a timestamped transcript (mm:ss then text per line) of a "
        "video about %.0f seconds long.\n\n"
        "Pick the %d BEST self-contained viral moments. Each must:\n"
        "- stand on its own without outside context (include setup AND payoff),\n"
        "- be between 10 and 60 seconds long,\n"
        "- start slightly BEFORE the setup line and end just AFTER the payoff,\n"
        "- not overlap another pick.\n"
        "%s\n"
        "For each pick also PREDICT its short-form performance as a short-form "
        "editor would, judging the actual content (not guessing):\n"
        "- score: 0-100 overall virality (hook strength + payoff + "
        "shareability + rewatch); be discerning, reserve 85+ for truly strong "
        "moments and spread the rest.\n"
        "- hook: the first-3-seconds hook, quoted or paraphrased in <=8 words.\n"
        "- payoff: the moment that rewards the viewer, in <=8 words.\n"
        "- retention_risk: the single biggest reason a viewer might swipe away, "
        "in <=8 words (e.g. 'slow 4s intro', 'context needed', 'none').\n"
        "- reason: one line on why it performs.\n\n"
        "Return ONLY a strict JSON array, no prose, with exactly these fields "
        "per object:\n"
        '[{"slug":"kebab-case-unique","title":"catchy human title",'
        '"reason":"one line why it is viral","hook":"first-3s hook",'
        '"payoff":"the payoff","retention_risk":"biggest drop-off risk",'
        '"score":<int 0-100>,"start":<seconds float>,'
        '"end":<seconds float>}]\n\n'
        "Transcript:\n%s\n"
        % (float(duration or 0.0), int(n), guidance_line, compact)
    )

    reply = llm.complete(prompt, system=system, max_tokens=4096)

    raw = _extract_json_array(reply)

    clips = []
    seen_slugs = set()
    for item in raw:
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        title = str(item.get("title") or "Untitled clip").strip()
        slug = _slugify(item.get("slug") or title)
        # Ensure uniqueness within the job.
        base = slug
        k = 2
        while slug in seen_slugs:
            slug = "%s-%d" % (base, k)
            k += 1
        seen_slugs.add(slug)

        try:
            score = int(round(float(item.get("score"))))
        except (TypeError, ValueError):
            score = 50
        score = min(100, max(0, score))

        clips.append({
            "slug": slug,
            "title": title,
            "reason": str(item.get("reason") or "").strip(),
            "hook": str(item.get("hook") or "").strip(),
            "payoff": str(item.get("payoff") or "").strip(),
            "retention_risk": str(item.get("retention_risk") or "").strip(),
            "score": score,
            "start": round(start, 2),
            "end": round(end, 2),
            "render": {
                "mode": "blurpad",
                "captions": False,
                "speed": 1.0,
                "zoom": 1.0,
            },
            "status": "pending",
            "file": None,
        })

    if not clips:
        raise RuntimeError("model returned no usable clips")

    # Rank best-first by predicted virality.
    clips.sort(key=lambda c: c.get("score", 0), reverse=True)

    out_path = os.path.join(job_dir, "clips.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(clips, f, ensure_ascii=False, indent=1)

    return clips
