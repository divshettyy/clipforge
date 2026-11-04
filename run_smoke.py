import os, sys, time, json, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from pipeline import download, transcribe, highlight, render

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
JOB = os.path.join(config.DATA_DIR, "test-smoke")
os.makedirs(os.path.join(JOB, "clips"), exist_ok=True)


def step(name, fn):
    t = time.time()
    print(f">>> {name} ...", flush=True)
    try:
        r = fn()
        print(f"<<< {name} OK  {time.time()-t:.1f}s", flush=True)
        return r
    except Exception:
        print(f"!!! {name} FAILED {time.time()-t:.1f}s", flush=True)
        traceback.print_exc()
        raise


meta = step("download", lambda: download.download(URL, JOB))
print("   meta:", json.dumps(meta)[:200], flush=True)
step("extract_audio", lambda: transcribe.extract_audio(JOB))
step("transcribe", lambda: transcribe.transcribe(JOB))
tr = json.load(open(os.path.join(JOB, "transcript.json")))
print(f"   transcript: lang={tr['language']} dur={round(tr['duration'])} segs={len(tr['segments'])}", flush=True)
clips = step("pick_highlights", lambda: highlight.pick_highlights(JOB, n=3, guidance="punchy, self-contained"))
for c in clips:
    print(f"   PICK {c['slug']} [{c['start']}-{c['end']}] :: {c['title']}", flush=True)
step("render_all", lambda: render.render_all(JOB))
print("ALL DONE", flush=True)
