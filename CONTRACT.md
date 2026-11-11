# ClipForge — shared build contract (ground truth)

A local web app: YouTube URL → auto viral vertical clips → NL-edit → push to DaVinci Resolve → upload to YouTube.
Every module below is built by a separate agent. Obey these paths, schemas, and function signatures EXACTLY so the pieces fit. Do not invent alternate names.

## Environment
- Project root: `/home/hakuza/clipforge`
- Python interpreter (use this, it has all deps): `/home/hakuza/venv/bin/python`
- ffmpeg/ffprobe: on PATH (`/usr/bin/ffmpeg`).
- Host: WSL2. DaVinci Resolve runs on the **Windows** host. WSL→Windows host IP: `172.20.16.1`.
- Config module: `clipforge/config.py` (already written — import from it, do not duplicate constants). Key values:
  - `ROOT`, `DATA_DIR` (=`<ROOT>/data/jobs`), `VENV_PY`
  - `WIN_HOST_IP="172.20.16.1"`, `RESOLVE_BRIDGE_PORT=8765`
  - `WIN_SHARE_DIR` — WSL path `/mnt/c/Users/Public/ClipForge` (Windows-visible drop folder for Resolve)
  - `ANTHROPIC_API_KEY` (from env), `CLAUDE_MODEL="claude-opus-4-8"`
  - `fonts_dir()` → returns a dir holding `NotoSansDevanagari-Bold.ttf` (download if missing).

## Per-job layout
`<DATA_DIR>/<job_id>/`
- `source.mp4`   downloaded video
- `audio.wav`    16k mono (for transcription)
- `transcript.json`
- `clips.json`   list of clip specs (schema below)
- `clips/NN_slug.mp4`  rendered vertical clips (NN = 2-digit 1-based index)
- `job.json`     `{ "id","url","title","duration","status","error","render_progress":{"done","total"}? }`; status in: queued, downloading, transcribing, picking, rendering, ready, error

A ready demo job already exists at `<DATA_DIR>/demo-igl-ep8/` with source.mp4, transcript.json, clips.json, and 6 rendered clips — use it to test without re-downloading.

## transcript.json schema
```json
{ "language":"en", "duration":3007.0,
  "segments":[ {"start":1.2,"end":3.4,"text":"...","words":[{"w":"word","s":1.2,"e":1.5}]} ] }
```

## clips.json schema  (list of clip specs)
```json
[{
  "slug":"mc-stan-range",          // kebab-case, unique within job
  "title":"MC Stan — what a range", // human title (also default YouTube title)
  "reason":"why this is viral",     // 1 line from the highlight picker
  "start":110.0, "end":143.5,       // seconds in source
  "render":{
    "mode":"blurpad",               // blurpad | centercrop | facecrop (speaker-follow)
    "aspect":"9:16",                // 9:16 | 4:5 | 1:1 | 16:9
    "captions":false,               // burn captions
    "caption_style":"word-pop",     // word-pop | karaoke | hormozi | big
    "caption_lang":"",              // "" = original; else translate target, e.g. "es"
    "caption_emphasis":false,       // highlight power words (colour + scale)
    "caption_emoji":false,          // inject keyword emoji (needs NotoEmoji font)
    "speed":1.0,                    // 0.5..2.0 playback speed
    "zoom":1.0,                     // 1.0..1.5 scale of foreground
    "lut":"",                       // .cube basename in assets/luts/
    "audio":"raw",                  // raw | enhance (HPF+denoise+comp+loudnorm)
    "music":"",                     // filename in assets/music/ (ducked bed)
    "title_card":false,             // true=use hook/title, or a custom string
    "title_secs":1.8,               // title card duration (0.5..6.0)
    "trim_silence":false,           // jump-cut dead air (remaps captions+reframe)
    "silence_db":-32.0,             // silencedetect threshold (dBFS)
    "silence_min":0.35,             // min silence length to cut (seconds)
    "punch_in":false,               // hook centre-zoom push; true or 0.02..0.25 intensity
    "punch_secs":1.8,               // punch-in ramp duration (defaults to title_secs)
    "broll":[]                      // [{file,start,end}] cutaways (file in assets/broll/)
  },
  "score":87,                       // 0-100 predicted virality (highlight picker)
  "hook":"...", "payoff":"...", "retention_risk":"...",  // picker analysis
  "status":"rendered",              // pending | rendered
  "file":"clips/01_mc-stan-range.mp4", // relative to job dir, or null
  "variants":{"1:1":"clips/01_slug__1x1.mp4"}, // OPTIONAL, multi-aspect batch export
  "_render_error":"...",            // OPTIONAL, set by render_all if this clip failed
  "youtube":{                        // OPTIONAL, added by pipeline/metadata.py
    "title":"... #Shorts",           // <=100 chars, keyword-front, ends #Shorts
    "description":"...",             // first 125 chars weighted + CTA + hashtags
    "tags":["mc stan", "..."],      // 10-15, joined <=500 chars
    "hashtags":["#mcstan", "..."]  // 3-5, specific->broad
  }
}]
```
Output clips are H.264 + AAC, +faststart, at the selected aspect (default
1080x1920 9:16; also 1080x1350 4:5, 1080x1080 1:1, 1920x1080 16:9).

