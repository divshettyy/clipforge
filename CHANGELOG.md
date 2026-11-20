# ClipForge — Changelog

A running log of feature work and fixes. Newest first. Keep appending to this
file as the tool evolves.

## Unreleased

- **Lead-in scoring**: clips now report `lead_in` (seconds of silence before the first word); openings with >1.5 s of dead air lose 4 points since they waste the hook window. 5 new tests.

- **Smarter clip scoring**: highlight picks now blend the model's virality score with measured delivery (words/sec and dead-air fraction from the transcript) — up to ±10 points, so slow/silent picks rank lower and brisk ones higher. Clips keep `llm_score`, `speech_rate`, `dead_air` for transparency. 7 new tests.
- **SRT export**: captions now break at sentence ends, long pauses (>0.7 s) and a 3.5 s max cue length instead of fixed 7-word chunks; cues never overlap. Added `tests/test_subtitles.py` (10 tests).

## 2026-11-20 — CI Python bump to 3.12

The CI smoke job pinned Python 3.11, which cannot install numpy 2.5.x (that release
requires Python 3.12 or newer). As a result the grouped Dependabot pull request that
bumped numpy failed to install its dependencies and the check went red, even though
the default branch stayed green on the older numpy it resolves to on 3.11. Bumped the
CI runner to Python 3.12 so the modern numpy installs and the dependency-update PRs
pass again; 3.12 also keeps full binary-wheel coverage for faster-whisper and the rest
of the stack, which a jump straight to the local runtime (3.14) would have risked.
Validated by the CI run triggered on push.

## 2026-11-20 — discoverability and dependency-hygiene pass (no code behaviour change)

Part of a repository-wide polish effort. On top of the earlier README badges, topic
tags and issue/PR templates, this pass adds a Dependabot configuration that opens
grouped weekly pull requests for both Python packages and GitHub Actions, an
`.editorconfig` so every contributor gets the same indentation and line-ending rules,
and upper version bounds on the previously unpinned dependencies (`numpy`,
`faster-whisper`, the Google API clients and `python-multipart`) to guard against a
surprise breaking major release. `yt-dlp` is intentionally left unpinned because it
needs to track the newest release to keep working against YouTube. Nothing in the
application pipeline changed.

## 2026-11-13 — portfolio/public-readiness pass (no code behaviour change)

Audit before making the GitHub repo public.

- **Secrets scan (working tree + full git history)** — searched for API keys,
  tokens, passwords, OAuth client secrets, `.env` files, and credential JSON
  across every commit (`git log -p`) and the working tree. Found **no real
  secret values** anywhere, in history or on disk: the only hits were
  placeholder docs (`sk-ant-...`, `export ANTHROPIC_API_KEY=`), code that
  *reads* the env var, and the always-gitignored `youtube/client_secret*.json`
  / `*token*.json` filenames mentioned in setup docs (never actually
  committed). The real local `.env` (holds a live `ANTHROPIC_API_KEY`) is
  untracked and already covered by `.gitignore`.
- **Added `LICENSE`** — MIT, 2026, divshettyy. Was missing.
- **README rewritten** for a public portfolio audience: tagline, feature list,
  tech stack table, prerequisites, generic install steps (`python3 -m venv`
  instead of assuming a pre-built local venv path), a config/env var
  reference table (names and defaults only, no values), a usage walkthrough,
  roadmap, known-limitations section, and an explicit license line. HTTP API
  list and layout section carried over from the prior pass (still accurate
  against `server.py`).
- No tracked file or any historical commit contains a real API key, OAuth
  secret, password, or private key. `.gitignore` already covers `.env`,
  `*.env`, YouTube OAuth artifacts, `data/` (per-job media), and standard
  Python/OS noise — confirmed sufficient, no changes needed.

Verified: no code changes, no schema changes — docs/license only.

## 2026-11-12 — docs/config gap sweep (no code behaviour change)

Audit pass to bring docs in line with the shipped code. No pipeline, schema, or
render changes — documentation and a config template only.

