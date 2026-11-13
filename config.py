"""ClipForge shared config. Import constants from here; never hardcode paths in modules."""
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv():
    """Load ROOT/.env (KEY=VALUE per line) into os.environ if not already set. Stdlib only."""
    path = os.path.join(ROOT, ".env")
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                os.environ.setdefault(k, v)
    except FileNotFoundError:
        pass


_load_dotenv()
DATA_DIR = os.path.join(ROOT, "data", "jobs")
VENV_PY = "/home/hakuza/venv/bin/python"

# WSL2 -> Windows host (DaVinci Resolve bridge)
WIN_HOST_IP = os.environ.get("CLIPFORGE_WIN_HOST", "172.20.16.1")
RESOLVE_BRIDGE_PORT = int(os.environ.get("CLIPFORGE_RESOLVE_PORT", "8765"))
# Windows-visible drop folder (WSL path to C:\Users\Public\ClipForge)
WIN_SHARE_DIR = os.environ.get("CLIPFORGE_WIN_SHARE", "/mnt/c/Users/Public/ClipForge")
# Optional shared secret for the Resolve bridge (resolve/bridge_server.py), which
# binds 0.0.0.0 so WSL can reach the Windows host. Set the same value in the
# bridge's environment to require it on every request. Empty = no auth (legacy
# behaviour), matching the bridge's own default.
RESOLVE_BRIDGE_TOKEN = os.environ.get("CLIPFORGE_BRIDGE_TOKEN", "")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLIPFORGE_MODEL", "claude-opus-4-8")
# LLM backend: "cli" = local Claude Code CLI on your subscription (no API credits),
# "api" = anthropic SDK with ANTHROPIC_API_KEY (pay-per-token).
LLM_BACKEND = os.environ.get("CLIPFORGE_LLM", "cli")

_FONTS = os.path.join(ROOT, "assets", "fonts")
_DEVA = "NotoSansDevanagari-Bold.ttf"
_DEVA_URL = ("https://raw.githubusercontent.com/notofonts/notofonts.github.io/main/"
             "fonts/NotoSansDevanagari/googlefonts/ttf/NotoSansDevanagari-Bold.ttf")


def _download_atomic(url, dest, min_size=50_000, timeout=60):
    """Download url -> dest atomically: fetch to dest+'.part', verify size, then
    os.replace. A dropped/short connection never leaves a truncated file at dest
    (so it will be retried next call). Returns True on success."""
    if os.path.exists(dest) and os.path.getsize(dest) >= min_size:
        return True
    import urllib.request
    tmp = dest + ".part"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "clipforge"})
        with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
            f.write(r.read())
        if os.path.getsize(tmp) < min_size:
            os.remove(tmp)
            return False
        os.replace(tmp, dest)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def fonts_dir():
    """Return dir holding a Devanagari-capable bold font; download once if missing."""
    os.makedirs(_FONTS, exist_ok=True)
    dest = os.path.join(_FONTS, _DEVA)
    _download_atomic(_DEVA_URL, dest, min_size=80_000)
    return _FONTS


_EMOJI = "NotoEmoji-Regular.ttf"
_EMOJI_URL = ("https://github.com/googlefonts/noto-emoji/raw/main/fonts/"
              "NotoEmoji-Regular.ttf")


def ensure_emoji_font():
    """Ensure a monochrome emoji font sits in fonts_dir so libass can fall back
    to it for emoji glyphs in burned captions. Downloads once; no-op if present
    or offline (captions still render, emoji may show as tofu)."""
    d = fonts_dir()
    dest = os.path.join(d, _EMOJI)
    _download_atomic(_EMOJI_URL, dest, min_size=50_000)
    return dest


_ASSETS = os.path.join(ROOT, "assets")
_LUTS = os.path.join(_ASSETS, "luts")
_MUSIC = os.path.join(_ASSETS, "music")
_BROLL = os.path.join(_ASSETS, "broll")


def luts_dir():
    """Dir holding .cube color LUTs (free cinematic grades)."""
    os.makedirs(_LUTS, exist_ok=True)
    return _LUTS


def music_dir():
    """Dir holding royalty-free music beds the user drops in (YouTube Audio
    Library / Pixabay / Freesound). Nothing is bundled — copyright-safe by default."""
    os.makedirs(_MUSIC, exist_ok=True)
    return _MUSIC


def list_luts():
    """Available LUT names (without the .cube extension), sorted.
    Auto-generates the free curated grade pack on first call."""
    d = luts_dir()
    try:
        import sys as _sys
        _r = os.path.dirname(os.path.abspath(__file__))
        if _r not in _sys.path:
            _sys.path.insert(0, _r)
        from pipeline import lutgen
        lutgen.ensure_pack(d)
    except Exception:
        pass
    try:
        return sorted(f[:-5] for f in os.listdir(d) if f.lower().endswith(".cube"))
    except Exception:
        return []


def list_music():
    """Available music-bed filenames (as dropped in assets/music/), sorted."""
    exts = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")
    try:
        return sorted(f for f in os.listdir(music_dir()) if f.lower().endswith(exts))
    except Exception:
        return []


def broll_dir():
    """Dir holding b-roll clips/images the user drops in (assets/broll/).
    Used to overlay cutaways over a clip window. Nothing is bundled."""
    os.makedirs(_BROLL, exist_ok=True)
    return _BROLL


def list_broll():
    """Available b-roll filenames (video or image), sorted."""
    exts = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".jpg", ".jpeg", ".png", ".gif")
    try:
        return sorted(f for f in os.listdir(broll_dir()) if f.lower().endswith(exts))
    except Exception:
        return []


def job_dir(job_id: str) -> str:
    # Guard against path traversal: job_id is used as a directory name, never a
    # path. Reject separators, '..', and anything outside a safe charset.
    jid = str(job_id)
    if ".." in jid or "/" in jid or "\\" in jid or not re.match(r"^[A-Za-z0-9._-]+$", jid):
        raise ValueError("invalid job_id")
    return os.path.join(DATA_DIR, jid)


def win_path(wsl_path: str) -> str:
    """Translate /mnt/c/... -> C:\\...  for paths handed to the Windows bridge."""
    p = os.path.abspath(wsl_path)
    if p.startswith("/mnt/") and len(p) > 6 and p[6] == "/":
        drive = p[5].upper()
        return drive + ":" + p[6:].replace("/", "\\")
    return p
