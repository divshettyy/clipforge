"""ClipForge FastAPI server.

Launched from ROOT (/home/hakuza/clipforge) with:
    /home/hakuza/venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8800

Only `config` is imported at module top. The sibling pipeline/editor/youtube/resolve
modules are imported LAZILY inside the worker and endpoint functions (wrapped in
try/except) so the server always starts and can serve the demo job even when those
modules are missing or broken. When a request needs an unavailable module the server
returns HTTP 503 with a JSON error naming the module.
"""
import json
import mimetypes
import os
import shutil
import threading
import uuid
from importlib import import_module

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import config
import validation

app = FastAPI(title="ClipForge")

# Only one transcription may run at a time. faster-whisper (int8 CPU) spins up a
# thread pool per call; letting several run concurrently in this single process
# oversubscribes the cores and makes every job far slower (and look "stuck").
# Serializing the heavy stage keeps each transcribe at its ~real duration.
_TRANSCRIBE_SEM = threading.Semaphore(1)

# Ensure the data dir exists so listing/creation never explodes.
os.makedirs(config.DATA_DIR, exist_ok=True)

STATIC_DIR = os.path.join(config.ROOT, "static")
os.makedirs(STATIC_DIR, exist_ok=True)


# --------------------------------------------------------------------------- #
# Small JSON / job.json helpers
# --------------------------------------------------------------------------- #
def _read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _job_json_path(job_id):
    return os.path.join(config.job_dir(job_id), "job.json")


def _clips_json_path(job_id):
    return os.path.join(config.job_dir(job_id), "clips.json")


def _load_job(job_id):
    path = _job_json_path(job_id)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail={"error": f"job '{job_id}' not found"})
    try:
        return _read_json(path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": f"corrupt job.json: {exc}"})


def _load_clips(job_id):
    path = _clips_json_path(job_id)
    if not os.path.isfile(path):
        return []
    try:
        return _read_json(path)
    except Exception:
        return []


def _set_status(job_id, status=None, error=None, **extra):
    """Update job.json in place, tolerant of a missing file."""
    path = _job_json_path(job_id)
    try:
        data = _read_json(path)
    except Exception:
        data = {"id": job_id}
    if status is not None:
        data["status"] = status
    if error is not None or status == "error":
        data["error"] = error
    data.update(extra)
    try:
        _write_json(path, data)
    except Exception:
        pass


def _video_key(url):
    """Reduce a YouTube URL to its video id so near-duplicate links (extra
    &list=…/&start_radio=… params, youtu.be short form) dedupe to one job."""
    import re
    if not url:
        return ""
    m = re.search(r"(?:youtu\.be/|youtube\.com/shorts/)([A-Za-z0-9_-]{6,})", url)
    if m:
        return m.group(1)
    m = re.search(r"[?&]v=([A-Za-z0-9_-]{6,})", url)
    if m:
        return m.group(1)
    return url.strip()


def _find_existing_job(url):
    """Return an existing job dict with the same video key whose status is not
    'error', else None. Lets create_job return the prior job instead of a dup."""
    key = _video_key(url)
    if not key:
        return None
    try:
        entries = sorted(os.listdir(config.DATA_DIR))
    except FileNotFoundError:
        return None
    for name in entries:
        path = os.path.join(config.DATA_DIR, name, "job.json")
        if not os.path.isfile(path):
            continue
        try:
            data = _read_json(path)
        except Exception:
            continue
        if data.get("status") == "error":
            continue
        if _video_key(data.get("url", "")) == key:
            return data
    return None


def _import_or_503(module_path):
    """Lazily import a sibling module, raising HTTP 503 naming it on failure."""
    try:
        return import_module(module_path)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": f"module '{module_path}' unavailable: {exc}",
                    "module": module_path},
        )


