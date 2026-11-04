"""ClipForge — DaVinci Resolve bridge server (WINDOWS side).

Runs on the Windows host under DaVinci Resolve's bundled Python. Exposes a tiny
HTTP API that the WSL app (resolve/push_client.py) calls to import rendered clips
into a Resolve timeline.

Stdlib only (http.server) — it must run under Resolve's Python with no pip deps.

Endpoints
  GET  /health   -> {"resolve": <bool, Resolve reachable+connected>}
  POST /import    {"timeline":"ClipForge","paths":["C:\\...\\01_x.mp4", ...]}
                 -> {"ok": true, "added": N}  on success
                 -> {"ok": false, "error": "..."}  on any handled failure

Listens on 0.0.0.0:8765 so WSL (172.20.16.x) can reach the Windows host.

See README.md for the required Windows environment variables and launch steps.
"""
import os
import sys
import json
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "0.0.0.0"
PORT = int(os.environ.get("CLIPFORGE_RESOLVE_PORT", "8765"))
PROJECT_NAME = "ClipForge"
DEFAULT_TIMELINE = "ClipForge"
RES_W, RES_H = "1080", "1920"


# ---------------------------------------------------------------------------
# DaVinci Resolve scripting API bootstrap
# ---------------------------------------------------------------------------
def _import_resolve_module():
    """Return the DaVinciResolveScript module, trying several load strategies.

    1. plain `import DaVinciResolveScript`
    2. add the standard Modules dir from RESOLVE_SCRIPT_API to sys.path, retry
    3. load the fusionscript native lib directly from RESOLVE_SCRIPT_LIB
    Raises ImportError if none work.
    """
    try:
        import DaVinciResolveScript as dvr  # type: ignore
        return dvr
    except Exception:
        pass

    # Strategy 2: RESOLVE_SCRIPT_API/Modules holds DaVinciResolveScript.py
    api = os.environ.get("RESOLVE_SCRIPT_API", "")
    if api:
        mod_dir = os.path.join(api, "Modules")
        if os.path.isdir(mod_dir) and mod_dir not in sys.path:
            sys.path.append(mod_dir)
        try:
            import DaVinciResolveScript as dvr  # type: ignore
            return dvr
        except Exception:
            pass

    # Strategy 3: load the native fusionscript lib pointed to by RESOLVE_SCRIPT_LIB
    lib = os.environ.get("RESOLVE_SCRIPT_LIB", "")
    if lib and os.path.exists(lib):
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("fusionscript", lib)
            if spec and spec.loader:
                fusionscript = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(fusionscript)  # type: ignore
                return fusionscript
        except Exception:
            pass
        # older Pythons / native module: imp.load_dynamic as a last resort
        try:
            import imp  # type: ignore
            return imp.load_dynamic("fusionscript", lib)
        except Exception:
            pass

    raise ImportError(
        "Could not import DaVinciResolveScript. Set RESOLVE_SCRIPT_API / "
        "RESOLVE_SCRIPT_LIB / PYTHONPATH (see README.md)."
    )


def get_resolve():
    """Return a connected Resolve app object, or None if unavailable."""
    try:
        dvr = _import_resolve_module()
    except Exception:
        return None
    try:
        return dvr.scriptapp("Resolve")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Resolve operations
# ---------------------------------------------------------------------------
def _get_or_create_project(resolve):
    """Return (project, error). Load the ClipForge project if it exists, else
    create it."""
    pm = resolve.GetProjectManager()
    if pm is None:
        return None, "GetProjectManager() returned None"

    # Is the ClipForge project already current?
    current = pm.GetCurrentProject()
    if current is not None and current.GetName() == PROJECT_NAME:
        return current, None

    # Try to load an existing project by name.
    try:
        names = pm.GetProjectListInCurrentFolder() or []
    except Exception:
        names = []
    if PROJECT_NAME in names:
        proj = pm.LoadProject(PROJECT_NAME)
        if proj is not None:
            return proj, None

    # Create a fresh one.
    proj = pm.CreateProject(PROJECT_NAME)
    if proj is None:
        # CreateProject fails if a project of that name already exists but wasn't
        # in the list we saw — fall back to loading it.
        proj = pm.LoadProject(PROJECT_NAME)
    if proj is None:
        return None, "could not load or create project '%s'" % PROJECT_NAME
    return proj, None


def _set_vertical_resolution(project):
    """Set the project's timeline resolution to 1080x1920 (best effort)."""
    try:
        project.SetSetting("timelineResolutionWidth", RES_W)
        project.SetSetting("timelineResolutionHeight", RES_H)
    except Exception:
        pass


def _find_timeline(project, name):
    try:
        count = project.GetTimelineCount()
    except Exception:
        return None
    try:
        count = int(count)
    except Exception:
        count = 0
    for i in range(1, count + 1):
        try:
            tl = project.GetTimelineByIndex(i)
        except Exception:
            tl = None
        if tl is not None:
            try:
                if tl.GetName() == name:
                    return tl
            except Exception:
                continue
    return None


