"""ClipForge: generate free .cube color-grade LUTs programmatically.

No assets to download and nothing proprietary — each grade is a small transfer
function (per-channel lift/gamma/gain + contrast + saturation) baked into a 3D
LUT. `ensure_pack(dir)` writes any missing curated grades into assets/luts/,
so they appear in the Studio LUT dropdown automatically.
"""
import os

_SIZE = 17  # 17^3 grid: good quality, tiny file


def _clamp(x):
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _grade(r, g, b, p):
    """Apply one grade's transfer to a normalised RGB triplet."""
    # Per-channel lift / gamma / gain (colour balance).
    out = []
    for c, (lift, gamma, gain) in zip((r, g, b), p["channels"]):
        c = c ** (1.0 / gamma) if gamma > 0 else c
        c = c * gain + lift
        out.append(c)
    r2, g2, b2 = out
    # Contrast around 0.5 pivot.
    k = p.get("contrast", 1.0)
    r2 = (r2 - 0.5) * k + 0.5
    g2 = (g2 - 0.5) * k + 0.5
    b2 = (b2 - 0.5) * k + 0.5
    # Saturation (mix toward luma).
    s = p.get("sat", 1.0)
    luma = 0.2126 * r2 + 0.7152 * g2 + 0.0722 * b2
    r2 = luma + (r2 - luma) * s
    g2 = luma + (g2 - luma) * s
    b2 = luma + (b2 - luma) * s
    return _clamp(r2), _clamp(g2), _clamp(b2)


# Curated grades. channels = per (R,G,B): (lift, gamma, gain).
GRADES = {
    "golden-hour": {"channels": [(0.03, 1.05, 1.08), (0.0, 1.0, 1.0), (-0.02, 0.95, 0.92)],
                    "contrast": 1.05, "sat": 1.12},
    "moody-blue": {"channels": [(-0.02, 0.95, 0.95), (0.0, 1.0, 1.0), (0.04, 1.08, 1.06)],
                   "contrast": 1.08, "sat": 0.92},
    "vibrant-pop": {"channels": [(0.0, 1.0, 1.05), (0.0, 1.0, 1.03), (0.0, 1.0, 1.02)],
                    "contrast": 1.12, "sat": 1.35},
    "vintage-fade": {"channels": [(0.06, 1.1, 0.92), (0.04, 1.05, 0.94), (0.05, 1.0, 0.9)],
                     "contrast": 0.9, "sat": 0.8},
    "cyberpunk": {"channels": [(0.02, 1.0, 1.06), (-0.01, 1.0, 0.96), (0.05, 1.05, 1.1)],
                  "contrast": 1.15, "sat": 1.3},
    "noir": {"channels": [(0.0, 1.02, 1.0), (0.0, 1.02, 1.0), (0.0, 1.02, 1.0)],
             "contrast": 1.2, "sat": 0.0},
    "clean-bright": {"channels": [(0.02, 1.08, 1.04), (0.02, 1.08, 1.04), (0.02, 1.08, 1.04)],
                     "contrast": 1.02, "sat": 1.08},
    "sunset": {"channels": [(0.05, 1.0, 1.1), (0.0, 1.0, 0.98), (-0.03, 0.98, 0.9)],
               "contrast": 1.06, "sat": 1.2},
    "film-green": {"channels": [(-0.01, 1.0, 0.97), (0.03, 1.04, 1.02), (-0.01, 1.0, 0.96)],
                   "contrast": 1.04, "sat": 0.95},
}


def write_cube(path, title, params, size=_SIZE):
    """Write one .cube LUT for a grade's params."""
    lines = ['TITLE "%s"' % title, "LUT_3D_SIZE %d" % size, ""]
    denom = size - 1
    # .cube: red index varies fastest, then green, then blue.
    for bi in range(size):
        for gi in range(size):
            for ri in range(size):
                r, g, b = _grade(ri / denom, gi / denom, bi / denom, params)
                lines.append("%.6f %.6f %.6f" % (r, g, b))
    # Unique temp so two concurrent ensure_pack() calls can't clobber each
    # other's in-progress write before the atomic os.replace.
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".cube.part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
    return path


def ensure_pack(luts_dir):
    """Generate any missing curated grades into luts_dir. Idempotent/cheap."""
    made = []
    for name, params in GRADES.items():
        dest = os.path.join(luts_dir, name + ".cube")
        if not os.path.isfile(dest):
            try:
                write_cube(dest, name, params)
                made.append(name)
            except Exception:
                pass
    return made


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else "assets/luts"
    os.makedirs(d, exist_ok=True)
    print("generated:", ensure_pack(d))