# --------------------------------------------------------------------------- #
# Background worker: download -> extract_audio -> transcribe -> pick -> render
# --------------------------------------------------------------------------- #
def _run_job(job_id, url, n, guidance):
    jd = config.job_dir(job_id)

    # Lazy imports of the sibling pipeline modules. If any is missing/broken the
    # job fails cleanly with the status recorded in job.json.
    try:
        download = import_module("pipeline.download").download
        transcribe_mod = import_module("pipeline.transcribe")
        extract_audio = transcribe_mod.extract_audio
        transcribe = transcribe_mod.transcribe
        pick_highlights = import_module("pipeline.highlight").pick_highlights
        render_all = import_module("pipeline.render").render_all
    except Exception as exc:
        _set_status(job_id, status="error",
                    error=f"pipeline module unavailable: {exc}")
        return

    try:
        _set_status(job_id, status="downloading")
        info = download(url, jd) or {}
        extra = {}
        if info.get("title") is not None:
            extra["title"] = info["title"]
        if info.get("duration") is not None:
            extra["duration"] = info["duration"]
        if extra:
            _set_status(job_id, **extra)

        extract_audio(jd)
        # Wait for the single transcription slot. Reflect the wait honestly so the
        # UI can show the job is queued behind another transcribe rather than hung.
        if not _TRANSCRIBE_SEM.acquire(blocking=False):
            _set_status(job_id, status="waiting-transcribe")
            _TRANSCRIBE_SEM.acquire()
        try:
            _set_status(job_id, status="transcribing")
            transcribe(jd)
        finally:
            _TRANSCRIBE_SEM.release()

        _set_status(job_id, status="picking")
        pick_highlights(jd, n=n, guidance=guidance)

        _set_status(job_id, status="rendering")
        render_all(jd)

        _set_status(job_id, status="ready", error=None)
    except Exception as exc:
        _set_status(job_id, status="error", error=str(exc))


def _run_reclip(job_id, n, prompt):
    """Re-pick highlights for an already-transcribed job from a free-text prompt
    ("Clip Anything"), then render. Reuses the existing transcript — no
    re-download / re-transcribe. Mirrors the pick->render tail of _run_job."""
    jd = config.job_dir(job_id)
    try:
        pick_highlights = import_module("pipeline.highlight").pick_highlights
        render_all = import_module("pipeline.render").render_all
    except Exception as exc:
        _set_status(job_id, status="error", error=f"pipeline module unavailable: {exc}")
        return
    try:
        _set_status(job_id, status="picking", error=None)
        pick_highlights(jd, n=n, guidance=prompt)
        _set_status(job_id, status="rendering")
        render_all(jd)
        _set_status(job_id, status="ready", error=None)
    except Exception as exc:
        _set_status(job_id, status="error", error=str(exc))


# --------------------------------------------------------------------------- #
# Media path-traversal-safe resolution
# --------------------------------------------------------------------------- #
def _safe_media_path(job_id, rel_path):
    job_root = os.path.realpath(config.job_dir(job_id))
    if not os.path.isdir(job_root):
        raise HTTPException(status_code=404, detail={"error": f"job '{job_id}' not found"})
    # Strip any leading slashes so os.path.join cannot escape to an absolute path.
    rel = rel_path.lstrip("/")
    target = os.path.realpath(os.path.join(job_root, rel))
    if target != job_root and not target.startswith(job_root + os.sep):
        raise HTTPException(status_code=403, detail={"error": "path traversal rejected"})
    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail={"error": "file not found"})
    return target


