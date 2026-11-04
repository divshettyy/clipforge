# ClipForge → DaVinci Resolve bridge

ClipForge runs in **WSL**; DaVinci Resolve runs on the **Windows** host. The two
pieces here connect them:

| File | Runs on | Role |
|------|---------|------|
| `bridge_server.py` | **Windows** (Resolve's Python) | HTTP server that drives the Resolve scripting API |
| `push_client.py`   | **WSL** (ClipForge app) | copies clips to a shared folder and calls the bridge |

The WSL side copies rendered clips into `C:\Users\Public\ClipForge`
(`/mnt/c/Users/Public/ClipForge` from WSL), translates each to a Windows path,
and POSTs them to the bridge. The bridge imports them into a project named
**ClipForge** and appends them to a 1080×1920 timeline.

---

## 1. Install the bridge on Windows

`bridge_server.py` uses **only the Python standard library**, so run it with the
Python that ships inside DaVinci Resolve (or any Python 3 — but Resolve's bundled
interpreter is simplest because it already knows where the scripting lib is).

Copy `bridge_server.py` to the Windows host, e.g. `C:\Users\Public\ClipForge\bridge_server.py`.

### 1a. Enable external scripting in Resolve

Open DaVinci Resolve →
**Preferences → System → General → External scripting using:** set to **Local**.

Keep Resolve **open** (a project loaded or on the project manager) the whole time
the bridge runs — the scripting API only works while Resolve is running.

### 1b. Set the scripting environment variables (Windows)

The standard Resolve scripting paths live under
`%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting`.

**PowerShell** (current session):

```powershell
$env:RESOLVE_SCRIPT_API = "C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting"
$env:RESOLVE_SCRIPT_LIB = "C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"
$env:PYTHONPATH = "$env:RESOLVE_SCRIPT_API\Modules;$env:PYTHONPATH"
```

**cmd.exe** (current session):

```cmd
set RESOLVE_SCRIPT_API=C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting
set RESOLVE_SCRIPT_LIB=C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll
set PYTHONPATH=%RESOLVE_SCRIPT_API%\Modules;%PYTHONPATH%
```

To make them permanent, set them under **System Properties → Environment
Variables** (or `setx`), then open a fresh terminal.

`bridge_server.py` imports the API as `import DaVinciResolveScript`; if that fails
it adds `%RESOLVE_SCRIPT_API%\Modules` to `sys.path` and, as a last resort, loads
the native lib directly from `RESOLVE_SCRIPT_LIB`. Setting all three variables
above covers every path.

### 1c. Run the bridge

```cmd
python bridge_server.py
```

It prints `ClipForge Resolve bridge listening on 0.0.0.0:8765` and binds
`0.0.0.0:8765` so WSL can reach the Windows host.

### 1d. Confirm it works (on Windows)

```cmd
curl http://localhost:8765/health
```

Expected: `{"resolve": true}` when Resolve is open and scripting is enabled.
`{"resolve": false}` means the bridge is up but can't talk to Resolve — recheck
step 1a/1b and that Resolve is running.

### 1e. Windows Firewall

The first run may raise a Windows Defender Firewall prompt — **allow** Python on
private networks so WSL can connect. If no prompt appeared and WSL can't reach the
bridge, add an inbound rule allowing TCP **8765**.

---

## 2. WSL side (`push_client.py`)

This is part of the ClipForge app and needs no setup — the FastAPI server calls it.
It uses only `urllib` from the stdlib (no `requests`).

- `resolve_health() -> bool` — GETs `/health` with a ~2 s timeout; returns `True`
  only when the bridge reports Resolve connected. Never raises, never hangs.
- `push_to_resolve(job_dir, slugs=None) -> dict` — selects the rendered clips
  (all, or those whose `slug` is in `slugs`), copies each into
  `config.WIN_SHARE_DIR`, translates the paths with `config.win_path(...)`, and
  POSTs them to the bridge. Returns the bridge's parsed JSON
  (`{"ok": true, "added": N}`), or `{"ok": false, "error": ...}` on a local error.

From WSL the bridge is reached at **`172.20.16.1:8765`** (the Windows host IP from
`config.WIN_HOST_IP` / `config.RESOLVE_BRIDGE_PORT`). Clips are dropped in
**`C:\Users\Public\ClipForge`** (`/mnt/c/Users/Public/ClipForge`), which must be
visible to both WSL and the Windows host.

Quick manual test from WSL:

```bash
cd /home/hakuza/clipforge
/home/hakuza/venv/bin/python -c \
  "from resolve.push_client import resolve_health; print(resolve_health())"

# push a specific job's clips (bridge must be running on Windows):
/home/hakuza/venv/bin/python resolve/push_client.py \
  /home/hakuza/clipforge/data/jobs/demo-igl-ep8
```

### If WSL can't reach the host

- `172.20.16.1` is the default WSL2 host IP; confirm with `ip route show | grep default`
  in WSL (the gateway is the Windows host). Override via the `CLIPFORGE_WIN_HOST`
  env var if different.
- Make sure the bridge is actually listening (`curl http://localhost:8765/health`
  on Windows) and the firewall allows inbound 8765 (step 1e).

---

## Endpoint reference

| Method | Path | Body | Response |
|--------|------|------|----------|
| `GET`  | `/health` | — | `{"resolve": <bool>}` |
| `POST` | `/import` | `{"timeline":"ClipForge","paths":["C:\\Users\\Public\\ClipForge\\01_x.mp4", ...]}` | `{"ok": true, "added": N}` or `{"ok": false, "error": "..."}` |

The bridge gets/creates the **ClipForge** project, forces timeline resolution to
**1080×1920**, imports the media into the media pool, creates the **ClipForge**
timeline if it doesn't exist, and appends the imported clips to it.