## Module APIs (exact signatures)
All functions take `job_dir` as an absolute path string unless noted.

### pipeline/download.py
`download(url:str, job_dir:str) -> dict`  # writes source.mp4; returns {"title","duration","path"}. Use yt-dlp (module `yt_dlp` or `/home/hakuza/venv/bin/yt-dlp`), format `bestvideo[height<=720]+bestaudio/best[height<=720]`, merge mp4.

### pipeline/transcribe.py
`extract_audio(job_dir:str) -> str`  # ffmpeg -> audio.wav 16k mono
`transcribe(job_dir:str) -> str`     # writes transcript.json, returns its path.
  NOTE: faster-whisper + current PyAV is incompatible via file path. Decode audio.wav yourself with the `wave` module into a float32 numpy array and pass the ndarray to `model.transcribe(...)`. Model: `WhisperModel("small", device="cpu", compute_type="int8")`, `word_timestamps=True`, `vad_filter=True`.

### pipeline/highlight.py
`pick_highlights(job_dir:str, n:int=6, guidance:str="") -> list`  # reads transcript.json, calls Claude API (anthropic SDK, model=CLAUDE_MODEL) to choose n self-contained viral moments (hook+payoff, 10-60s). Writes clips.json (status="pending", file=null, render defaults mode="blurpad",captions=false,speed=1.0,zoom=1.0). Returns the list. `guidance` is optional user steer ("focus on roasts", "shorter clips").

### pipeline/render.py
`render_clip(job_dir:str, clip:dict, index:int) -> str`  # renders one spec per its render block, writes clips/NN_slug.mp4, sets clip["status"]="rendered", clip["file"]=..., returns output path.
`render_all(job_dir:str) -> None`  # renders every clip in clips.json, persists clips.json.
  - blurpad: split bg(scale increase+crop 1080x1920+boxblur=40:10) + fg(scale decrease, then *zoom); overlay centered.
  - centercrop: scale to fill then crop center 1080x1920.
  - facecrop: same as centercrop for v1 (leave a TODO; no face model required).
  - captions=true: build an ASS from transcript words within [start,end], rebased, 4-word lines, active word yellow; burn via `subtitles=...:fontsdir=<fonts_dir()>`; font "Noto Sans Devanagari". speed via setpts/atempo.

### editor/nl_edit.py
`apply_instruction(job_dir:str, slug:str, instruction:str) -> dict`  # loads clip spec, calls Claude API with a tool/JSON schema that emits edit ops (adjust start/end by delta or absolute, set mode, captions on/off, speed, zoom, retitle), applies to the spec, persists clips.json, re-renders that clip via pipeline.render.render_clip, returns updated spec. Must be robust to free text like "make it 5s shorter and add captions, crop to speaker".

### pipeline/metadata.py
`generate_metadata(job_dir:str, slug:str=None, goal:str="funnel") -> dict|list`  # Shorts SEO metadata via the shared `llm` backend (subscription CLI, no API credits). For one clip (slug) or all, writes `clip["youtube"]={title,description,tags,hashtags}`, persists clips.json, returns the clip dict (slug given) or full list. goal in: funnel | growth | monetisation. Distilled from the claude-youtube skill (MIT). Clamps: title<=100 incl #Shorts, tags<=15 and joined<=500 chars, description<=5000.

### youtube/upload.py
`upload(file_path:str, title:str, description:str="", tags:list=None, privacy:str="private") -> str`  # YouTube Data API v3 resumable upload, returns video_id. OAuth: client secret at `<ROOT>/youtube/client_secret.json`, token cache at `<ROOT>/youtube/token.json` (InstalledAppFlow, run_console/run_local_server fallback). categoryId "24". Also write `youtube/SETUP.md` with exact GCP steps (create project, enable YouTube Data API v3, OAuth consent screen (external, add self as test user), create OAuth client "Desktop app", download client_secret.json to that path).

