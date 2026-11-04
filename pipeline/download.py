"""ClipForge pipeline: download a YouTube video to <job_dir>/source.mp4 via yt-dlp."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: F401  (kept for parity / future use)


def download(url: str, job_dir: str) -> dict:
    """Download `url` to <job_dir>/source.mp4 (<=720p, merged mp4).

    Returns {"title", "duration", "path"}.
    """
    os.makedirs(job_dir, exist_ok=True)
    out_path = os.path.join(job_dir, "source.mp4")

    try:
        import yt_dlp
    except Exception as e:  # pragma: no cover - dep is installed
        raise RuntimeError("yt-dlp is not importable: %s" % e)

    ydl_opts = {
        "format": "bestvideo[height<=720]+bestaudio/best[height<=720]",
        "merge_output_format": "mp4",
        # yt-dlp fills in the extension; force mp4 container regardless of source.
        "outtmpl": os.path.join(job_dir, "source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "overwrites": True,
        "postprocessors": [
            {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
        ],
    }

    info = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:
        raise RuntimeError("yt-dlp download failed for %s: %s" % (url, e))

    # Normalise the produced file to exactly source.mp4.
    if not os.path.exists(out_path):
        # The merge/remux may have produced another extension; find it.
        candidates = [
            os.path.join(job_dir, f)
            for f in os.listdir(job_dir)
            if f.startswith("source.") and not f.endswith(".part")
        ]
        if candidates:
            # Prefer a real media file; take the largest.
            candidates.sort(key=lambda p: os.path.getsize(p), reverse=True)
            if candidates[0] != out_path:
                os.replace(candidates[0], out_path)
        if not os.path.exists(out_path):
            raise RuntimeError("download produced no source.mp4 in %s" % job_dir)

    title = (info or {}).get("title") or "Untitled"
    duration = (info or {}).get("duration")
    try:
        duration = float(duration) if duration is not None else 0.0
    except (TypeError, ValueError):
        duration = 0.0

    return {"title": title, "duration": duration, "path": out_path}
