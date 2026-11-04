"""ClipForge NL editor.

Takes a free-text instruction for a single clip, asks Claude to translate it into
a strict JSON edit-ops object, applies the ops to the clip spec, persists clips.json,
and re-renders that clip.

Importable as `from editor.nl_edit import apply_instruction` (CWD=/home/hakuza) and
`from editor import nl_edit` (CWD=ROOT). Reads config from clipforge/config.py.
"""
import os
import sys
import json
import re

# Make the project root importable whether CWD is /home/hakuza or ROOT.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL  # noqa: E402

# ---- documented clamp ranges -------------------------------------------------
_MODES = ("blurpad", "centercrop", "facecrop")
_SPEED_MIN, _SPEED_MAX = 0.5, 2.0
_ZOOM_MIN, _ZOOM_MAX = 1.0, 1.5

# ---- the strict edit-ops tool schema handed to Claude ------------------------
_EDIT_TOOL = {
    "name": "apply_edit",
    "description": (
        "Translate a natural-language editing instruction for a single short vertical "
        "video clip into structured edit operations. Only include the keys that the "
        "instruction actually asks to change; omit everything else. All times are in "
        "seconds relative to the SOURCE video timeline."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "start_abs": {
                "type": "number",
                "description": "New absolute clip start time in seconds (source timeline).",
            },
            "end_abs": {
                "type": "number",
                "description": "New absolute clip end time in seconds (source timeline).",
            },
            "start_delta": {
                "type": "number",
                "description": (
                    "Shift the start time by this many seconds. Negative moves the start "
                    "earlier (clip gets longer at the front), positive trims the front."
                ),
            },
            "end_delta": {
                "type": "number",
                "description": (
                    "Shift the end time by this many seconds. Positive extends the end "
                    "(clip gets longer), negative trims the end (clip gets shorter)."
                ),
            },
            "mode": {
                "type": "string",
                "enum": list(_MODES),
                "description": (
                    "Framing mode. blurpad = blurred background padding; centercrop = crop "
                    "to fill; facecrop = crop to the speaker/face."
                ),
            },
            "captions": {
                "type": "boolean",
                "description": "Whether to burn word-pop captions onto the clip.",
            },
            "speed": {
                "type": "number",
                "description": "Playback speed multiplier, 0.5 (slower) to 2.0 (faster).",
            },
            "zoom": {
                "type": "number",
                "description": "Foreground zoom/scale, 1.0 (none) to 1.5 (max).",
            },
            "title": {
                "type": "string",
                "description": "New human/display title for the clip.",
            },
        },
        "additionalProperties": False,
    },
}