# --------------------------------------------------------------------------- #
# API endpoints
# --------------------------------------------------------------------------- #
@app.post("/api/jobs")
async def create_job(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if not body.get("url"):
        raise HTTPException(status_code=400, detail={"error": "missing 'url'"})
    try:
        url = validation.clean_url(body.get("url"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": str(e)})
    n = validation.clamp_clips(body.get("n"))
    guidance = validation.clean_guidance(body.get("guidance"))
    force = bool((body or {}).get("force"))

    # Dedup: same video already has a (non-error) job -> return it instead of
    # re-downloading/re-transcribing. Pass {"force":true} to bypass.
    if not force:
        existing = _find_existing_job(url)
        if existing:
            return {"job_id": existing.get("id"), "deduped": True,
                    "status": existing.get("status")}

    job_id = "job-" + uuid.uuid4().hex[:12]
    jd = config.job_dir(job_id)
    os.makedirs(jd, exist_ok=True)
    _write_json(_job_json_path(job_id), {
        "id": job_id, "url": url, "title": "", "duration": 0,
        "status": "queued", "error": None,
    })

    threading.Thread(target=_run_job, args=(job_id, url, n, guidance),
                     daemon=True).start()
    return {"job_id": job_id}


@app.post("/api/jobs/{job_id}/reclip")
async def reclip_job(job_id: str, request: Request):
    """Clip Anything: re-pick clips for an existing job from a natural-language
    prompt, reusing its transcript. Runs pick->render on a background thread; the
    UI re-polls GET /api/jobs/{id} exactly as for a new job."""
    job = _load_job(job_id)  # 404 if missing
    status = job.get("status")
    if status in ("queued", "downloading", "transcribing", "waiting-transcribe",
                  "picking", "rendering"):
        raise HTTPException(status_code=409,
                            detail={"error": f"job is '{status}'; wait until it is ready"})
    if not os.path.isfile(os.path.join(config.job_dir(job_id), "transcript.json")):
        raise HTTPException(status_code=400,
                            detail={"error": "this job has no transcript yet"})
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}
    prompt = (body.get("prompt") or body.get("guidance") or "").strip()
    try:
        n = int(body.get("n") or len(_load_clips(job_id)) or 6)
    except (TypeError, ValueError):
        n = len(_load_clips(job_id)) or 6
    n = min(20, max(1, n))
    threading.Thread(target=_run_reclip, args=(job_id, n, prompt),
                     daemon=True).start()
    return {"job_id": job_id, "status": "picking"}


@app.get("/api/jobs")
async def list_jobs():
    jobs = []
    try:
        entries = sorted(os.listdir(config.DATA_DIR))
    except FileNotFoundError:
        entries = []
    for name in entries:
        path = os.path.join(config.DATA_DIR, name, "job.json")
        if not os.path.isfile(path):
            continue
        try:
            data = _read_json(path)
        except Exception:
            continue
        jobs.append({
            "id": data.get("id", name),
            "title": data.get("title", ""),
            "status": data.get("status", "unknown"),
        })
    return jobs


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = _load_job(job_id)
    clips = _load_clips(job_id)
    return {**job, "clips": clips}


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    """Delete a job and its entire working directory.

    Refuses while the pipeline is mid-run (so we never rmtree files a worker
    thread is still writing). The containing path is re-resolved under DATA_DIR
    to block any traversal via a crafted job_id.
    """
    job = _load_job(job_id)  # 404 if missing
    status = job.get("status")
    if status in ("downloading", "transcribing", "picking", "rendering"):
        raise HTTPException(
            status_code=409,
            detail={"error": f"job is '{status}'; wait until it is ready or errors"},
        )
    data_root = os.path.realpath(config.DATA_DIR)
    target = os.path.realpath(config.job_dir(job_id))
    if target != data_root and not target.startswith(data_root + os.sep):
        raise HTTPException(status_code=403, detail={"error": "invalid job path"})
    if target == data_root or not os.path.isdir(target):
        raise HTTPException(status_code=404, detail={"error": f"job '{job_id}' not found"})
    try:
        shutil.rmtree(target)
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": f"delete failed: {exc}"})
    return {"deleted": job_id}


@app.api_route("/api/jobs/{job_id}/media/{path:path}", methods=["GET", "HEAD"])
async def get_media(job_id: str, path: str):
    target = _safe_media_path(job_id, path)
    media_type, _ = mimetypes.guess_type(target)
    return FileResponse(target, media_type=media_type or "application/octet-stream",
                        filename=os.path.basename(target))


@app.post("/api/jobs/{job_id}/clips/{slug}/edit")
async def edit_clip(job_id: str, slug: str, request: Request):
    _load_job(job_id)  # 404 if job missing
    try:
        body = await request.json()
    except Exception:
        body = {}
    instruction = (body or {}).get("instruction")
    if not instruction:
        raise HTTPException(status_code=400, detail={"error": "missing 'instruction'"})
    nl_edit = _import_or_503("editor.nl_edit")
    try:
        spec = nl_edit.apply_instruction(config.job_dir(job_id), slug, instruction)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)})
    return spec


_RENDER_KEYS = ("mode", "captions", "audio", "lut", "music", "speed", "zoom",
                "caption_style", "caption_lang", "broll",
                "aspect", "title_card", "title_secs",
                "trim_silence", "silence_db", "silence_min",
                "punch_in", "punch_secs",
                "caption_emphasis", "caption_emoji")

# One-click "make it viral" render presets — curated compositions of the render
# flags above into known-good short-form looks. Pure composition: no new render
# code, each key is an existing render-block field (see CONTRACT.md).
AUTOPILOT_PRESETS = {
    "viral": {  # the default social-ready look
        "mode": "facecrop", "aspect": "9:16", "audio": "enhance",
        "captions": True, "caption_style": "word-pop",
        "caption_emphasis": True, "caption_emoji": True,
        "punch_in": True, "trim_silence": True,
    },
    "hormozi": {  # one-big-word, high energy
        "mode": "facecrop", "aspect": "9:16", "audio": "enhance",
        "captions": True, "caption_style": "hormozi",
        "caption_emphasis": True, "caption_emoji": False,
        "punch_in": True, "trim_silence": True,
    },
    "clean": {  # minimal, no gimmicks
        "mode": "facecrop", "aspect": "9:16", "audio": "enhance",
        "captions": True, "caption_style": "big",
        "caption_emphasis": False, "caption_emoji": False,
        "punch_in": False, "trim_silence": True,
    },
}


