"""ClipForge pipeline: generate Shorts-optimized YouTube metadata per clip.

Distills the Shorts + metadata SEO rules from the claude-youtube skill
(github.com/AgriciDaniel/claude-youtube, MIT) into a single LLM call via the
shared `llm` backend (CLI subscription by default — no API credits needed).

For each clip it produces, and stores under `clip["youtube"]`:
  {
    "title":       <= 100 chars, keyword front-loaded, ends with #Shorts,
    "description":  front-loaded first 125 chars + CTA to source video + hashtags,
    "tags":        [10-15 tags, joined length <= 500 chars],
    "hashtags":    [3-5, specific -> broad]
  }

Importable as `from pipeline.metadata import generate_metadata` (CWD=ROOT) and
`from clipforge.pipeline.metadata import generate_metadata` (CWD=/home/hakuza).
"""
import os
import sys
import re
import json

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402

TITLE_MAX = 100          # YouTube hard limit
TAGS_TOTAL_MAX = 500     # YouTube hard limit on the joined tag string
TAGS_COUNT_MAX = 15
DESC_MAX = 5000          # YouTube hard limit
_SHORTS_TAG = "#Shorts"

_GOALS = {
    "funnel": "drive viewers to watch the full source video",
    "growth": "maximize new-viewer reach and subscriptions",
    "monetisation": "maximize Shorts watch-time and RPM",
}

_SYSTEM = (
    "You are the YouTube Shorts SEO brain of ClipForge. Given one short vertical "
    "clip (its working title, why it is viral, and a transcript excerpt), you write "
    "a complete, copy-paste-ready upload metadata package optimized for the YouTube "
    "Shorts algorithm (Viewed-vs-Swiped-Away, loop rate). Rules:\n"
    "- title: 1 line, <= 90 characters BEFORE the automatically-appended #Shorts, "
    "front-load the strongest keyword/hook, curiosity-driven, no clickbait lies.\n"
    "- description: the FIRST 125 characters carry all SEO weight, so put the hook + "
    "primary keyword there. Then 1-2 context lines. Natural, human tone.\n"
    "- tags: 10-15, priority order (exact keyword, variations, broader topic); "
    "lowercase; no '#'.\n"
    "- hashtags: 3-5, most specific first, broadest last; each starts with '#'.\n"
    "Respond with ONLY a single JSON object, no prose and no code fences, with keys: "
    'title (string), description (string), tags (array of strings), hashtags (array '
    "of strings)."
)


