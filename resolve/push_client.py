"""ClipForge — WSL-side client that pushes rendered clips to the DaVinci Resolve
bridge running on the Windows host.

Runs inside the ClipForge app (WSL). Copies the selected rendered clip files into
the Windows-visible share folder (config.WIN_SHARE_DIR), translates each path to a
Windows path, and POSTs them to the bridge's /import endpoint.

Stdlib only (urllib.request) — no `requests` dependency.
"""
import os
import sys
import json
import shutil
import urllib.request
import urllib.error

# Make `from config import ...` work whether CWD is the project root or /home/hakuza.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402

TIMELINE_NAME = "ClipForge"
_HEALTH_TIMEOUT = 2.0      # seconds — resolve_health() must never hang
_IMPORT_TIMEOUT = 120.0    # importing media into Resolve can take a while


def _bridge_url(path: str) -> str:
    return "http://{host}:{port}{path}".format(
        host=config.WIN_HOST_IP, port=config.RESOLVE_BRIDGE_PORT, path=path
    )


def _auth_headers() -> dict:
    """X-ClipForge-Token header if CLIPFORGE_BRIDGE_TOKEN is configured, else {}."""
    token = getattr(config, "RESOLVE_BRIDGE_TOKEN", "")
    return {"X-ClipForge-Token": token} if token else {}


def resolve_health() -> bool:
    """GET /health on the bridge. Return True only if the bridge answers and
    reports Resolve connected. Never raises; returns False fast on any problem."""
    try:
        req = urllib.request.Request(
            _bridge_url("/health"), method="GET", headers=_auth_headers()
        )
        with urllib.request.urlopen(req, timeout=_HEALTH_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
        data = json.loads(body)
        return bool(data.get("resolve", False))
    except Exception:
        return False


def push_to_resolve(job_dir: str, slugs=None) -> dict:
    """Copy selected rendered clips to the Windows share and ask the bridge to
    import them into the ClipForge timeline.

    Args:
        job_dir: absolute path to a job directory.
        slugs:   optional list of slugs to push; None/empty = all rendered clips.

    Returns the bridge's parsed JSON response, or a {"ok": False, "error": ...}
    dict describing a local failure (so callers always get a dict).
    """
    job_dir = os.path.abspath(job_dir)
    clips_path = os.path.join(job_dir, "clips.json")

    # --- load clips.json ---------------------------------------------------
    try:
        with open(clips_path, "r", encoding="utf-8") as fh:
            clips = json.load(fh)
    except FileNotFoundError:
        return {"ok": False, "error": "clips.json not found in %s" % job_dir}
    except Exception as exc:
        return {"ok": False, "error": "failed to read clips.json: %s" % exc}

    if not isinstance(clips, list):
        return {"ok": False, "error": "clips.json is not a list"}

    want = set(slugs) if slugs else None

    # --- select rendered clips --------------------------------------------
    selected = []
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        if clip.get("status") != "rendered":
            continue
        rel = clip.get("file")
        if not rel:
            continue
        if want is not None and clip.get("slug") not in want:
            continue
        selected.append(clip)

    if not selected:
        if want is not None:
            return {"ok": False, "error": "no rendered clips match the given slugs"}
        return {"ok": False, "error": "no rendered clips to push"}

    # --- ensure the Windows share folder exists ---------------------------
    share = config.WIN_SHARE_DIR
    try:
        os.makedirs(share, exist_ok=True)
    except Exception as exc:
        return {"ok": False, "error": "cannot create share dir %s: %s" % (share, exc)}

    # --- copy each clip into the share, collect Windows paths -------------
    win_paths = []
    copied = []
    for clip in selected:
        src = os.path.join(job_dir, clip["file"])
        if not os.path.isfile(src):
            return {"ok": False,
                    "error": "clip file missing: %s (slug=%s)" % (src, clip.get("slug"))}
        dest = os.path.join(share, os.path.basename(src))
        try:
            shutil.copy2(src, dest)
        except Exception as exc:
            return {"ok": False, "error": "copy failed for %s: %s" % (src, exc)}
        copied.append(dest)
        win_paths.append(config.win_path(dest))

    # --- POST to the bridge ------------------------------------------------
    payload = {"timeline": TIMELINE_NAME, "paths": win_paths}
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    headers.update(_auth_headers())
    req = urllib.request.Request(
        _bridge_url("/import"),
        data=data,
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=_IMPORT_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        return {"ok": False,
                "error": "bridge HTTP %s: %s" % (exc.code, detail or exc.reason)}
    except urllib.error.URLError as exc:
        return {"ok": False,
                "error": "cannot reach bridge at %s: %s"
                         % (_bridge_url("/import"), exc.reason)}
    except Exception as exc:
        return {"ok": False, "error": "bridge request failed: %s" % exc}

    try:
        result = json.loads(body)
    except Exception:
        return {"ok": False, "error": "bridge returned non-JSON: %s" % body[:500]}

    # annotate with how many files we actually pushed (informational)
    if isinstance(result, dict):
        result.setdefault("pushed", len(win_paths))
    return result


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Push rendered clips to DaVinci Resolve bridge")
    ap.add_argument("job_dir", help="absolute path to a job directory")
    ap.add_argument("--slug", action="append", dest="slugs",
                    help="slug to push (repeatable); omit for all rendered clips")
    ap.add_argument("--health", action="store_true", help="just check bridge health")
    args = ap.parse_args()

    if args.health:
        print(json.dumps({"resolve": resolve_health()}))
    else:
        print(json.dumps(push_to_resolve(args.job_dir, slugs=args.slugs), indent=2))
