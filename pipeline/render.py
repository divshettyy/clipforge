"""ClipForge pipeline: render a clip spec into an H.264/AAC vertical/social mp4.

Output aspect is selectable per clip (9:16 default, plus 4:5, 1:1, 16:9).
Render features: blurpad/centercrop/facecrop framing, speaker-follow reframe,
burned caption templates (word-pop/karaoke/hormozi/big) with translation,
hook title card, b-roll cutaways, silence/dead-air jump-cuts (with caption +
reframe time remap), LUT grade, audio enhance, ducked music bed.
"""
import os
import sys
import copy
import json
import subprocess

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config

OUT_W, OUT_H = 1080, 1920

# Output aspect presets (width, height). 9:16 default; others for multi-platform.
ASPECTS = {
    "9:16": (1080, 1920),
    "4:5": (1080, 1350),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
}


def aspect_dims(name):
    """Return (w, h) for an aspect preset name; falls back to 9:16."""
    return ASPECTS.get((name or "9:16").strip(), (OUT_W, OUT_H))


# --------------------------------------------------------------------------- #
# ASS caption generation
# --------------------------------------------------------------------------- #
WHITE = "&H00FFFFFF&"
YELLOW = "&H0000F0FF&"  # active word (AABBGGRR -> yellow)

# Caption templates the UI/edit layer can select via render.caption_style.
CAPTION_STYLES = ("word-pop", "karaoke", "hormozi", "big")

_FONT = "Noto Sans Devanagari"

# Power words to emphasise (colour + scale) when render.emphasis is on.
_POWER_WORDS = {
    "never", "always", "every", "everyone", "nobody", "huge", "massive",
    "insane", "crazy", "secret", "free", "money", "rich", "best", "worst",
    "first", "last", "only", "win", "won", "lose", "lost", "died", "death",
    "love", "hate", "shocking", "unbelievable", "literally", "millions",
    "billions", "percent", "fastest", "biggest", "smallest", "wrong", "right",
    "true", "false", "stop", "now", "new", "proof", "guarantee", "instantly",
    "forever", "everything", "nothing", "exactly", "truth", "lie",
}

# Keyword -> emoji for auto-emoji captions (render.emoji).
_EMOJI_MAP = {
    "money": "\U0001F4B0", "cash": "\U0001F4B0", "dollar": "\U0001F4B0",
    "rich": "\U0001F4B0", "fire": "\U0001F525", "insane": "\U0001F525",
    "crazy": "\U0001F525", "lit": "\U0001F525", "love": "❤",
    "heart": "❤", "time": "⏰", "clock": "⏰", "hour": "⏰",
    "think": "\U0001F914", "thought": "\U0001F914", "win": "\U0001F3C6",
    "winner": "\U0001F3C6", "won": "\U0001F3C6", "victory": "\U0001F3C6",
    "up": "\U0001F4C8", "growth": "\U0001F4C8", "grow": "\U0001F4C8",
    "increase": "\U0001F4C8", "rise": "\U0001F4C8", "down": "\U0001F4C9",
    "drop": "\U0001F4C9", "crash": "\U0001F4C9", "idea": "\U0001F4A1",
    "warning": "⚠", "danger": "⚠", "star": "⭐",
    "best": "⭐", "laugh": "\U0001F602", "funny": "\U0001F602",
    "mind": "\U0001F9E0", "brain": "\U0001F9E0", "look": "\U0001F440",
    "watch": "\U0001F440", "see": "\U0001F440", "rocket": "\U0001F680",
    "launch": "\U0001F680", "hundred": "\U0001F4AF", "music": "\U0001F3B5",
    "song": "\U0001F3B5", "phone": "\U0001F4F1",
}


def _clean_token(text):
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _word_flags(text, emphasis, emoji):
    """Return (is_emphasised, emoji_char) for a caption word."""
    tok = _clean_token(text)
    emph = False
    if emphasis and tok:
        if tok in _POWER_WORDS:
            emph = True
        elif any(c.isdigit() for c in text):  # numbers / stats
            emph = True
        elif len(text) >= 2 and text.isupper():  # author already shouted it
            emph = True
    emo = ""
    if emoji and tok:
        emo = _EMOJI_MAP.get(tok, "")
    return emph, emo
