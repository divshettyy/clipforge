# ClipForge — Changelog

A running log of feature work and fixes. Newest first. Keep appending to this
file as the tool evolves.

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
