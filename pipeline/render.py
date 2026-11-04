"""ClipForge pipeline: render a clip spec into a 1080x1920 H.264/AAC vertical mp4."""
import os
import sys
import json
import subprocess

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config

OUT_W, OUT_H = 1080, 1920


# --------------------------------------------------------------------------- #
# ASS caption generation
# --------------------------------------------------------------------------- #
WHITE = "&H00FFFFFF&"
YELLOW = "&H0000F0FF&"  # active word (AABBGGRR -> yellow)

_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans Devanagari,74,&H00FFFFFF,&H000000FF,&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,1,5,2,2,80,80,240,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_time(t: float) -> str:
    if t < 0:
        t = 0.0
    cs = int(round(t * 100))
    h = cs // 360000
    cs -= h * 360000
    m = cs // 6000
    cs -= m * 6000
    s = cs // 100
    cs -= s * 100
    return "%d:%02d:%02d.%02d" % (h, m, s, cs)


def _collect_words(transcript: dict, start: float, end: float):
    """Words overlapping [start, end], rebased so clip start == 0."""
    words = []
    for seg in transcript.get("segments", []):
        for w in (seg.get("words") or []):
            ws, we = w.get("s"), w.get("e")
            if ws is None or we is None:
                continue
            if we <= start or ws >= end:
                continue
            text = (w.get("w") or "").strip()
            if not text:
                continue
            rs = max(0.0, ws - start)
            re_ = max(rs + 0.02, we - start)
            words.append((rs, min(re_, end - start), text))
    words.sort(key=lambda x: x[0])
    return words


def _build_ass(transcript: dict, start: float, end: float, out_path: str) -> str:
    """Write a 4-word-line ASS with the active word highlighted yellow."""
    words = _collect_words(transcript, start, end)
    lines = [_ASS_HEADER]
    for i in range(0, len(words), 4):
        group = words[i:i + 4]
        texts = [g[2] for g in group]
        for j, (ws, we, _t) in enumerate(group):
            ev_start = ws
            ev_end = group[j + 1][0] if j + 1 < len(group) else we
            if ev_end <= ev_start:
                ev_end = ev_start + 0.08
            parts = []
            for k, word in enumerate(texts):
                if k == j:
                    parts.append("{\\c%s}%s{\\c%s}" % (YELLOW, word, WHITE))
                else:
                    parts.append(word)
            text = " ".join(parts)
            lines.append(
                "Dialogue: 0,%s,%s,Default,,0,0,0,,%s"
                % (_ass_time(ev_start), _ass_time(ev_end), text)
            )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out_path