# AABBGGRR colours.
_C_WHITE = "&H00FFFFFF"
_C_YELLOW = "&H0000F0FF"
_C_BLACK = "&H00000000"
_C_GREEN = "&H0022CC22"   # hormozi active box
_C_SHADOW = "&HA0000000"

# Style name per template (referenced by the Dialogue builders).
_STYLE_NAME = {"word-pop": "Default", "karaoke": "Kara",
               "hormozi": "Hormozi", "big": "Big"}


def _style_line(style: str, w: int, h: int) -> str:
    """Build one [V4+ Styles] line for `style`, scaled to the output height.

    Font sizes/margins are authored against a 1920px-tall canvas and scaled by
    h/1920 so captions look consistent across aspect ratios. MarginV (vertical)
    scales with h; MarginL/R (horizontal) scale with w.
    """
    sv = h / 1920.0
    sh = w / 1080.0

    def pv(x):  # vertical-scaled int
        return max(1, int(round(x * sv)))

    def ph(x):  # horizontal-scaled int
        return max(1, int(round(x * sh)))

    if style == "karaoke":
        return ("Style: Kara,%s,%d,%s,%s,%s,%s,-1,0,0,0,100,100,0,0,1,%d,%d,2,%d,%d,%d,1"
                % (_FONT, pv(76), _C_YELLOW, _C_WHITE, _C_BLACK, _C_SHADOW,
                   pv(5), pv(2), ph(80), ph(80), pv(240)))
    if style == "hormozi":
        return ("Style: Hormozi,%s,%d,%s,&H000000FF,%s,%s,-1,0,0,0,100,100,0,0,3,%d,0,5,%d,%d,0,1"
                % (_FONT, pv(110), _C_WHITE, _C_GREEN, _C_GREEN,
                   pv(8), ph(60), ph(60)))
    if style == "big":
        return ("Style: Big,%s,%d,%s,&H000000FF,%s,%s,-1,0,0,0,100,100,0,0,1,%d,%d,5,%d,%d,0,1"
                % (_FONT, pv(120), _C_YELLOW, _C_BLACK, _C_SHADOW,
                   pv(9), pv(3), ph(60), ph(60)))
    # word-pop (default)
    return ("Style: Default,%s,%d,%s,&H000000FF,%s,%s,-1,0,0,0,100,100,0,0,1,%d,%d,2,%d,%d,%d,1"
            % (_FONT, pv(74), _C_WHITE, _C_BLACK, _C_SHADOW,
               pv(5), pv(2), ph(80), ph(80), pv(240)))


def _ass_header(style: str, w: int = OUT_W, h: int = OUT_H) -> str:
    if style not in CAPTION_STYLES:
        style = "word-pop"
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: %d\nPlayResY: %d\nWrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        "%s\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n" % (w, h, _style_line(style, w, h))
    )


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


_EMPH = "&H0022CC22&"  # emphasis colour (green), inline \c form


def _tok(word, emo):
    return word + ((" " + emo) if emo else "")


def _wp_dialogues(words, emphasis=False, emoji=False):
    """word-pop: 4-word lines, active word yellow; power words green; emoji."""
    out = []
    for i in range(0, len(words), 4):
        group = words[i:i + 4]
        texts = [g[2] for g in group]
        metas = [_word_flags(g[2], emphasis, emoji) for g in group]
        for j, (ws, we, _t) in enumerate(group):
            ev_start = ws
            ev_end = group[j + 1][0] if j + 1 < len(group) else we
            if ev_end <= ev_start:
                ev_end = ev_start + 0.08
            parts = []
            for k, word in enumerate(texts):
                emph, emo = metas[k]
                tok = _tok(word, emo)
                if k == j:
                    sc = "\\fscx112\\fscy112" if emph else ""
                    parts.append("{\\c%s%s}%s{\\c%s\\fscx100\\fscy100}"
                                 % (YELLOW, sc, tok, WHITE))
                elif emph:
                    parts.append("{\\c%s}%s{\\c%s}" % (_EMPH, tok, WHITE))
                else:
                    parts.append(tok)
            out.append("Dialogue: 0,%s,%s,Default,,0,0,0,,%s"
                       % (_ass_time(ev_start), _ass_time(ev_end), " ".join(parts)))
    return out