def do_import(paths, timeline_name):
    """Import the given Windows file paths into the ClipForge project/timeline.

    Returns a dict: {"ok": True, "added": N} or {"ok": False, "error": ...}.
    """
    if not isinstance(paths, list) or not paths:
        return {"ok": False, "error": "no paths provided"}

    # Keep only paths that exist on this (Windows) host.
    existing = [p for p in paths if isinstance(p, str) and os.path.exists(p)]
    missing = [p for p in paths if p not in existing]
    if not existing:
        return {"ok": False,
                "error": "none of the given paths exist on the Windows host: %s"
                         % ", ".join(str(p) for p in paths)}

    resolve = get_resolve()
    if resolve is None:
        return {"ok": False, "error": "DaVinci Resolve is not reachable "
                                      "(is Resolve open with external scripting enabled?)"}

    project, err = _get_or_create_project(resolve)
    if project is None:
        return {"ok": False, "error": err}

    _set_vertical_resolution(project)

    media_pool = project.GetMediaPool()
    if media_pool is None:
        return {"ok": False, "error": "GetMediaPool() returned None"}

    # --- import media into the media pool ---------------------------------
    try:
        items = media_pool.ImportMedia(existing)
    except Exception as exc:
        return {"ok": False, "error": "ImportMedia failed: %s" % exc}
    if not items:
        # Fall back to MediaStorage.AddItemListToMediaPool if ImportMedia gave nothing.
        try:
            storage = resolve.GetMediaStorage()
            items = storage.AddItemListToMediaPool(existing) if storage else None
        except Exception as exc:
            return {"ok": False, "error": "AddItemListToMediaPool failed: %s" % exc}
    if not items:
        return {"ok": False, "error": "no media items were imported"}

    # --- ensure the timeline exists and is current ------------------------
    tl_name = timeline_name or DEFAULT_TIMELINE
    timeline = _find_timeline(project, tl_name)
    if timeline is None:
        try:
            timeline = media_pool.CreateEmptyTimeline(tl_name)
        except Exception as exc:
            return {"ok": False, "error": "CreateEmptyTimeline failed: %s" % exc}
        if timeline is None:
            return {"ok": False, "error": "could not create timeline '%s'" % tl_name}

    try:
        project.SetCurrentTimeline(timeline)
    except Exception:
        pass

    # --- append the imported clips to the timeline ------------------------
    try:
        appended = media_pool.AppendToTimeline(items)
    except Exception as exc:
        return {"ok": False, "error": "AppendToTimeline failed: %s" % exc}

    # AppendToTimeline returns a list of timeline items (or None/empty on failure).
    if appended:
        try:
            added = len(appended)
        except Exception:
            added = len(items)
    else:
        added = len(items)

    result = {"ok": True, "added": added}
    if missing:
        result["skipped_missing"] = missing
    return result


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "ClipForgeResolveBridge/1.0"

    def _send_json(self, code, obj):
        try:
            body = json.dumps(obj).encode("utf-8")
        except Exception:
            body = b'{"ok": false, "error": "could not serialize response"}'
            code = 500
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def log_message(self, fmt, *args):
        # Compact logging to stderr.
        sys.stderr.write("[bridge] %s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/health":
            try:
                resolve = get_resolve()
                connected = False
                if resolve is not None:
                    try:
                        connected = resolve.GetProjectManager() is not None
                    except Exception:
                        connected = False
                self._send_json(200, {"resolve": bool(connected)})
            except Exception:
                self._send_json(200, {"resolve": False})
        else:
            self._send_json(404, {"ok": False, "error": "not found: %s" % self.path})

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/import":
            self._send_json(404, {"ok": False, "error": "not found: %s" % self.path})
            return

        # Read the body.
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except (TypeError, ValueError):
            length = 0
        try:
            raw = self.rfile.read(length) if length > 0 else b""
        except Exception as exc:
            self._send_json(400, {"ok": False, "error": "could not read body: %s" % exc})
            return

        # Parse JSON.
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception as exc:
            self._send_json(400, {"ok": False, "error": "invalid JSON: %s" % exc})
            return
        if not isinstance(data, dict):
            self._send_json(400, {"ok": False, "error": "body must be a JSON object"})
            return

        paths = data.get("paths")
        timeline = data.get("timeline", DEFAULT_TIMELINE)

        # Do the import — never let an exception crash the server.
        try:
            result = do_import(paths, timeline)
        except Exception as exc:
            sys.stderr.write(traceback.format_exc())
            self._send_json(200, {"ok": False, "error": "unhandled: %s" % exc})
            return

        self._send_json(200, result)


def main():
    httpd = ThreadingHTTPServer((HOST, PORT), BridgeHandler)
    sys.stderr.write("ClipForge Resolve bridge listening on %s:%d\n" % (HOST, PORT))
    sys.stderr.write("  GET  /health\n  POST /import\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nshutting down\n")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
