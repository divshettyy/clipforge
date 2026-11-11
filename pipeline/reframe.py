"""ClipForge pipeline: active-face reframe track for facecrop mode.

Samples frames from the clip window, runs a tiny ONNX face detector
(Ultra-Light-Fast-Generic-Face-Detector, RFB-320) on onnxruntime, picks the
dominant face per frame, smooths the horizontal centre into a jitter-free track,
and emits an ffmpeg `sendcmd` script that animates a `crop` filter's x so the
output follows the speaker instead of a static centre crop.

No new pip installs: uses the already-present `onnxruntime` + `numpy`, decodes
frames with an ffmpeg rawvideo pipe. If the model cannot be fetched or
onnxruntime is unavailable, callers fall back to a static centre crop.
"""
import os
import sys
import json
import urllib.request
import subprocess

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config

# RFB-320 Ultraface: input 320x240 RGB, mean 127, scale 1/128; outputs decoded
# boxes [N,4] (normalised x1,y1,x2,y2) + scores [N,2] (bg, face).
_MODEL_URL = (
    "https://github.com/onnx/models/raw/main/validated/vision/body_analysis/"
    "ultraface/models/version-RFB-320.onnx"
)
_NET_W, _NET_H = 320, 240
_SCORE_THR = 0.6

_session = None  # cached ort.InferenceSession


def _model_path() -> str:
    d = os.path.join(config.ROOT, "assets", "models")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "ultraface-RFB-320.onnx")


