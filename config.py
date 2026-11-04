"""ClipForge shared config. Import constants from here; never hardcode paths in modules."""
import os

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

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLIPFORGE_MODEL", "claude-opus-4-8")
# LLM backend: "cli" = local Claude Code CLI on your subscription (no API credits),
# "api" = anthropic SDK with ANTHROPIC_API_KEY (pay-per-token).
LLM_BACKEND = os.environ.get("CLIPFORGE_LLM", "cli")

_FONTS = os.path.join(ROOT, "assets", "fonts")
_DEVA = "NotoSansDevanagari-Bold.ttf"
_DEVA_URL = ("https://raw.githubusercontent.com/notofonts/notofonts.github.io/main/"
             "fonts/NotoSansDevanagari/googlefonts/ttf/NotoSansDevanagari-Bold.ttf")


def fonts_dir():
    """Return dir holding a Devanagari-capable bold font; download once if missing."""
    os.makedirs(_FONTS, exist_ok=True)
    dest = os.path.join(_FONTS, _DEVA)
    if not os.path.exists(dest):
        import urllib.request
        try:
            urllib.request.urlretrieve(_DEVA_URL, dest)
        except Exception:
            pass
    return _FONTS


_ASSETS = os.path.join(ROOT, "assets")
_LUTS = os.path.join(_ASSETS, "luts")
_MUSIC = os.path.join(_ASSETS, "music")


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
    """Available LUT names (without the .cube extension), sorted."""
    try:
        return sorted(f[:-5] for f in os.listdir(luts_dir()) if f.lower().endswith(".cube"))
    except Exception:
        return []


def list_music():
    """Available music-bed filenames (as dropped in assets/music/), sorted."""
    exts = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")
    try:
        return sorted(f for f in os.listdir(music_dir()) if f.lower().endswith(exts))
    except Exception:
        return []


def job_dir(job_id: str) -> str:
    return os.path.join(DATA_DIR, job_id)


def win_path(wsl_path: str) -> str:
    """Translate /mnt/c/... -> C:\\...  for paths handed to the Windows bridge."""
    p = os.path.abspath(wsl_path)
    if p.startswith("/mnt/") and len(p) > 6:
        drive = p[5].upper()
        return drive + ":" + p[6:].replace("/", "\\")
    return p
