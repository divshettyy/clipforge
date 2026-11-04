# ClipForge

A local web app that turns a YouTube URL into short, auto-cut vertical (9:16)
"viral" clips, lets you refine each clip with a natural-language edit, push the
results into DaVinci Resolve, and upload them to YouTube.

Pipeline per job: **download → extract audio → transcribe → pick highlights →
render clips**, driven from a web UI with live status polling.

## Layout

- `server.py` — FastAPI app (`app`), the HTTP API + static site host.
- `pipeline/` — `download.py`, `transcribe.py`, `highlight.py`, `render.py`.
- `editor/nl_edit.py` — natural-language clip editing via the Claude API.
- `youtube/upload.py` — YouTube Data API v3 resumable upload (see `youtube/SETUP.md`).
- `resolve/` — DaVinci Resolve bridge (Windows) + push client (WSL); see `resolve/README.md`.
- `config.py` — shared constants and paths (import from here; do not hardcode).
- `data/jobs/<job_id>/` — per-job working dir (`source.mp4`, `audio.wav`,
  `transcript.json`, `clips.json`, `clips/NN_slug.mp4`, `job.json`).
- `static/index.html` — single-file vanilla-JS dark UI.

## Virtualenv

A ready virtualenv with every dependency pre-installed lives at
`/home/hakuza/venv`. Use its interpreter directly — no new `pip install` is
needed:

```bash
/home/hakuza/venv/bin/python --version
```

If you ever rebuild it: `/home/hakuza/venv/bin/python -m pip install -r requirements.txt`.

## Configure the Anthropic API key

Highlight picking and NL editing call the Claude API. Export your key before
starting the server (it is read from the environment via `config.ANTHROPIC_API_KEY`):

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Model used: `claude-opus-4-8` (override with `CLIPFORGE_MODEL`).

## Start the server

Run from the project root:

```bash
cd /home/hakuza/clipforge
/home/hakuza/venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8800
```

Then open http://127.0.0.1:8800/ .

### HTTP API

- `POST /api/jobs` `{"url","n"?,"guidance"?}` → `{"job_id"}` (runs the pipeline on a background thread)
- `GET  /api/jobs` → list of `{id,title,status}`
- `GET  /api/jobs/{id}` → `job.json` fields + `clips`
- `GET  /api/jobs/{id}/media/{path}` → serves files under the job dir (path-traversal safe)
- `POST /api/jobs/{id}/clips/{slug}/edit` `{"instruction"}` → updated clip spec
- `POST /api/jobs/{id}/clips/{slug}/render` → re-render that clip
- `POST /api/jobs/{id}/resolve` `{"slugs"?}` → push clips to DaVinci Resolve
- `POST /api/jobs/{id}/clips/{slug}/youtube` `{"title"?,"description"?,"tags"?,"privacy"?}` → `{"video_id"}`

`job.json` `status` progresses: `queued → downloading → transcribing → picking →
rendering → ready`, or `error` with a message. The server imports the
pipeline/editor/youtube/resolve modules lazily; if one is missing or broken the
server still starts and serves existing jobs, and the relevant endpoint returns
HTTP 503 naming the unavailable module.

## Demo job

A pre-rendered demo job ships at `data/jobs/demo-igl-ep8/` (source video,
transcript, `clips.json`, and 6 rendered clips). It appears in the UI
immediately with no download or API key required — use it to exercise the
server, media endpoint, and clip cards offline.

## Resolve and YouTube setup

- DaVinci Resolve integration (Windows bridge + WSL push client): see `resolve/README.md`.
- YouTube upload OAuth / GCP project setup: see `youtube/SETUP.md`.