def _escape_filter_path(p: str) -> str:
    """Escape a path for use inside an ffmpeg filter argument."""
    return p.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def render_clip(job_dir: str, clip: dict, index: int) -> str:
    """Render one clip spec to clips/NN_slug.mp4; mutate clip status/file."""
    src = os.path.join(job_dir, "source.mp4")
    if not os.path.exists(src):
        raise RuntimeError("source.mp4 not found in %s" % job_dir)

    start = float(clip["start"])
    end = float(clip["end"])
    duration = max(0.1, end - start)

    render = clip.get("render") or {}
    mode = render.get("mode", "blurpad")
    captions = bool(render.get("captions", False))
    speed = float(render.get("speed", 1.0) or 1.0)
    zoom = float(render.get("zoom", 1.0) or 1.0)
    speed = min(2.0, max(0.5, speed))
    zoom = min(1.5, max(1.0, zoom))
    audio_mode = (render.get("audio") or "raw")          # raw | enhance
    lut_name = (render.get("lut") or "").strip()          # LUT basename in assets/luts/
    music_name = (render.get("music") or "").strip()      # file in assets/music/

    clips_dir = os.path.join(job_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)
    slug = clip.get("slug") or ("clip%02d" % index)
    out_name = "%02d_%s.mp4" % (index, slug)
    out_path = os.path.join(clips_dir, out_name)

    # --- build video filter chain ---
    if mode == "blurpad":
        fgw = int(round(OUT_W * zoom))
        fgh = int(round(OUT_H * zoom))
        vchain = (
            "[0:v]split=2[bg][fg];"
            "[bg]scale=%d:%d:force_original_aspect_ratio=increase,"
            "crop=%d:%d,boxblur=40:10[bg];"
            "[fg]scale=%d:%d:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2"
            % (OUT_W, OUT_H, OUT_W, OUT_H, fgw, fgh)
        )
    else:
        # centercrop and facecrop (facecrop == centercrop for v1; TODO: face model)
        sw = int(round(OUT_W * zoom))
        sh = int(round(OUT_H * zoom))
        vchain = (
            "[0:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d"
            % (sw, sh, OUT_W, OUT_H)
        )

    # --- video post chain: LUT grade -> captions -> speed ---
    post = ""
    # LUT color grade (free .cube packs in assets/luts/); applied before captions
    # so burned-in text keeps its own colors on top of the grade.
    if lut_name:
        base = lut_name if lut_name.lower().endswith(".cube") else lut_name + ".cube"
        lut_path = os.path.join(config.luts_dir(), os.path.basename(base))
        if os.path.isfile(lut_path):
            post += ",lut3d=%s" % _escape_filter_path(lut_path)

    ass_path = None
    if captions:
        tpath = os.path.join(job_dir, "transcript.json")
        if os.path.exists(tpath):
            with open(tpath, "r", encoding="utf-8") as f:
                transcript = json.load(f)
            ass_path = os.path.join(clips_dir, ".captions_%02d.ass" % index)
            _build_ass(transcript, start, end, ass_path)
            fonts = config.fonts_dir()
            post += ",subtitles=%s:fontsdir=%s" % (
                _escape_filter_path(ass_path), _escape_filter_path(fonts)
            )
    if abs(speed - 1.0) > 1e-6:
        post += ",setpts=PTS/%s" % _fmt(speed)

    vchain += post + "[vout]"

    # --- audio chain: enhance -> speed -> optional ducked music bed ---
    a_filters = []
    if audio_mode == "enhance":
        # studio-ish cleanup: HPF rumble, FFT denoise, gentle compression, EBU R128.
        a_filters += [
            "highpass=f=90",
            "afftdn=nf=-25",
            "acompressor=threshold=-18dB:ratio=3:attack=5:release=120",
            "loudnorm=I=-14:TP=-1.5:LRA=11",
        ]
    if abs(speed - 1.0) > 1e-6:
        a_filters.append("atempo=%s" % _fmt(speed))
    voice_chain = "[0:a]" + (",".join(a_filters) if a_filters else "anull") + "[voice]"

    music_path = None
    if music_name:
        mp = os.path.join(config.music_dir(), os.path.basename(music_name))
        if os.path.isfile(mp):
            music_path = mp

    audio_fc = voice_chain
    if music_path:
        # Music bed ducked under the voice via sidechain compression, then mixed.
        audio_fc += (
            ";[1:a]volume=0.30[mraw]"
            ";[mraw][voice]sidechaincompress=threshold=0.05:ratio=6:attack=5:release=250[mduck]"
            ";[voice][mduck]amix=inputs=2:normalize=0:duration=first[aout]"
        )
        amap = "[aout]"
    else:
        amap = "[voice]"

    filter_complex = vchain + ";" + audio_fc
    maps = ["-map", "[vout]", "-map", amap]

    inputs = ["-ss", _fmt(start), "-t", _fmt(duration), "-i", src]
    if music_path:
        # Loop the bed so it always covers the clip; -t on output trims it.
        inputs += ["-stream_loop", "-1", "-i", music_path]

    cmd = [
        "ffmpeg", "-y", "-nostdin",
    ] + inputs + [
        "-filter_complex", filter_complex,
    ] + maps + [
        "-t", _fmt(duration),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path,
    ]

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if ass_path and os.path.exists(ass_path):
        try:
            os.remove(ass_path)
        except OSError:
            pass
    if proc.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(
            "ffmpeg render failed for %s: %s"
            % (out_name, proc.stderr.decode("utf-8", "replace")[-1200:])
        )

    clip["status"] = "rendered"
    clip["file"] = os.path.join("clips", out_name)
    return out_path


def _fmt(x: float) -> str:
    """Compact float formatting for ffmpeg args."""
    return ("%.3f" % float(x)).rstrip("0").rstrip(".")


def make_thumbnail(job_dir: str, clip: dict, index: int, text: str = "") -> str:
    """Grab a 1280x720 (16:9) thumbnail frame near the clip's midpoint, optional
    bold title overlay. Writes clips/NN_slug.jpg, returns the job-relative path."""
    src = os.path.join(job_dir, "source.mp4")
    if not os.path.exists(src):
        raise RuntimeError("source.mp4 not found in %s" % job_dir)
    start = float(clip["start"])
    end = float(clip["end"])
    mid = start + max(0.0, (end - start)) * 0.5

    clips_dir = os.path.join(job_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)
    slug = clip.get("slug") or ("clip%02d" % index)
    out_name = "%02d_%s.jpg" % (index, slug)
    out_path = os.path.join(clips_dir, out_name)

    vf = "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720"
    if text:
        safe = (text or "").replace("\\", "").replace(":", " ").replace("'", "")
        fontfile = os.path.join(config.fonts_dir(), "NotoSansDevanagari-Bold.ttf")
        draw = (
            "drawtext=fontfile=%s:text='%s':fontcolor=white:fontsize=72:"
            "box=1:boxcolor=black@0.5:boxborderw=24:x=(w-text_w)/2:y=h-text_h-70"
            % (_escape_filter_path(fontfile), safe[:60])
        )
        vf += "," + draw

    cmd = [
        "ffmpeg", "-y", "-nostdin", "-ss", _fmt(mid), "-i", src,
        "-frames:v", "1", "-vf", vf, "-q:v", "3", out_path,
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(
            "ffmpeg thumbnail failed for %s: %s"
            % (out_name, proc.stderr.decode("utf-8", "replace")[-800:])
        )
    rel = os.path.join("clips", out_name)
    clip["thumbnail"] = rel
    return rel


def render_all(job_dir: str) -> None:
    """Render every clip in clips.json and persist the updated specs."""
    cpath = os.path.join(job_dir, "clips.json")
    if not os.path.exists(cpath):
        raise RuntimeError("clips.json not found in %s" % job_dir)
    with open(cpath, "r", encoding="utf-8") as f:
        clips = json.load(f)

    for i, clip in enumerate(clips, start=1):
        render_clip(job_dir, clip, i)
        # Persist incrementally so progress survives a crash.
        with open(cpath, "w", encoding="utf-8") as f:
            json.dump(clips, f, ensure_ascii=False, indent=1)
