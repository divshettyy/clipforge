# ClipForge

A local web app that turns a YouTube URL into short, auto-cut vertical (9:16)
"viral" clips, lets you refine each clip with a natural-language edit, push the
results into DaVinci Resolve, and upload them to YouTube.

Pipeline per job: **download → extract audio → transcribe → pick highlights →
render clips**, driven from a web UI with live status polling.

### Editing / render features
- **Framing:** blur-pad, centre-crop, and **active-speaker face-track reframe** (ONNX).
- **Virality ranking:** highlight picker scores each clip 0–100 with hook / payoff / retention-risk read-out, best-first.
- **Captions:** 4 burned templates (word-pop, karaoke sweep, Hormozi box, big-word), **power-word emphasis**, **auto-emoji**, and translation to any language.
- **Silence jump-cuts:** dead-air removal with caption + reframe time-remap.
- **Hook punch-in** zoom, **hook title card**, **b-roll** cutaways, ducked **music** bed, **audio enhance**.
- **Aspects:** 9:16 / 4:5 / 1:1 / 16:9, with multi-aspect batch export.
- **Colour:** free generated **LUT grade** pack (assets/luts/) + any dropped-in `.cube`.
- **Output:** parallel render with live progress, SRT/ASS export, thumbnail generator.

## Layout

- `server.py` — FastAPI app (`app`), the HTTP API + static site host.
- `pipeline/` — `download.py`, `transcribe.py`, `highlight.py`, `render.py`,
  `reframe.py` (speaker-follow face-track), `silence.py` (jump-cuts),
  `lutgen.py` (LUT pack), `subtitles.py` (SRT/ASS), `metadata.py` (Shorts SEO),
  `assist.py` (b-roll / music / thumbnail-text / "why not viral?" copilot).
- `editor/nl_edit.py` — natural-language clip editing via the shared LLM backend.
- `llm.py` — shared Claude backend (CLI subscription or API SDK; see below).
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

## Configure the LLM backend

Highlight picking, NL editing, SEO metadata, and the creative assists all go
through one shared backend (`llm.py`), selected by `CLIPFORGE_LLM`:

- **`cli`** (default) — shells out to the local Claude Code CLI (`claude -p`),
  billed against your Claude subscription (Pro/Max). **No `ANTHROPIC_API_KEY`
  and no API credits needed.** Requires the `claude` CLI on `PATH`.
- **`api`** — the `anthropic` Python SDK with a pay-per-token key:

  ```bash
  export CLIPFORGE_LLM=api
  export ANTHROPIC_API_KEY="sk-ant-..."
  ```

`config.py` auto-loads a `ROOT/.env` file (`KEY=VALUE` per line) on import, so
you can keep these in `clipforge/.env` instead of exporting them. Copy
`.env.example` to `.env` to start. Values already in the environment win over
`.env`.

Model used: `claude-opus-4-8` (override with `CLIPFORGE_MODEL`).

## Start the server

Run from the project root:

```bash
cd /home/hakuza/clipforge
/home/hakuza/venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8800
```

Then open http://127.0.0.1:8800/ .

### HTTP API

- `POST   /api/jobs` `{"url","n"?,"guidance"?}` → `{"job_id"}` (runs the pipeline on a background thread)
- `GET    /api/jobs` → list of `{id,title,status}`
- `GET    /api/jobs/{id}` → `job.json` fields + `clips`
- `DELETE /api/jobs/{id}` → delete the job dir (409 while mid-run)
- `POST   /api/jobs/{id}/reclip` `{"prompt"?,"n"?}` → "Clip Anything": re-pick + re-render from the existing transcript (409 mid-run, 400 if no transcript)
- `GET    /api/jobs/{id}/media/{path}` → serves files under the job dir (path-traversal safe; GET/HEAD)
- `POST   /api/jobs/{id}/clips/{slug}/edit` `{"instruction"}` → updated clip spec
- `POST   /api/jobs/{id}/clips/{slug}/render` → re-render that clip
- `POST   /api/jobs/{id}/clips/{slug}/variants` `{"aspects"?}` → multi-aspect batch export → `{"variants",...}`
- `POST   /api/jobs/{id}/clips/{slug}/metadata` `{"goal"?}` → Shorts SEO `{"youtube":{...}}` (goal: funnel|growth|monetisation)
- `GET    /api/jobs/{id}/clips/{slug}/srt?fmt=srt|ass` → download captions as SRT/ASS
- `POST   /api/jobs/{id}/clips/{slug}/thumbnail` `{"text"?}` → generate a thumbnail → `{"thumbnail"}`
- `POST   /api/jobs/{id}/clips/{slug}/assist` `{"kind"}` → creative assist (`kind`: broll|music|thumbtext|improve)
- `GET    /api/autopilot` → `{"presets":{name:render_block}}` (viral|hormozi|clean)
- `POST   /api/jobs/{id}/clips/{slug}/autopilot` `{"preset"?}` → one-click "Make viral" merge + re-render
- `GET    /api/assets` → available render assets: `{luts, music, broll, caption_styles, aspects}`
- `POST   /api/jobs/{id}/resolve` `{"slugs"?}` → push clips to DaVinci Resolve
- `POST   /api/jobs/{id}/clips/{slug}/youtube` `{"title"?,"description"?,"tags"?,"privacy"?}` → `{"video_id"}`

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