def _kara_dialogues(words, emphasis=False, emoji=False):
    """karaoke: one Dialogue per 4-word line, \\kf sweep fills each word."""
    out = []
    for i in range(0, len(words), 4):
        group = words[i:i + 4]
        if not group:
            continue
        ls = group[0][0]
        le = max(g[1] for g in group)
        if le <= ls:
            le = ls + 0.4
        parts = []
        for (ws, we, t) in group:
            _emph, emo = _word_flags(t, False, emoji)  # \kf owns colour
            dur_cs = max(6, int(round((we - ws) * 100)))
            parts.append("{\\kf%d}%s " % (dur_cs, _tok(t, emo)))
        out.append("Dialogue: 0,%s,%s,Kara,,0,0,0,,%s"
                   % (_ass_time(ls), _ass_time(le), "".join(parts).rstrip()))
    return out


def _single_word_dialogues(words, style_name, pop=False, emphasis=False, emoji=False):
    """hormozi / big: one word on screen at a time, gap-free."""
    out = []
    n = len(words)
    for idx, (ws, we, t) in enumerate(words):
        ev_start = ws
        ev_end = words[idx + 1][0] if idx + 1 < n else we
        if ev_end <= ev_start:
            ev_end = ev_start + 0.12
        emph, emo = _word_flags(t, emphasis, emoji)
        disp = _tok(t.upper(), emo)
        tags = ""
        if pop:
            tags += "\\fscx112\\fscy112"
        if emph:
            tags += "\\c%s" % _EMPH
        tag = ("{%s}" % tags) if tags else ""
        out.append("Dialogue: 0,%s,%s,%s,,0,0,0,,%s%s"
                   % (_ass_time(ev_start), _ass_time(ev_end), style_name, tag, disp))
    return out


def _collect_segments(transcript, start, end):
    """Segments overlapping [start,end], rebased to clip 0. [(rs,re,text),...]."""
    out = []
    for seg in transcript.get("segments", []):
        s, e = seg.get("start"), seg.get("end")
        tx = (seg.get("text") or "").strip()
        if s is None or e is None or not tx:
            continue
        if e <= start or s >= end:
            continue
        rs = max(0.0, s - start)
        re_ = min(end - start, e - start)
        if re_ <= rs:
            re_ = rs + 0.3
        out.append((rs, re_, tx))
    return out


def _translate_lines(lines, lang):
    """Translate a list of caption lines to `lang` via the shared LLM backend.
    Returns a same-length list; falls back to the originals on any error."""
    if not lines:
        return lines
    try:
        import llm
        numbered = "\n".join("%d. %s" % (i + 1, t) for i, t in enumerate(lines))
        prompt = (
            "Translate each numbered subtitle line into %s. Keep it short and "
            "natural for burned-in video captions. Return ONLY a JSON array of "
            "strings, same order and same count, no numbering, no prose.\n\n%s"
            % (lang, numbered)
        )
        reply = llm.complete(prompt, system="You are a precise subtitle translator.",
                             max_tokens=2048)
        import re as _re
        m = _re.search(r"\[.*\]", reply, _re.DOTALL)
        arr = json.loads(m.group(0) if m else reply)
        out = [str(x) for x in arr]
        if len(out) == len(lines):
            return out
    except Exception:
        pass
    return lines


def _build_translated_ass(transcript, start, end, out_path, lang,
                          w=OUT_W, h=OUT_H, keep=None):
    """Line-timed ASS in a target language (word timing is lost in translation,
    so captions display one segment line at a time)."""
    segs = _collect_segments(transcript, start, end)
    texts = _translate_lines([s[2] for s in segs], lang)
    remap = None
    if keep:
        from pipeline import silence
        remap = silence.make_remap(keep)
    lines = [_ass_header("word-pop", w, h)]
    for (rs, re_, _orig), tx in zip(segs, texts):
        if remap:
            ns, ne = remap(rs), remap(re_)
            if ns is None and ne is None:
                continue
            rs = ns if ns is not None else (ne or 0.0)
            re_ = ne if ne is not None else (rs + 0.4)
            if re_ <= rs:
                re_ = rs + 0.4
        tx = tx.replace("{", "(").replace("}", ")")
        lines.append("Dialogue: 0,%s,%s,Default,,0,0,0,,%s"
                     % (_ass_time(rs), _ass_time(re_), tx))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out_path