@app.post("/api/jobs/{job_id}/clips/{slug}/render")
async def render_clip_endpoint(job_id: str, slug: str, request: Request):
    _load_job(job_id)
    render = _import_or_503("pipeline.render")
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}
    # Accept render options either flat or nested under "render".
    opts = dict(body.get("render") or {})
    for k in _RENDER_KEYS:
        if k in body:
            opts[k] = body[k]

    clips = _load_clips(job_id)
    index = None
    clip = None
    for i, c in enumerate(clips):
        if c.get("slug") == slug:
            clip = c
            index = i + 1  # 1-based
            break
    if clip is None:
        raise HTTPException(status_code=404, detail={"error": f"clip '{slug}' not found"})
    if opts:
        clip.setdefault("render", {})
        clip["render"].update(opts)
    try:
        render.render_clip(config.job_dir(job_id), clip, index)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)})
    # Persist the (mutated) clip back into clips.json.
    clips[index - 1] = clip
    try:
        _write_json(_clips_json_path(job_id), clips)
    except Exception:
        pass
    return clip


@app.post("/api/jobs/{job_id}/clips/{slug}/variants")
async def render_variants_endpoint(job_id: str, slug: str, request: Request):
    _load_job(job_id)
    render = _import_or_503("pipeline.render")
    try:
        body = await request.json()
    except Exception:
        body = {}
    aspects = (body or {}).get("aspects") or []

    clips = _load_clips(job_id)
    index = None
    clip = None
    for i, c in enumerate(clips):
        if c.get("slug") == slug:
            clip = c
            index = i + 1  # 1-based
            break
    if clip is None:
        raise HTTPException(status_code=404, detail={"error": f"clip '{slug}' not found"})
    try:
        render.render_variants(config.job_dir(job_id), clip, index, aspects)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)})
    # Persist the (mutated) clip back into clips.json.
    clips[index - 1] = clip
    try:
        _write_json(_clips_json_path(job_id), clips)
    except Exception:
        pass
    return {"variants": clip.get("variants"), "clip": clip}


@app.post("/api/jobs/{job_id}/clips/{slug}/metadata")
async def generate_metadata_endpoint(job_id: str, slug: str, request: Request):
    _load_job(job_id)
    try:
        body = await request.json()
    except Exception:
        body = {}
    goal = (body or {}).get("goal", "funnel")
    clips = _load_clips(job_id)
    if not any(c.get("slug") == slug for c in clips):
        raise HTTPException(status_code=404, detail={"error": f"clip '{slug}' not found"})
    meta_mod = _import_or_503("pipeline.metadata")
    try:
        clip = meta_mod.generate_metadata(config.job_dir(job_id), slug, goal)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)})
    return {"youtube": clip.get("youtube"), "clip": clip}


@app.get("/api/assets")
async def list_assets():
    """Available free render assets: color LUTs and user-dropped music beds."""
    _r = __import__("pipeline.render", fromlist=["CAPTION_STYLES", "ASPECTS"])
    return {"luts": config.list_luts(), "music": config.list_music(),
            "broll": config.list_broll(),
            "caption_styles": list(getattr(_r, "CAPTION_STYLES", ())),
            "aspects": list(getattr(_r, "ASPECTS", {}).keys())}


def _find_clip(job_id, slug):
    clips = _load_clips(job_id)
    for i, c in enumerate(clips):
        if c.get("slug") == slug:
            return clips, c, i + 1
    raise HTTPException(status_code=404, detail={"error": f"clip '{slug}' not found"})


@app.get("/api/jobs/{job_id}/clips/{slug}/srt")
async def export_srt(job_id: str, slug: str, fmt: str = "srt"):
    _load_job(job_id)
    _clips, clip, _idx = _find_clip(job_id, slug)
    subs = _import_or_503("pipeline.subtitles")
    try:
        if fmt == "ass":
            path = subs.write_ass(config.job_dir(job_id), clip, _idx)
            media = "text/plain"
        else:
            path = subs.write_srt(config.job_dir(job_id), clip)
            media = "application/x-subrip"
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)})
    return FileResponse(path, media_type=media, filename=os.path.basename(path))


