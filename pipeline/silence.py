"""ClipForge pipeline: silence / dead-air removal (jump-cuts).

Detects silent gaps inside a clip window with ffmpeg `silencedetect`, computes
the speech segments to keep, and provides:
  - keep-segment detection (clip-relative seconds, 0..duration),
  - a time remapper (untrimmed clip time -> trimmed timeline) so captions and
    the speaker-follow reframe track stay in sync after cutting,
  - ffmpeg trim+concat filter prefixes for video and audio.

All times are relative to the clip start (the caller reads the source with
`-ss start -t duration`, which rebases timestamps to 0).
"""
import re
import subprocess

_SILENCE_RE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_RE_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def detect_silences(src, start, dur, thresh_db=-32.0, min_silence=0.35):
    """Return a list of (a, b) silent intervals (clip-relative seconds).

    Only silences at least `min_silence` long are reported. Robust to the
    final silence having no explicit end (clamped to `dur`).
    """
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner",
        "-ss", "%.3f" % start, "-t", "%.3f" % dur, "-i", src,
        "-map", "0:a:0", "-af",
        "silencedetect=n=%gdB:d=%g" % (thresh_db, min_silence),
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    text = proc.stderr.decode("utf-8", "replace")
    sils = []
    cur = None
    for line in text.splitlines():
        ms = _SILENCE_RE_START.search(line)
        if ms:
            cur = max(0.0, float(ms.group(1)))
            continue
        me = _SILENCE_RE_END.search(line)
        if me and cur is not None:
            e = min(dur, float(me.group(1)))
            if e > cur:
                sils.append((cur, e))
            cur = None
    if cur is not None and dur > cur:  # trailing silence, no end line
        sils.append((cur, dur))
    return sils


def keep_segments(src, start, dur, thresh_db=-32.0, min_silence=0.35,
                  pad=0.06, min_keep=0.25):
    """Compute speech segments to keep (clip-relative), cutting long silences.

    `pad` leaves a little air around speech so words are never clipped.
    Returns [(a, b), ...] sorted, or a single [(0, dur)] when nothing is cut.
    """
    dur = max(0.0, float(dur))
    sils = detect_silences(src, start, dur, thresh_db, min_silence)
    # Turn silences into cut regions, shrunk by pad on each side.
    cuts = []
    for (a, b) in sils:
        ca, cb = a + pad, b - pad
        if cb - ca >= max(0.05, min_silence - 2 * pad):
            cuts.append((ca, cb))
    if not cuts:
        return [(0.0, dur)]
    # Keep = complement of cut regions within [0, dur].
    keep = []
    prev = 0.0
    for (ca, cb) in cuts:
        if ca > prev:
            keep.append((prev, ca))
        prev = max(prev, cb)
    if prev < dur:
        keep.append((prev, dur))
    # Drop tiny slivers; merge adjacent keeps separated by a dropped sliver.
    keep = [(a, b) for (a, b) in keep if b - a >= min_keep]
    if not keep:
        return [(0.0, dur)]
    return keep


def total_kept(keep):
    return sum(max(0.0, b - a) for (a, b) in keep)


def is_trimmed(keep, dur, min_cut=0.30):
    """True only if the keep-map removes a meaningful amount of time."""
    return (float(dur) - total_kept(keep)) >= min_cut and len(keep) >= 1


def make_remap(keep):
    """Return f(t) mapping untrimmed clip time -> trimmed timeline.

    A time inside a kept segment maps to its new position; a time inside a cut
    gap returns None. Times before/after everything clamp to the edges.
    """
    # Precompute cumulative kept duration before each segment.
    cum = []
    acc = 0.0
    for (a, b) in keep:
        cum.append(acc)
        acc += (b - a)
    total = acc

    def remap(t):
        for i, (a, b) in enumerate(keep):
            if a <= t <= b:               # inside a kept segment
                return cum[i] + (t - a)
            if t < a:                     # in a gap before segment i (incl leading)
                return None
        return None                       # after the last kept segment (trailing)

    return remap


def build_trim_filters(keep):
    """Return (video_prefix, audio_prefix) ffmpeg filterchains that trim the
    source to the keep-map and concat into [vtrim] / [atrim]."""
    n = len(keep)
    vparts, aparts = [], []
    vlabels, alabels = [], []
    for i, (a, b) in enumerate(keep):
        vparts.append("[0:v]trim=start=%.3f:end=%.3f,setpts=PTS-STARTPTS[tv%d]"
                      % (a, b, i))
        aparts.append("[0:a]atrim=start=%.3f:end=%.3f,asetpts=PTS-STARTPTS[ta%d]"
                      % (a, b, i))
        vlabels.append("[tv%d]" % i)
        alabels.append("[ta%d]" % i)
    vfc = ";".join(vparts) + ";" + "".join(vlabels) + "concat=n=%d:v=1:a=0[vtrim]" % n
    afc = ";".join(aparts) + ";" + "".join(alabels) + "concat=n=%d:v=0:a=1[atrim]" % n
    return vfc, afc