def _remap_span(start, end, remap, min_len=0.08):
    """Remap a single [start,end) clip-relative span through a silence
    keep-map's remap(t) function. Returns (new_start, new_end) or None if the
    span falls entirely inside a cut gap. Shared by captions (_remap_words)
    and b-roll windows (render_clip), which both need the exact same
    clamp-to-nearest-kept-edge behavior to stay in sync with the trimmed
    [vtrim]/[atrim] timeline -- without it, whichever one skips the remap
    silently desyncs from the others the moment trim_silence is combined
    with it.
    """
    ns, ne = remap(start), remap(end)
    if ns is None and ne is None:
        return None  # falls entirely inside a cut gap
    if ns is None:
        ns = remap(max(0.0, end - 0.001)) or 0.0
    if ne is None:
        ne = remap(max(0.0, end - 0.001))
        if ne is None:
            ne = ns + min_len
    if ne <= ns:
        ne = ns + min_len
    return ns, ne


def _remap_words(words, keep):
    """Remap rebased word spans through a silence keep-map; drop cut words."""
    if not keep:
        return words
    from pipeline import silence
    remap = silence.make_remap(keep)
    out = []
    for (rs, re_, text) in words:
        span = _remap_span(rs, re_, remap)
        if span is None:
            continue
        out.append((span[0], span[1], text))
    return out


def _build_ass(transcript: dict, start: float, end: float, out_path: str,
               style: str = "word-pop", w: int = OUT_W, h: int = OUT_H,
               keep=None, emphasis=False, emoji=False) -> str:
    """Write a burn-in ASS for [start,end] in the chosen caption template."""
    if style not in CAPTION_STYLES:
        style = "word-pop"
    words = _remap_words(_collect_words(transcript, start, end), keep)
    lines = [_ass_header(style, w, h)]
    if style == "karaoke":
        lines += _kara_dialogues(words, emphasis=emphasis, emoji=emoji)
    elif style == "hormozi":
        lines += _single_word_dialogues(words, "Hormozi", pop=False,
                                        emphasis=emphasis, emoji=emoji)
    elif style == "big":
        lines += _single_word_dialogues(words, "Big", pop=True,
                                        emphasis=emphasis, emoji=emoji)
    else:
        lines += _wp_dialogues(words, emphasis=emphasis, emoji=emoji)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out_path