@app.post("/api/jobs/{job_id}/clips/{slug}/thumbnail")
async def make_thumbnail_endpoint(job_id: str, slug: str, request: Request):
    _load_job(job_id)
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = ((body or {}).get("text") or "").strip()
    render = _import_or_503("pipeline.render")
    clips, clip, index = _find_clip(job_id, slug)
    try:
        rel = render.make_thumbnail(config.job_dir(job_id), clip, index, text)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)})
    clips[index - 1] = clip
    try:
        _write_json(_clips_json_path(job_id), clips)
    except Exception:
        pass
    return {"thumbnail": rel, "clip": clip}


@app.post("/api/jobs/{job_id}/clips/{slug}/assist")
async def assist_endpoint(job_id: str, slug: str, request: Request):
    _load_job(job_id)
    try:
        body = await request.json()
    except Exception:
        body = {}
    kind = ((body or {}).get("kind") or "").strip()
    _clips, clip, _idx = _find_clip(job_id, slug)
    assist = _import_or_503("pipeline.assist")
    fn = {"broll": assist.broll_shots, "music": assist.music_keywords,
          "thumbtext": assist.thumbnail_text,
          "improve": assist.improve_tips}.get(kind)
    if fn is None:
        raise HTTPException(status_code=400,
                            detail={"error": "kind must be one of: broll, music, thumbtext, improve"})
    try:
        return fn(config.job_dir(job_id), clip)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)})


@app.get("/api/autopilot")
async def list_autopilot():
    """Available one-click viral presets (name -> render block it applies)."""
    return {"presets": AUTOPILOT_PRESETS}


@app.post("/api/jobs/{job_id}/clips/{slug}/autopilot")
async def autopilot_clip(job_id: str, slug: str, request: Request):
    """One-click finish: merge a curated viral render preset onto the clip and
    re-render it. Returns the updated clip spec (same shape as /render)."""
    _load_job(job_id)
    render = _import_or_503("pipeline.render")
    try:
        body = await request.json()
    except Exception:
        body = {}
    preset = ((body or {}).get("preset") or "viral").strip().lower()
    opts = dict(AUTOPILOT_PRESETS.get(preset) or AUTOPILOT_PRESETS["viral"])
    clips, clip, index = _find_clip(job_id, slug)
    clip.setdefault("render", {})
    clip["render"].update(opts)
    try:
        render.render_clip(config.job_dir(job_id), clip, index)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)})
    clips[index - 1] = clip
    try:
        _write_json(_clips_json_path(job_id), clips)
    except Exception:
        pass
    return clip


@app.post("/api/jobs/{job_id}/resolve")
async def push_resolve(job_id: str, request: Request):
    _load_job(job_id)
    try:
        body = await request.json()
    except Exception:
        body = {}
    slugs = (body or {}).get("slugs")
    push_client = _import_or_503("resolve.push_client")
    try:
        resp = push_client.push_to_resolve(config.job_dir(job_id), slugs)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)})
    return resp


@app.post("/api/jobs/{job_id}/clips/{slug}/youtube")
async def upload_youtube(job_id: str, slug: str, request: Request):
    _load_job(job_id)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}
    clips = _load_clips(job_id)
    clip = next((c for c in clips if c.get("slug") == slug), None)
    if clip is None:
        raise HTTPException(status_code=404, detail={"error": f"clip '{slug}' not found"})
    rel = clip.get("file")
    if not rel:
        raise HTTPException(status_code=400, detail={"error": "clip has no rendered file"})
    file_path = os.path.join(config.job_dir(job_id), rel)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail={"error": "clip file missing on disk"})

    uploader = _import_or_503("youtube.upload")
    yt = clip.get("youtube") or {}
    title = body.get("title") or yt.get("title") or clip.get("title") or slug
    description = body.get("description")
    if description is None:
        description = yt.get("description", "")
    tags = body.get("tags")
    if tags is None:
        tags = yt.get("tags")
    privacy = body.get("privacy", "private")
    try:
        video_id = uploader.upload(file_path, title, description, tags, privacy)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)})
    return {"video_id": video_id}


# --------------------------------------------------------------------------- #
# Static site: serve index.html at / and mount static/ for assets
# --------------------------------------------------------------------------- #
@app.get("/")
async def index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path, media_type="text/html")
    return JSONResponse(
        status_code=404,
        content={"error": "static/index.html not found yet (built by another agent)"},
    )


# Mounted last so it does not shadow the API routes above.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
