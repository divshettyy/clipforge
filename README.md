# ClipForge

**YouTube URL in, viral vertical clips out.** A local, self-hosted pipeline
that downloads a YouTube video, transcribes it, picks the best moments, and
renders burned-caption 9:16 clips — then lets you refine each one with plain
English, push it into DaVinci Resolve, or upload it straight to YouTube.

> Built as a local alternative to opus.pro / submagic / klap.app style tools —
> same core loop (download → transcribe → auto-clip → caption → export), but
> running on your own machine against your own Claude subscription.

![ClipForge screenshot placeholder](docs/screenshot.png)
*(screenshot/demo GIF placeholder — see `data/jobs/demo-igl-ep8/` for a sample
job you can load without downloading anything)*

## What it does

Point it at a YouTube URL and it runs one pipeline per job:

**download → extract audio → transcribe → pick highlights → render clips**

all driven from a single-page web UI with live status polling. Each rendered
clip can then be edited with a natural-language instruction ("make the hook
shorter", "switch to the Hormozi caption style"), re-rendered, pushed to
DaVinci Resolve, or uploaded to YouTube — without leaving the UI.

## Features

- **Framing** — blur-pad, centre-crop, and active-speaker **face-track
  reframe** (ONNX, Ultraface RFB-320) that follows whoever is talking.
- **Virality ranking** — the highlight picker scores every candidate clip
  0–100 with a hook / payoff / retention-risk breakdown, best first.
- **Captions** — 4 burned styles (word-pop, karaoke sweep, Hormozi box,
  big-word), power-word emphasis, auto-emoji, and translation to any language.
- **Silence jump-cuts** — dead-air removal with caption and reframe timing
  remapped to match.
- **Hook punch-in** zoom, hook title cards, B-roll cutaways, ducked music bed,
  audio enhance.
- **Multi-aspect export** — 9:16 / 4:5 / 1:1 / 16:9 in one batch.
- **Colour grading** — a built-in LUT pack (`assets/luts/`) plus any `.cube`
  you drop in.
- **Natural-language clip editing** — re-spec a rendered clip in plain
  English via the shared Claude backend.
- **One-click "Make viral"** autopilot presets (viral / Hormozi / clean).
- **SEO metadata generator** — YouTube Shorts title/description/tags tuned to
  a goal (funnel / growth / monetisation).
- **DaVinci Resolve push** — send clips straight into a Resolve timeline
  (WSL2 → Windows bridge).
- **YouTube upload** — resumable upload via the YouTube Data API v3.
- Parallel rendering with live progress, SRT/ASS caption export, thumbnail
  generator, and a demo job that works fully offline.

## Tech stack

| Layer | Tech |
|---|---|
| Backend | Python, FastAPI, Uvicorn |
| Video/audio | ffmpeg, yt-dlp |
| Transcription | faster-whisper |
| Face tracking | ONNX Runtime (Ultraface RFB-320) |
| LLM (highlights, NL edit, SEO, assists) | Claude (via Claude Code CLI subscription, or the `anthropic` SDK) |
| Frontend | Single-file vanilla JS/HTML/CSS (`static/index.html`) |
| Integrations | DaVinci Resolve (scripting bridge), YouTube Data API v3 |

## Prerequisites

- Python 3.10+
- `ffmpeg` / `ffprobe` on `PATH`
- Either:
  - the `claude` CLI on `PATH` and an active Claude subscription (default,
    no API key needed), **or**
  - an Anthropic API key, if you set `CLIPFORGE_LLM=api`
- Optional: DaVinci Resolve (Windows) for the Resolve push feature; a Google
  Cloud OAuth client for YouTube upload (see `youtube/SETUP.md`)

## Install

```bash
git clone https://github.com/divshettyy/clipforge.git
cd clipforge
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
```

(If you already have a pre-built virtualenv elsewhere, just point the run
command below at its `python` instead.)

## Configure

Copy the example env file and fill in what you need — most people only need
to decide the LLM backend:

```bash
cp .env.example .env
```

| Variable | Purpose | Default |
|---|---|---|
| `CLIPFORGE_LLM` | LLM backend: `cli` (local Claude Code CLI, no API key) or `api` (Anthropic SDK, pay-per-token) | `cli` |
| `ANTHROPIC_API_KEY` | Anthropic API key — only read when `CLIPFORGE_LLM=api` | *(empty)* |
| `CLIPFORGE_MODEL` | Claude model id used by both backends | `claude-opus-4-8` |
| `CLIPFORGE_WIN_HOST` | Windows host IP, as seen from WSL2, for the Resolve bridge | `172.20.16.1` |
| `CLIPFORGE_RESOLVE_PORT` | Port the Resolve bridge listens on (Windows side) | `8765` |
| `CLIPFORGE_WIN_SHARE` | WSL path to the Windows-visible drop folder used to hand clips to Resolve | `/mnt/c/Users/Public/ClipForge` |

`config.py` auto-loads `.env` from the project root on import. Values already
set in your shell environment take precedence over the file. **Never commit
`.env`** — it's git-ignored, along with `youtube/client_secret*.json` and any
`*token*.json`.

## Run

```bash
cd clipforge
venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8800
```

Open **http://127.0.0.1:8800/**. A pre-rendered demo job
(`data/jobs/demo-igl-ep8/`) is already there — load it to try the UI, media
endpoints, and clip cards with zero downloads or API calls.

## Usage walkthrough

1. Paste a YouTube URL into the UI and submit — a job is created and the
   pipeline runs in the background (`queued → downloading → transcribing →
   picking → rendering → ready`).
2. Watch clip candidates appear as they render, each with a virality score
   and hook/payoff/retention read-out.
3. Pick a clip and either:
   - type a natural-language edit ("trim the first 2 seconds", "bigger
     captions") and re-render, or
   - run a one-click autopilot preset, or
   - export it to another aspect ratio, grab SRT/ASS captions, or generate a
     thumbnail.
4. Generate SEO metadata for the clip, then push it to DaVinci Resolve or
   upload it directly to YouTube from the same card.

### HTTP API

- `POST /api/jobs` `{"url","n"?,"guidance"?}` → `{"job_id"}`
- `GET /api/jobs` → `[{id,title,status}]`
- `GET /api/jobs/{id}` → job fields + clips
- `DELETE /api/jobs/{id}` → delete the job dir (409 while mid-run)
- `POST /api/jobs/{id}/reclip` `{"prompt"?,"n"?}` — re-pick + re-render from the existing transcript
- `GET /api/jobs/{id}/media/{path}` — serves files under the job dir
- `POST /api/jobs/{id}/clips/{slug}/edit` `{"instruction"}`
- `POST /api/jobs/{id}/clips/{slug}/render`
- `POST /api/jobs/{id}/clips/{slug}/variants` `{"aspects"?}`
- `POST /api/jobs/{id}/clips/{slug}/metadata` `{"goal"?}`
- `GET /api/jobs/{id}/clips/{slug}/srt?fmt=srt|ass`
- `POST /api/jobs/{id}/clips/{slug}/thumbnail` `{"text"?}`
- `POST /api/jobs/{id}/clips/{slug}/assist` `{"kind"}` — broll | music | thumbtext | improve
- `GET /api/autopilot` → preset list (viral | hormozi | clean)
- `POST /api/jobs/{id}/clips/{slug}/autopilot` `{"preset"?}`
- `GET /api/assets` → available LUTs, music, b-roll, caption styles, aspects
- `POST /api/jobs/{id}/resolve` `{"slugs"?}` — push clips to DaVinci Resolve
- `POST /api/jobs/{id}/clips/{slug}/youtube` `{"title"?,"description"?,"tags"?,"privacy"?}`

The server imports the pipeline/editor/youtube/resolve modules lazily — if one
is missing or broken, the server still starts and serves existing jobs, and
only the affected endpoint returns `503`.

## Layout

- `server.py` — FastAPI app, HTTP API, and static site host.
- `pipeline/` — `download.py`, `transcribe.py`, `highlight.py`, `render.py`,
  `reframe.py` (face-track), `silence.py` (jump-cuts), `lutgen.py` (LUT pack),
  `subtitles.py` (SRT/ASS), `metadata.py` (SEO), `assist.py` (creative copilot).
- `editor/nl_edit.py` — natural-language clip editing via the shared LLM backend.
- `llm.py` — shared Claude backend (CLI subscription or API SDK).
- `youtube/upload.py` — YouTube Data API v3 resumable upload (`youtube/SETUP.md`).
- `resolve/` — DaVinci Resolve bridge + push client (`resolve/README.md`).
- `config.py` — shared constants and paths; import from here, don't hardcode.
- `data/jobs/<job_id>/` — per-job working dir (gitignored).
- `static/index.html` — single-file vanilla-JS UI.
- `CONTRACT.md` — ground-truth module/schema contract for anyone extending the pipeline.

## Roadmap

- [ ] Automated test coverage beyond the end-to-end smoke script
- [ ] Dockerfile / one-command setup (currently assumes a local Python + ffmpeg env)
- [ ] Batch job queue / multi-video playlists
- [ ] Pluggable transcription/LLM backends beyond Whisper + Claude
- [ ] CI pipeline (lint + smoke test on push)

## Known limitations

- Single-machine, single-user design — no auth, no multi-tenant job isolation.
- DaVinci Resolve push assumes a specific WSL2 → Windows host setup; see
  `resolve/README.md` for the bridge requirements.
- `run_smoke.py` is an informal end-to-end smoke test (downloads a real video),
  not a unit test suite.

## License

MIT — see [LICENSE](LICENSE).