def _escape_filter_path(p: str) -> str:
    """Escape a path for use inside an ffmpeg filter argument."""
    return p.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _escape_drawtext(s: str) -> str:
    """Escape text for an ffmpeg drawtext `text=` value (single-line, clamped).
    Apostrophes become the typographic variant to avoid quote breakage."""
    s = (s or "").replace("\n", " ").replace("\r", " ")
    s = s.replace("\\", "\\\\").replace("%", "\\%")
    s = s.replace(":", "\\:").replace("'", "’").replace('"', "")
    return s.strip()[:60]


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def render_clip(job_dir: str, clip: dict, index: int, name_suffix: str = "") -> str:
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
    caption_style = (render.get("caption_style") or "word-pop").strip()  # template
    caption_lang = (render.get("caption_lang") or "").strip()  # translate target, e.g. "es"
    cap_emphasis = bool(render.get("caption_emphasis", False))  # power-word highlight
    cap_emoji = bool(render.get("caption_emoji", False))        # keyword emoji
    broll_spec = render.get("broll") or []                # [{file,start,end}] clip-relative
    aspect = (render.get("aspect") or "9:16").strip()     # 9:16 | 4:5 | 1:1 | 16:9
    ow, oh = aspect_dims(aspect)
    # Hook title card: burned headline over the first seconds (retention boost).
    title_card = render.get("title_card")
    if title_card is True:  # auto from the clip's hook/title
        title_card = (clip.get("hook") or clip.get("title") or "").strip()
    title_card = (str(title_card).strip() if title_card else "")
    title_secs = float(render.get("title_secs", 1.8) or 1.8)
    title_secs = min(6.0, max(0.5, title_secs))
    punch_in = render.get("punch_in", False)             # hook zoom push
    punch_amt = 0.08
    if isinstance(punch_in, (int, float)) and not isinstance(punch_in, bool):
        punch_amt = float(punch_in)
        punch_in = punch_amt > 0.001
    punch_in = bool(punch_in)
    punch_amt = min(0.25, max(0.02, punch_amt))
    punch_secs = float(render.get("punch_secs", title_secs) or title_secs)
    punch_secs = min(6.0, max(0.4, punch_secs))
    trim_silence = bool(render.get("trim_silence", False))  # jump-cut dead air
    sil_thresh = float(render.get("silence_db", -32.0) or -32.0)
    sil_min = float(render.get("silence_min", 0.35) or 0.35)

    # Compute the silence keep-map up front; downstream caption/reframe times
    # are remapped through it so nothing desyncs after the cut.
    keep = None
    out_dur = duration
    if trim_silence:
        try:
            from pipeline import silence
            km = silence.keep_segments(src, start, duration,
                                       thresh_db=sil_thresh, min_silence=sil_min)
            if silence.is_trimmed(km, duration):
                keep = km
                out_dur = silence.total_kept(km)
        except Exception:
            keep = None  # detection failed -> render untrimmed

    clips_dir = os.path.join(job_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)
    slug = clip.get("slug") or ("clip%02d" % index)
    out_name = "%02d_%s%s.mp4" % (index, slug, name_suffix)
    out_path = os.path.join(clips_dir, out_name)

    # --- build video filter chain ---
    reframe_cmds = None
    if mode == "blurpad":
        fgw = int(round(ow * zoom))
        fgh = int(round(oh * zoom))
        vchain = (
            "[0:v]split=2[bg][fg];"
            "[bg]scale=%d:%d:force_original_aspect_ratio=increase,"
            "crop=%d:%d,boxblur=40:10[bg];"
            "[fg]scale=%d:%d:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2"
            % (ow, oh, ow, oh, fgw, fgh)
        )
    elif mode == "facecrop":
        # Active-face reframe: crop follows the dominant speaker across the clip.
        # Falls back to a static centre crop if the detector/model is unavailable.
        reframe_cmds = None
        built = None
        try:
            from pipeline import reframe
            built = reframe.build_crop_filter(
                src, start, duration, job_dir, index, zoom=zoom, out_w=ow, out_h=oh,
                keep=keep
            )
        except Exception:
            built = None
        if built:
            vchain, reframe_cmds = built
        else:
            sw = int(round(ow * zoom))
            sh = int(round(oh * zoom))
            vchain = (
                "[0:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d"
                % (sw, sh, ow, oh)
            )
    else:
        # centercrop
        sw = int(round(ow * zoom))
        sh = int(round(oh * zoom))
        vchain = (
            "[0:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d"
            % (sw, sh, ow, oh)
        )

    # When trimming silence, the framing chain reads the concatenated [vtrim]
    # instead of the raw source stream.
    if keep:
        vchain = vchain.replace("[0:v]", "[vtrim]", 1)

    # --- video post chain: punch-in -> LUT grade -> captions -> title -> speed ---
    post = ""
    # Hook punch-in: smooth centre zoom that pushes in over `punch_secs` then
    # holds, for a retention bump at the open. Applied before captions so the
    # burned text is never scaled. Commas inside expressions are escaped.
    if punch_in:
        z = "(1+%g*min(t/%g\\,1))" % (punch_amt, punch_secs)
        post += (
            ",crop=w='iw/%s':h='ih/%s':x='(iw-ow)/2':y='(ih-oh)/2',scale=%d:%d"
            % (z, z, ow, oh)
        )
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
            if cap_emoji:
                try:
                    config.ensure_emoji_font()
                except Exception:
                    pass
            if caption_lang:
                _build_translated_ass(transcript, start, end, ass_path,
                                      caption_lang, w=ow, h=oh, keep=keep)
            else:
                _build_ass(transcript, start, end, ass_path,
                           style=caption_style, w=ow, h=oh, keep=keep,
                           emphasis=cap_emphasis, emoji=cap_emoji)
            fonts = config.fonts_dir()
            post += ",subtitles=%s:fontsdir=%s" % (
                _escape_filter_path(ass_path), _escape_filter_path(fonts)
            )

    # Hook title card over the first `title_secs` (before speed remap so its
    # timing is in pre-speed clip seconds, matching the enable window).
    if title_card:
        fontfile = os.path.join(config.fonts_dir(), "NotoSansDevanagari-Bold.ttf")
        fs = max(28, int(round(0.052 * oh)))
        bw = max(10, int(round(0.012 * oh)))
        yy = int(round(0.11 * oh))
        post += (
            ",drawtext=fontfile=%s:text='%s':fontcolor=white:fontsize=%d:"
            "box=1:boxcolor=black@0.62:boxborderw=%d:line_spacing=10:"
            "x=(w-text_w)/2:y=%d:enable='lt(t\\,%s)'"
            % (_escape_filter_path(fontfile), _escape_drawtext(title_card),
               fs, bw, yy, _fmt(title_secs))
        )

    if abs(speed - 1.0) > 1e-6:
        post += ",setpts=PTS/%s" % _fmt(speed)

    # --- b-roll cutaways: overlay local assets over clip-relative windows ---
    # NOTE: broll windows are given in clip-relative (pre-silence-cut) seconds.
    # When trim_silence is on, [vbase] below reads the trimmed/concatenated
    # [vtrim] timeline (see the `vchain = vchain.replace("[0:v]", "[vtrim]", 1)`
    # above), so an unremapped window would overlay at the wrong point -- the
    # wrong footage, or not at all if it now falls past the shorter trimmed
    # duration. Captions and the reframe track already remap through the same
    # keep-map (_remap_words / make_remap above); b-roll needs the identical
    # treatment to stay in sync with both.
    broll_remap = None
    if keep:
        from pipeline import silence
        broll_remap = silence.make_remap(keep)
    broll_files = []  # absolute paths, input order after source
    broll_items = []  # (input_index, start, end)
    for b in broll_spec:
        try:
            fn = os.path.basename(str(b.get("file") or "").strip())
            if not fn:
                continue
            bp = os.path.join(config.broll_dir(), fn)
            if not os.path.isfile(bp):
                continue
            a = max(0.0, float(b.get("start", 0) or 0))
            bb = float(b.get("end", a) or a)
            if bb <= a:
                continue
            if broll_remap:
                span = _remap_span(a, bb, broll_remap, min_len=0.1)
                if span is None:
                    continue
                a, bb = span
            broll_items.append((1 + len(broll_files), a, bb))
            broll_files.append(bp)
        except (TypeError, ValueError):
            continue

    if broll_items:
        video_fc = vchain + "[vbase]"
        cur = "[vbase]"
        for j, (iidx, a, bb) in enumerate(broll_items):
            video_fc += (
                ";[%d:v]scale=%d:%d:force_original_aspect_ratio=increase,"
                "crop=%d:%d,setpts=PTS-STARTPTS[br%d]"
                % (iidx, ow, oh, ow, oh, j)
            )
            video_fc += (
                ";%s[br%d]overlay=0:0:enable='between(t,%s,%s)'[v%d]"
                % (cur, j, _fmt(a), _fmt(bb), j)
            )
            cur = "[v%d]" % j
        if post:
            video_fc += ";" + cur + post[1:] + "[vout]"
        else:
            video_fc += ";" + cur + "null[vout]"
        vchain = video_fc
    else:
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
    asrc = "[atrim]" if keep else "[0:a]"
    voice_chain = asrc + (",".join(a_filters) if a_filters else "anull") + "[voice]"

    music_path = None
    if music_name:
        mp = os.path.join(config.music_dir(), os.path.basename(music_name))
        if os.path.isfile(mp):
            music_path = mp

    # Music is the LAST input; its index shifts past any b-roll inputs.
    music_idx = 1 + len(broll_files)
    audio_fc = voice_chain
    if music_path:
        # Music bed ducked under the voice via sidechain compression, then mixed.
        audio_fc += (
            ";[%d:a]volume=0.30[mraw]"
            ";[mraw][voice]sidechaincompress=threshold=0.05:ratio=6:attack=5:release=250[mduck]"
            ";[voice][mduck]amix=inputs=2:normalize=0:duration=first[aout]"
            % music_idx
        )
        amap = "[aout]"
    else:
        amap = "[voice]"

    # Silence-trim prefix: build [vtrim]/[atrim] from the raw source, which the
    # framing + voice chains above were spliced to read from.
    trim_prefix = ""
    if keep:
        from pipeline import silence
        vtrim_fc, atrim_fc = silence.build_trim_filters(keep)
        trim_prefix = vtrim_fc + ";" + atrim_fc + ";"

    filter_complex = trim_prefix + vchain + ";" + audio_fc
    maps = ["-map", "[vout]", "-map", amap]

    inputs = ["-ss", _fmt(start), "-t", _fmt(duration), "-i", src]
    # B-roll inputs (indices 1..N), in the order referenced by the overlays.
    for bp in broll_files:
        inputs += ["-i", bp]
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
    if reframe_cmds and os.path.exists(reframe_cmds):
        try:
            os.remove(reframe_cmds)
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