- **README — LLM backend section rewritten.** It claimed highlight picking / NL
  editing "call the Claude API" and told you to export `ANTHROPIC_API_KEY`. In
  fact all LLM-using modules (`highlight`, `nl_edit`, `assist`, `metadata`) go
  through `llm.py`, whose default backend is `cli` (local Claude Code CLI on the
  user's subscription, **no API key / credits**); `api` is the opt-in SDK path.
  Documented both backends, `CLIPFORGE_LLM`, and the `ROOT/.env` auto-load.
- **README — HTTP API list completed.** Added the 10 live endpoints that were
  missing: `DELETE /jobs/{id}`, `reclip`, `variants`, `metadata`, `srt`,
  `thumbnail`, `assist`, `GET /autopilot` + `POST .../autopilot`, `GET /assets`.
- **README — Layout updated** to list the full `pipeline/` module set (reframe,
  silence, lutgen, subtitles, metadata, assist) and `llm.py`.
- **CONTRACT.md — stale `facecrop` TODO removed.** It still read "same as
  centercrop for v1 (leave a TODO; no face model required)"; facecrop has been a
  real active-speaker face-track reframe (`pipeline/reframe.py`) since
  2026-11-11. Rewrote the line to match. This was the only TODO in the tree.
- **Added `.env.example`** documenting every config env var (`CLIPFORGE_LLM`,
  `ANTHROPIC_API_KEY`, `CLIPFORGE_MODEL`, `CLIPFORGE_WIN_HOST`,
  `CLIPFORGE_RESOLVE_PORT`, `CLIPFORGE_WIN_SHARE`). Not gitignored (`*.env` does
  not match `.env.example`); `.env` itself stays ignored.

Verified: `server import OK`, README API list matches `@app.*` routes in
`server.py`, `/api/assets` description matches its handler's return shape.

## 2026-11-11 — competitor-parity pass (Opus "ClipAnything", Submagic one-click finish)

Market gap analysis vs opus.pro / submagic / klap.app / vizard / ssemble / lakai.
ClipForge already matched or beat them on virality scoring, active-speaker
reframe, caption templates, silence jump-cuts, punch-in, title cards, B-roll,
LUTs, multi-aspect, SEO metadata. Three gaps closed, surgically (no schema or
render-engine changes — pure reuse of existing pipeline + render flags):

### Clip Anything (prompt re-clipping) — matches Opus "ClipAnything"
- `POST /api/jobs/{id}/reclip` `{"prompt","n"?}`: re-picks clips for an already
  transcribed job from a natural-language prompt ("every money tip", "the
  funniest roasts"), reusing the transcript — no re-download/transcribe. Runs
  `pick_highlights(guidance=prompt)`→`render_all` on a background thread; the UI
  re-polls like a new job. Guards: 409 mid-run, 400 if no transcript.
- UI: a "✨ Clip Anything" prompt bar above the clip grid (prompt + count +
  Find clips), shown once a job has clips.

### One-click "Make viral" — matches Submagic/Klap one-click finish
- `POST /api/jobs/{id}/clips/{slug}/autopilot` `{"preset"?}` + `GET /api/autopilot`.
  Three curated presets (`viral` default, `hormozi`, `clean`), each a composition
  of existing render flags (facecrop + word-pop captions + emphasis/emoji +
  hook punch-in + silence jump-cuts + audio enhance). Merges onto the clip and
  re-renders via the unchanged `render.render_clip`.
- UI: per-card "✨ Make viral" button → one-click social-ready render.

### Virality copilot ("Why not viral?")
- `pipeline.assist.improve_tips` + assist `kind="improve"`: concrete, clip-specific
  edit tips with impact ranking, judged against the clip's transcript, score,
  hook and current render settings. Degrades to an empty list on model hiccup.
- UI: per-card "💡 Why not viral?" button rendering impact-tagged tips.

Verified: `py AST OK`, `server import OK`, app JS `node --check` OK, live server
GET / 200, GET /api/autopilot returns presets, demo job still loads (6 clips),
reclip guard returns 404/409/400 correctly. CONTRACT.md updated with all four
endpoint additions.

## 2026-11-11

Goal for this phase: make ClipForge the best-in-class YouTube→short-form clip
tool, beyond ssemble.com and lakai.pro. Built and validated the following.

### Framing & reframe
- **Active-speaker face-track reframe** (`pipeline/reframe.py`): samples the clip
  at 4 fps via an ffmpeg rawvideo pipe, runs a tiny Ultraface RFB-320 ONNX model
  on the already-present `onnxruntime` (no new pip install), picks the dominant
  speaker (confidence × face width), smooths the horizontal centre (EMA +
  velocity clamp), and animates a `crop` filter via an ffmpeg `sendcmd` script so
  the crop follows the speaker. `facecrop` mode is now real (was a centre-crop
  stub). Graceful fallback to centre crop if the model/onnxruntime is
  unavailable. Model auto-downloads to `assets/models/` on first use (needs net).
- **Rotation-aware `probe_dims`**: reads `tags.rotate` / display-matrix side-data
  rotation and swaps width/height for rotated sources, so portrait phone video is
  not over-cropped.

### Virality intelligence
- Highlight picker (`pipeline/highlight.py`) now returns per clip: `score`
  (0–100 predicted virality), `hook`, `payoff`, `retention_risk`. Clips are
  sorted best-first. UI shows a colour-tiered score badge + hook/payoff/risk chips.

### Captions
- Four burned-in templates (`render.caption_style`): `word-pop` (default),
  `karaoke` (`\kf` sweep), `hormozi` (one big word, green box), `big` (huge pop).
- Dimension-parametric: fonts/margins scale to the output aspect (no distortion
  on 1:1 / 16:9).
- **Translation** (`render.caption_lang`): line-timed captions in a target
  language via the shared LLM backend, graceful fallback to original offline.
- **Power-word emphasis** (`render.caption_emphasis`): lexicon + numbers +
  ALLCAPS words highlighted in green/scale.
- **Auto-emoji** (`render.caption_emoji`): keyword→emoji map, with a monochrome
  NotoEmoji font fallback (`config.ensure_emoji_font`).

### Editing
- **Silence / dead-air jump-cuts** (`pipeline/silence.py` + render wiring):
  `silencedetect` → keep-segments → trim+concat, with full time-remap of
  captions and the reframe track through a keep-map so nothing desyncs. Verified
  60s→56.66s with synced karaoke captions.
- **Hook punch-in zoom** (`render.punch_in`): smooth centre zoom over
  `punch_secs` then hold (retention bump at the open).
- **Hook title card** (`render.title_card`/`title_secs`): burned headline over
  the first seconds, scales with aspect.
- **B-roll cutaways** (`render.broll`): overlay local `assets/broll/` files over
  clip-relative windows.
- Music bed (ducked sidechain) and audio enhance (HPF/denoise/compress/loudnorm)
  were already present.

### Output
- **Multiple aspect ratios** (`render.aspect`): 9:16 / 4:5 / 1:1 / 16:9, threaded
  through every filter + reframe + captions.
- **Multi-aspect batch export** (`render.render_variants` + `POST
  /api/jobs/{id}/clips/{slug}/variants`): render one clip to several aspects in
  one action; files suffixed (`__9x16`) and recorded in `clip["variants"]`.
- **Free LUT grade pack** (`pipeline/lutgen.py`): 9 generated `.cube` grades
  (golden-hour, moody-blue, vibrant-pop, vintage-fade, cyberpunk, noir,
  clean-bright, sunset, film-green), auto-populated via `config.list_luts()`.
  LUT count 5→14.
- **Parallel render** (`render_all`, ThreadPool, workers=cpu/2 capped 3) with
  per-clip error isolation (`_render_error`) and **live progress**
  (`render_progress {done,total}` in job.json, shown as "rendering N/M" in UI).

### Hardening (from a code-review pass)
- Atomic font downloads (`.part` + size check + `os.replace` + timeout) so a
  truncated TTF never sticks.
- `config.job_dir` path-traversal guard.
- `config.win_path` requires `/mnt/<drive>/` (no longer mangles `/mnt/data/x`).
- Reframe inference wrapped (returns `[]`/centre-crop on error); empty remapped
  track → `None`.
- `silence.make_remap` returns `None` for leading/trailing/interior cut gaps.
- `lutgen` uses a unique tempfile (no concurrent-write clobber).

### Run
```bash
cd /home/hakuza/clipforge
/home/hakuza/venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8800
# then open http://127.0.0.1:8800/
```
Demo job `data/jobs/demo-igl-ep8/` (6 rendered clips) lets the UI load without a
re-download. `CONTRACT.md` holds the ground-truth `render`/clip/job schemas.