### resolve/  (DaVinci Resolve — Windows side + WSL side)
- `resolve/bridge_server.py` — runs on the **Windows** host under Resolve's Python. Plain `http.server` (no extra deps). `POST /import` with JSON `{"timeline":"ClipForge","paths":["C:\\Users\\Public\\ClipForge\\01_x.mp4", ...]}` → uses DaVinciResolveScript API to get/create project "ClipForge", import the media, create/append a 1080x1920 timeline, append the clips. `GET /health` → `{"resolve":true}`. Document the Windows env vars (RESOLVE_SCRIPT_API, RESOLVE_SCRIPT_LIB, PYTHONPATH) and how to launch in `resolve/README.md`.
- `resolve/push_client.py` — WSL side. `push_to_resolve(job_dir:str, slugs:list=None) -> dict`: copy the selected rendered clips into `WIN_SHARE_DIR`, translate each to its Windows path (`/mnt/c/...` -> `C:\...`), POST to `http://172.20.16.1:8765/import`. Returns bridge response. `resolve_health() -> bool`.

### server.py  (FastAPI, served by uvicorn)
Mount `static/` ; `GET /` returns `static/index.html`.
- `POST /api/jobs` body `{"url","n"?,"guidance"?}` → create job dir + job.json(status=queued), run download→extract_audio→transcribe→pick_highlights→render_all in a BackgroundTasks/thread, return `{"job_id"}`.
- `GET /api/jobs` → list jobs (id,title,status).
- `GET /api/jobs/{id}` → job.json + clips.json.
- `GET /api/jobs/{id}/media/{path:path}` → serve files under the job dir (clips + source) with correct mime.
- `POST /api/jobs/{id}/clips/{slug}/edit` body `{"instruction"}` → editor.apply_instruction, return updated spec.
- `POST /api/jobs/{id}/clips/{slug}/render` → re-render that clip.
- `POST /api/jobs/{id}/clips/{slug}/metadata` body `{"goal"?}` → pipeline.metadata.generate_metadata for that clip, return `{"youtube":{...},"clip":{...}}`.
- `POST /api/jobs/{id}/resolve` body `{"slugs"?}` → resolve.push_client.push_to_resolve.
- `POST /api/jobs/{id}/clips/{slug}/youtube` body `{"title"?,"description"?,"tags"?,"privacy"?}` → youtube.upload on that clip file, return {video_id}.
- `POST /api/jobs/{id}/reclip` body `{"prompt"?,"n"?}` → "Clip Anything": re-run `highlight.pick_highlights(job_dir, n, guidance=prompt)` then `render.render_all` on a background thread, reusing the existing transcript (no re-download/transcribe). Replaces clips.json. 409 if the job is mid-run, 400 if it has no transcript yet. Poll `GET /api/jobs/{id}` (status picking→rendering→ready) exactly as for a new job. `n` defaults to the current clip count.
- `GET /api/autopilot` → `{"presets":{name:render_block}}`; one-click viral render presets. Names: `viral` (default), `hormozi`, `clean`. Each value is a subset of the render block above (only existing `render.*` keys).
- `POST /api/jobs/{id}/clips/{slug}/autopilot` body `{"preset"?}` → merge the named preset's render block onto the clip and re-render via `render.render_clip`; persists and returns the clip (same shape as `/render`). Unknown preset falls back to `viral`.
- `POST /api/jobs/{id}/clips/{slug}/assist` `kind` now also accepts `"improve"` → `pipeline.assist.improve_tips(job_dir, clip)` returns `{"tips":[{"change","why","impact"}]}` (virality copilot: concrete, clip-specific edits to raise the score).
Run via `/home/hakuza/venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8800` from ROOT.

### static/index.html
Single file, vanilla JS (no build step), dark UI. Flow: input YouTube URL + optional count/guidance → POST /api/jobs → poll GET /api/jobs/{id} until status=ready, showing stage. Then a responsive grid of clip cards: `<video controls>` from the media endpoint, title, reason, start-end; per card: a text input for an NL edit instruction + "Apply edit" (calls edit endpoint, re-polls), a "Push to Resolve" button, and a "Upload to YouTube" button with title/description fields + privacy select. Also a global "Push all to Resolve" button. Show spinners/toasts for in-flight ops. On the demo job, cards should display immediately.

## Conventions
- Pure stdlib + the already-installed deps (fastapi, uvicorn, anthropic, yt-dlp, faster-whisper, numpy, google-api-python-client, google-auth-oauthlib). No new pip installs.
- Read config from `clipforge/config.py`. Never hardcode `/home/hakuza/...` inside modules except via config.
- Each module importable as `from clipforge.pipeline import render` when CWD is `/home/hakuza`, AND runnable with CWD=ROOT via `from pipeline import render`. Prefer package-relative imports that work with CWD=ROOT (`from config import ...`, `from pipeline.render import ...`). Server is launched from ROOT.
- Write defensive code: create dirs, try/except around external calls, clear errors into job.json.