_SYSTEM = (
    "You are the editing brain of ClipForge, a tool that turns a clip spec plus a "
    "free-text instruction into structured edit operations. You will be given the "
    "current clip spec as JSON and a natural-language instruction. Call the apply_edit "
    "tool exactly once with ONLY the fields the instruction changes. Rules:\n"
    "- 'make it N seconds shorter' => end_delta: -N (unless it clearly means trim the "
    "start).\n"
    "- 'make it N seconds longer' => end_delta: +N.\n"
    "- 'start N seconds earlier/later' => start_delta.\n"
    "- 'start at MM:SS' or 'start at T' => start_abs in seconds.\n"
    "- 'add captions'/'subtitles' => captions: true; 'remove captions' => captions: false.\n"
    "- 'crop to speaker/face' => mode: facecrop; 'center crop'/'fill' => mode: centercrop; "
    "'blur background'/'blurred padding' => mode: blurpad.\n"
    "- 'speed up'/'2x' etc => speed; 'slow down'/'0.5x' => speed.\n"
    "- 'zoom in' => zoom (1.0-1.5).\n"
    "- 'rename'/'title it X'/'call it X' => title.\n"
    "Do not include fields that are not requested. Respond with ONLY a single JSON "
    "object, no prose and no code fences, containing just the changed fields "
    "(allowed keys: start_abs, end_abs, start_delta, end_delta, mode, captions, "
    "speed, zoom, title). If nothing should change, return {}."
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
    """Strip markdown code fences and surrounding prose, return a best-effort JSON string."""
    t = text.strip()
    # Remove ```json ... ``` or ``` ... ``` fences.
    fence = re.match(r"^```[a-zA-Z0-9]*\s*(.*?)\s*```$", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    # If there is still surrounding prose, grab the outermost {...}.
    if not t.startswith("{"):
        first = t.find("{")
        last = t.rfind("}")
        if first != -1 and last != -1 and last > first:
            t = t[first:last + 1]
    return t


def _extract_ops(resp):
    """Pull the edit-ops dict out of an anthropic Messages response.

    Prefers a tool_use block; falls back to parsing text as JSON (fence-tolerant).
    """
    tool_input = None
    text_chunks = []
    for block in getattr(resp, "content", []) or []:
        btype = getattr(block, "type", None)
        if btype == "tool_use" and getattr(block, "name", None) == "apply_edit":
            tool_input = getattr(block, "input", None)
            break
        if btype == "text":
            text_chunks.append(getattr(block, "text", "") or "")
    if isinstance(tool_input, dict):
        return tool_input
    # Fallback: parse text content as JSON.
    raw = "\n".join(c for c in text_chunks if c).strip()
    if raw:
        try:
            return json.loads(_strip_fences(raw))
        except Exception:
            pass
    return {}


def _get_ops(clip, instruction, duration):
    """Call the LLM backend and return the raw ops dict (may be empty on failure)."""
    import importlib
    llm = importlib.import_module("llm")

    spec_json = json.dumps(clip, ensure_ascii=False, indent=1)
    user_msg = (
        "Current clip spec (JSON):\n```json\n" + spec_json + "\n```\n"
        + ("Source video duration (seconds): %s\n" % duration if duration else "")
        + "\nInstruction:\n" + instruction.strip()
        + "\n\nReturn ONLY a JSON object with just the fields this instruction changes."
    )
    reply = llm.complete(user_msg, system=_SYSTEM, max_tokens=1024)
    try:
        ops = json.loads(_strip_fences(reply))
        return ops if isinstance(ops, dict) else {}
    except Exception:
        return {}


def _as_number(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _apply_ops(clip, ops, duration):
    """Mutate `clip` in place according to `ops`, clamping to documented ranges."""
    render = clip.setdefault("render", {})
    render.setdefault("mode", "blurpad")
    render.setdefault("captions", False)
    render.setdefault("speed", 1.0)
    render.setdefault("zoom", 1.0)

    start = _as_number(clip.get("start"))
    end = _as_number(clip.get("end"))
    if start is None:
        start = 0.0
    if end is None:
        end = start + 1.0

    # --- timing: absolute overrides, then deltas ---
    if _as_number(ops.get("start_abs")) is not None:
        start = _as_number(ops.get("start_abs"))
    if _as_number(ops.get("end_abs")) is not None:
        end = _as_number(ops.get("end_abs"))
    if _as_number(ops.get("start_delta")) is not None:
        start = start + _as_number(ops.get("start_delta"))
    if _as_number(ops.get("end_delta")) is not None:
        end = end + _as_number(ops.get("end_delta"))

    # Bound to [0, duration].
    upper = duration if (duration and duration > 0) else None
    start = max(0.0, start)
    if upper is not None:
        start = min(start, upper)
        end = min(end, upper)
    # Guarantee start < end with at least a 0.5s window.
    if end <= start:
        end = start + 0.5
        if upper is not None and end > upper:
            end = upper
            start = max(0.0, end - 0.5)
    clip["start"] = round(start, 3)
    clip["end"] = round(end, 3)

    # --- mode ---
    mode = ops.get("mode")
    if isinstance(mode, str) and mode.strip().lower() in _MODES:
        render["mode"] = mode.strip().lower()

    # --- captions ---
    if "captions" in ops and ops["captions"] is not None:
        cap = ops["captions"]
        if isinstance(cap, str):
            cap = cap.strip().lower() in ("true", "yes", "on", "1")
        render["captions"] = bool(cap)

    # --- speed ---
    sp = _as_number(ops.get("speed"))
    if sp is not None:
        render["speed"] = round(_clamp(sp, _SPEED_MIN, _SPEED_MAX), 3)

    # --- zoom ---
    zm = _as_number(ops.get("zoom"))
    if zm is not None:
        render["zoom"] = round(_clamp(zm, _ZOOM_MIN, _ZOOM_MAX), 3)

    # --- title ---
    title = ops.get("title")
    if isinstance(title, str) and title.strip():
        clip["title"] = title.strip()

    return clip


def apply_instruction(job_dir, slug, instruction):
    """Apply a free-text editing instruction to the clip identified by `slug`.

    Loads the clip spec from <job_dir>/clips.json, asks Claude to emit strict JSON
    edit ops, applies and clamps them, persists clips.json, re-renders the clip via
    pipeline.render.render_clip, and returns the updated clip spec dict.

    Uses the configured LLM backend (CLI subscription or API). Raises RuntimeError
    if clips.json is missing or no clip matches `slug`.
    """
    job_dir = os.path.abspath(job_dir)
    clips_path = os.path.join(job_dir, "clips.json")
    if not os.path.isfile(clips_path):
        raise RuntimeError("clips.json not found in job dir: %s" % clips_path)

    clips = _load_json(clips_path)
    if not isinstance(clips, list):
        raise RuntimeError("clips.json malformed (expected a list): %s" % clips_path)

    index = None  # 1-based position for NN_slug naming
    clip = None
    for i, c in enumerate(clips):
        if isinstance(c, dict) and c.get("slug") == slug:
            clip = c
            index = i + 1
            break
    if clip is None:
        raise RuntimeError("no clip with slug %r in %s" % (slug, clips_path))

    # Source duration from job.json (used to bound start/end).
    duration = None
    job_path = os.path.join(job_dir, "job.json")
    if os.path.isfile(job_path):
        try:
            duration = _as_number(_load_json(job_path).get("duration"))
        except Exception:
            duration = None

    ops = _get_ops(clip, instruction, duration)
    if not isinstance(ops, dict):
        ops = {}
    _apply_ops(clip, ops, duration)

    # Mark pending so a failed/skipped render is not advertised as fresh.
    clip["status"] = "pending"

    # Persist the edited spec before rendering so the edit is never lost.
    _dump_json(clips_path, clips)

    # Re-render the single clip. The renderer is written by another agent; import
    # lazily so this module loads even if pipeline.render is absent.
    try:
        from pipeline.render import render_clip
    except Exception as exc:
        clip["_render_error"] = "render unavailable: %s" % exc
        _dump_json(clips_path, clips)
        return clip

    try:
        render_clip(job_dir, clip, index)
        # render_clip sets clip["status"]="rendered" and clip["file"]; persist it.
        _dump_json(clips_path, clips)
        clip.pop("_render_error", None)
    except Exception as exc:
        clip["_render_error"] = "render failed: %s" % exc
        _dump_json(clips_path, clips)

    return clip


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Apply an NL edit to a ClipForge clip.")
    ap.add_argument("job_dir")
    ap.add_argument("slug")
    ap.add_argument("instruction")
    args = ap.parse_args()
    out = apply_instruction(args.job_dir, args.slug, args.instruction)
    print(json.dumps(out, ensure_ascii=False, indent=2))