def render_variants(job_dir, clip, index, aspects):
    """Render `clip` to each aspect in `aspects` (subset of ASPECTS keys),
    writing a separate file per aspect, and record them on the clip.
    Returns {aspect: job-relative-path}. Does not change the clip's own
    primary "file"/"aspect"."""
    wanted = [a for a in (aspects or []) if a in ASPECTS]
    if not wanted:
        return {}
    variants = dict(clip.get("variants") or {})
    for aspect in wanted:
        cp = copy.deepcopy(clip)
        cp["render"] = copy.deepcopy(clip.get("render") or {})
        cp["render"]["aspect"] = aspect
        suffix = "__" + aspect.replace(":", "x")
        out_path = render_clip(job_dir, cp, index, name_suffix=suffix)
        variants[aspect] = os.path.join("clips", os.path.basename(out_path))
    clip["variants"] = variants
    return variants


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


def render_all(job_dir: str, workers: int = None) -> None:
    """Render every clip in clips.json in parallel and persist the specs.

    Each ffmpeg job is already multi-threaded, so workers is kept small to
    avoid CPU oversubscription. One clip failing records its error on that clip
    and does not abort the others. clips.json is rewritten under a lock after
    each clip finishes so progress survives a crash.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    cpath = os.path.join(job_dir, "clips.json")
    if not os.path.exists(cpath):
        raise RuntimeError("clips.json not found in %s" % job_dir)
    with open(cpath, "r", encoding="utf-8") as f:
        clips = json.load(f)

    if workers is None:
        workers = max(1, min(3, (os.cpu_count() or 2) // 2))
    lock = threading.Lock()
    total = len(clips)
    done = [0]
    jpath = os.path.join(job_dir, "job.json")

    def _persist():
        with open(cpath, "w", encoding="utf-8") as f:
            json.dump(clips, f, ensure_ascii=False, indent=1)
        # Merge per-clip render progress into job.json without clobbering it.
        try:
            j = {}
            if os.path.exists(jpath):
                with open(jpath, "r", encoding="utf-8") as f:
                    j = json.load(f)
            j["render_progress"] = {"done": done[0], "total": total}
            with open(jpath, "w", encoding="utf-8") as f:
                json.dump(j, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def _one(i, clip):
        try:
            render_clip(job_dir, clip, i)
            clip.pop("_render_error", None)
        except Exception as exc:  # keep going; record per-clip
            clip["status"] = clip.get("status") or "pending"
            clip["_render_error"] = str(exc)[-400:]
        with lock:
            done[0] += 1
            _persist()

    if workers <= 1 or len(clips) <= 1:
        for i, clip in enumerate(clips, start=1):
            _one(i, clip)
        return

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_one, i, clip) for i, clip in enumerate(clips, start=1)]
        for _ in as_completed(futs):
            pass
    _persist()
