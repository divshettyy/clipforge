"""Request validation helpers for the job API (pure functions, no deps)."""
from urllib.parse import urlparse

MAX_URL_LEN = 2048
MIN_CLIPS, MAX_CLIPS, DEFAULT_CLIPS = 1, 20, 6
MAX_GUIDANCE_LEN = 2000


def clean_url(url):
    """Return a stripped http(s) URL, or raise ValueError with a user message.

    Rejects non-strings and non-http(s) schemes (file://, ftp://, bare paths)
    so yt-dlp can never be pointed at local files.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("'url' must be a non-empty string")
    url = url.strip()
    if len(url) > MAX_URL_LEN:
        raise ValueError("'url' is too long")
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError("'url' must be an http(s) link")
    return url


def clamp_clips(n):
    """Coerce the requested clip count into [MIN_CLIPS, MAX_CLIPS]."""
    try:
        n = int(n or DEFAULT_CLIPS)
    except (TypeError, ValueError):
        n = DEFAULT_CLIPS
    return max(MIN_CLIPS, min(MAX_CLIPS, n))


def clean_guidance(g):
    return g.strip()[:MAX_GUIDANCE_LEN] if isinstance(g, str) else ""