def _ensure_model() -> str:
    p = _model_path()
    if os.path.isfile(p) and os.path.getsize(p) > 100_000:
        return p
    tmp = p + ".part"
    req = urllib.request.Request(_MODEL_URL, headers={"User-Agent": "clipforge"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        f.write(r.read())
    if os.path.getsize(tmp) < 100_000:
        os.remove(tmp)
        raise RuntimeError("ultraface model download looked truncated")
    os.replace(tmp, p)
    return p


def _get_session():
    global _session
    if _session is not None:
        return _session
    import onnxruntime as ort
    sess = ort.InferenceSession(_ensure_model(), providers=["CPUExecutionProvider"])
    _session = sess
    return sess


def available() -> bool:
    """True when face reframe can run (onnxruntime import + model reachable)."""
    try:
        _get_session()
        return True
    except Exception:
        return False


def probe_dims(src: str):
    """Return the DISPLAY (width, height) of the source via ffprobe, or None.

    Accounts for rotation metadata (tags.rotate or a display-matrix side-data
    rotation): ffmpeg autorotates decoded frames, so a 1920x1080 stream tagged
    90deg decodes to 1080x1920. Dimensions are swapped to match the real frame.
    """
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries",
             "stream=width,height:stream_tags=rotate:stream_side_data=rotation",
             "-of", "json", src],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        info = json.loads(out.stdout.decode("utf-8", "replace"))
        st = info["streams"][0]
        w, h = int(st["width"]), int(st["height"])
        rot = 0
        tags = st.get("tags") or {}
        if tags.get("rotate"):
            rot = int(float(tags["rotate"]))
        for sd in (st.get("side_data_list") or []):
            if sd.get("rotation") is not None:
                rot = int(float(sd["rotation"]))
                break
        if abs(rot) % 180 == 90:
            w, h = h, w
        return w, h
    except Exception:
        return None


def _iter_frames(src: str, start: float, dur: float, fps: float):
    """Yield (t_rel, ndarray[H,W,3] uint8) sampled at `fps`, scaled to net size."""
    cmd = [
        "ffmpeg", "-nostdin", "-v", "error",
        "-ss", "%.3f" % start, "-t", "%.3f" % dur, "-i", src,
        "-vf", "fps=%g,scale=%d:%d" % (fps, _NET_W, _NET_H),
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    frame_bytes = _NET_W * _NET_H * 3
    i = 0
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            arr = np.frombuffer(buf, dtype=np.uint8).reshape(_NET_H, _NET_W, 3)
            yield (i / fps, arr)
            i += 1
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        proc.wait()


def _detect_faces(sess, frame):
    """Run the detector; return list of (score, cx, cy) normalised to [0,1]."""
    x = (frame.astype(np.float32) - 127.0) / 128.0
    x = np.transpose(x, (2, 0, 1))[None, ...]  # NCHW
    inp = sess.get_inputs()[0].name
    outs = sess.run(None, {inp: x})
    boxes = scores = None
    for o in outs:
        if o.ndim == 3 and o.shape[-1] == 4:
            boxes = o[0]
        elif o.ndim == 3 and o.shape[-1] == 2:
            scores = o[0]
    if boxes is None or scores is None:
        return []
    face_p = scores[:, 1]
    keep = face_p >= _SCORE_THR
    faces = []
    for b, p in zip(boxes[keep], face_p[keep]):
        x1, y1, x2, y2 = b
        faces.append((float(p), float((x1 + x2) / 2), float((y1 + y2) / 2),
                      float(max(0.0, x2 - x1))))
    return faces


def compute_center_track(src, start, dur, fps=4.0):
    """Sample the clip; return list of (t_rel, cx_norm) for the dominant face.

    Dominant = highest (confidence * width). Frames with no face reuse the last
    known centre (or 0.5). Returns [] if detection is unavailable.
    """
    try:
        sess = _get_session()
    except Exception:
        return []
    track = []
    last = 0.5
    for t, frame in _iter_frames(src, start, dur, fps):
        try:
            faces = _detect_faces(sess, frame)
        except Exception:
            faces = []  # inference hiccup -> reuse last centre, keep going
        if faces:
            # score weighted by face width -> prefer the close, confident speaker.
            _, cx, _cy, _w = max(faces, key=lambda f: f[0] * (0.3 + f[3]))
            last = cx
        track.append((t, last))
    return track


def _smooth(track, alpha=0.25, max_step=0.04):
    """EMA + per-step velocity clamp to kill jitter and whip-pans."""
    out = []
    s = track[0][1] if track else 0.5
    for t, cx in track:
        target = alpha * cx + (1 - alpha) * s
        delta = max(-max_step, min(max_step, target - s))
        s += delta
        out.append((t, s))
    return out


def build_crop_filter(src, start, dur, job_dir, index, zoom=1.0, fps=4.0,
                      out_w=None, out_h=None, keep=None):
    """Return (filter_str, cmds_path) for a speaker-following crop, or None.

    filter_str chains sendcmd -> crop(animated x) -> scale to out_w x out_h.
    Caller deletes cmds_path after ffmpeg finishes. None => fall back to
    static centre crop (no faces / detector unavailable).

    `keep` (silence keep-map) remaps the crop track onto the trimmed timeline
    so the reframe stays locked to the speaker after jump-cuts.
    """
    from pipeline.render import OUT_W, OUT_H  # avoid import cycle at module load
    if out_w is None:
        out_w = OUT_W
    if out_h is None:
        out_h = OUT_H

    dims = probe_dims(src)
    if not dims:
        return None
    iw, ih = dims
    track = compute_center_track(src, start, dur, fps=fps)
    if not track:
        return None
    track = _smooth(track)

    # Remap sample times onto the trimmed timeline; drop samples in cut gaps.
    if keep:
        from pipeline import silence
        remap = silence.make_remap(keep)
        rtrack = []
        for (t, cx) in track:
            nt = remap(t)
            if nt is not None:
                rtrack.append((nt, cx))
        if not rtrack:
            return None  # every sample fell in a cut -> let caller centre-crop
        track = rtrack

    zoom = min(1.5, max(1.0, float(zoom or 1.0)))
    # Crop a window matching the target aspect that fills height, tightened by
    # zoom. Clamp both dims to the source so a narrow source can't over-crop.
    ch = int(round(ih / zoom))
    cw = int(round(ch * out_w / out_h))
    if cw > iw:
        # Source narrower than target aspect: fit width, crop height instead.
        cw = iw
        ch = min(ih, int(round(cw * out_h / out_w)))
    ch = min(ch, ih)
    cw = min(cw, iw)
    y0 = max(0, (ih - ch) // 2)
    x_max = max(0, iw - cw)

    def _x_for(cx_norm):
        x = cx_norm * iw - cw / 2.0
        return int(round(min(x_max, max(0, x))))

    lines = []
    for t, cx in track:
        lines.append("%.3f crop x %d;" % (t, _x_for(cx)))
    cmds_path = os.path.join(job_dir, "clips", ".reframe_%02d.cmds" % index)
    os.makedirs(os.path.dirname(cmds_path), exist_ok=True)
    with open(cmds_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    x0 = _x_for(track[0][1])
    esc = cmds_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    filt = (
        "[0:v]sendcmd=f=%s,crop=w=%d:h=%d:x=%d:y=%d,scale=%d:%d"
        % (esc, cw, ch, x0, y0, out_w, out_h)
    )
    return filt, cmds_path