def _load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _dump_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _strip_fences(text):
    t = (text or "").strip()
    fence = re.match(r"^```[a-zA-Z0-9]*\s*(.*?)\s*```$", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    if not t.startswith("{"):
        first, last = t.find("{"), t.rfind("}")
        if first != -1 and last != -1 and last > first:
            t = t[first:last + 1]
    return t


def _transcript_excerpt(job_dir, start, end, limit=1200):
    """Return the transcript text overlapping [start, end], truncated to `limit`."""
    tpath = os.path.join(job_dir, "transcript.json")
    if not os.path.isfile(tpath):
        return ""
    try:
        tr = _load_json(tpath)
    except Exception:
        return ""
    parts = []
    for seg in tr.get("segments", []):
        s = seg.get("start", 0.0)
        e = seg.get("end", s)
        if e < start or s > end:
            continue
        txt = (seg.get("text") or "").strip()
        if txt:
            parts.append(txt)
    out = " ".join(parts).strip()
    return out[:limit]


def _clean_title(title, fallback):
    t = re.sub(r"\s+", " ", str(title or "").strip()).strip('"').strip()
    t = t.replace("#Shorts", "").replace("#shorts", "").strip()
    if not t:
        t = fallback
    # Reserve room for " #Shorts".
    budget = TITLE_MAX - (len(_SHORTS_TAG) + 1)
    if len(t) > budget:
        t = t[:budget].rstrip()
    return "%s %s" % (t, _SHORTS_TAG)


def _clean_tags(tags):
    out, seen = [], set()
    for tag in tags or []:
        tag = re.sub(r"\s+", " ", str(tag or "").strip()).lstrip("#").strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
        if len(out) >= TAGS_COUNT_MAX:
            break
    # Enforce the joined 500-char limit (YouTube counts the comma-joined string).
    final = []
    for tag in out:
        candidate = final + [tag]
        if len(",".join(candidate)) > TAGS_TOTAL_MAX:
            break
        final = candidate
    return final


def _clean_hashtags(hashtags):
    out, seen = [], set()
    for h in hashtags or []:
        h = str(h or "").strip().split()[0] if str(h or "").strip() else ""
        if not h:
            continue
        if not h.startswith("#"):
            h = "#" + h.lstrip("#")
        key = h.lower()
        if key in seen or key == _SHORTS_TAG.lower():
            continue
        seen.add(key)
        out.append(h)
        if len(out) >= 5:
            break
    return out


def _build_description(desc, hashtags, source_url, source_title, goal):
    body = re.sub(r"\n{3,}", "\n\n", str(desc or "").strip())
    lines = [body] if body else []
    if goal == "funnel" and source_url:
        label = source_title.strip() if source_title else "the full video"
        lines.append("▶ Watch %s: %s" % (label, source_url))
    elif source_url:
        lines.append("From: %s" % source_url)
    tagline = " ".join(hashtags + [_SHORTS_TAG]) if hashtags else _SHORTS_TAG
    lines.append(tagline)
    out = "\n\n".join(p for p in lines if p).strip()
    return out[:DESC_MAX]


def _one(job_dir, clip, goal, source_url, source_title):
    excerpt = _transcript_excerpt(job_dir, clip.get("start", 0.0), clip.get("end", 0.0))
    goal_text = _GOALS.get(goal, _GOALS["funnel"])
    prompt = (
        "Clip working title: %s\n"
        "Why it is viral: %s\n"
        "Clip length: %.0f seconds\n"
        "Source video title: %s\n"
        "Goal: %s\n"
        "Transcript excerpt:\n\"\"\"\n%s\n\"\"\"\n\n"
        "Write the Shorts metadata JSON now."
        % (
            clip.get("title") or clip.get("slug") or "clip",
            clip.get("reason") or "",
            float(clip.get("end", 0.0)) - float(clip.get("start", 0.0)),
            source_title or "(unknown)",
            goal_text,
            excerpt or "(transcript unavailable)",
        )
    )

    import llm
    reply = llm.complete(prompt, system=_SYSTEM, max_tokens=1024)
    try:
        data = json.loads(_strip_fences(reply))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    fallback_title = clip.get("title") or clip.get("slug") or "clip"
    hashtags = _clean_hashtags(data.get("hashtags"))
    meta = {
        "title": _clean_title(data.get("title"), fallback_title),
        "description": _build_description(
            data.get("description"), hashtags, source_url, source_title, goal
        ),
        "tags": _clean_tags(data.get("tags")),
        "hashtags": hashtags,
    }
    clip["youtube"] = meta
    return meta


def generate_metadata(job_dir, slug=None, goal="funnel"):
    """Generate Shorts SEO metadata for one clip (by `slug`) or every clip.

    Loads clips.json, calls the LLM backend per clip, stores the package under
    `clip["youtube"]`, persists clips.json, and returns the updated clip spec(s):
    a single dict when `slug` is given, else the full clips list.

    Raises RuntimeError if clips.json is missing or `slug` matches nothing.
    """
    job_dir = os.path.abspath(job_dir)
    clips_path = os.path.join(job_dir, "clips.json")
    if not os.path.isfile(clips_path):
        raise RuntimeError("clips.json not found in job dir: %s" % clips_path)
    clips = _load_json(clips_path)
    if not isinstance(clips, list):
        raise RuntimeError("clips.json malformed (expected a list): %s" % clips_path)

    source_url, source_title = "", ""
    job_path = os.path.join(job_dir, "job.json")
    if os.path.isfile(job_path):
        try:
            job = _load_json(job_path)
            source_url = job.get("url") or ""
            source_title = job.get("title") or ""
        except Exception:
            pass

    if goal not in _GOALS:
        goal = "funnel"

    if slug is not None:
        clip = next((c for c in clips if isinstance(c, dict) and c.get("slug") == slug), None)
        if clip is None:
            raise RuntimeError("no clip with slug %r in %s" % (slug, clips_path))
        _one(job_dir, clip, goal, source_url, source_title)
        _dump_json(clips_path, clips)
        return clip

    for clip in clips:
        if isinstance(clip, dict):
            _one(job_dir, clip, goal, source_url, source_title)
    _dump_json(clips_path, clips)
    return clips


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Generate Shorts SEO metadata for ClipForge clips.")
    ap.add_argument("job_dir")
    ap.add_argument("--slug", default=None)
    ap.add_argument("--goal", default="funnel", choices=sorted(_GOALS))
    args = ap.parse_args()
    out = generate_metadata(args.job_dir, args.slug, args.goal)
    print(json.dumps(out, ensure_ascii=False, indent=2))
