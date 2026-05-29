#!/usr/bin/env python3
"""
patch_scripts.py
================
Adds multi-school support to downloader scripts.
Run automatically by setup_mac.sh / setup_windows.bat.

NOTE: canvas_auth.py is NOT patched here — it reads the Canvas URL
directly from canvas_config.json at runtime without any imports.
"""
from pathlib import Path

HERE = Path(__file__).parent

REPLACEMENT = '''\
try:
    from canvas_config import CANVAS_BASE_URL as _canvas_url
    CANVAS_BASE_URL = _canvas_url
except ImportError:
    CANVAS_BASE_URL = "https://canvas.harvard.edu"\
'''

# Only patch these three files — NOT canvas_auth.py
PATCHES = [
    (
        "canvas_downloader.py",
        'CANVAS_BASE_URL = "https://canvas.harvard.edu"',
        REPLACEMENT,
    ),
    (
        "external_downloader.py",
        'CANVAS_BASE_URL = "https://canvas.harvard.edu"',
        REPLACEMENT,
    ),
    (
        "panopto_downloader.py",
        'CANVAS_BASE_URL  = "https://canvas.harvard.edu"',
        REPLACEMENT.replace(
            'CANVAS_BASE_URL = _canvas_url',
            'CANVAS_BASE_URL = _canvas_url\n    PANOPTO_BASE_URL = _pu'
        ).replace(
            'except ImportError:\n    CANVAS_BASE_URL = "https://canvas.harvard.edu"',
            'except ImportError:\n    CANVAS_BASE_URL  = "https://canvas.harvard.edu"\n'
            '    PANOPTO_BASE_URL = "https://harvard.hosted.panopto.com"'
        ).replace(
            'from canvas_config import CANVAS_BASE_URL as _canvas_url',
            'from canvas_config import CANVAS_BASE_URL as _canvas_url, '
            'PANOPTO_BASE_URL as _pu'
        ),
    ),
    (
        "reserves_downloader.py",
        'CANVAS_BASE_URL  = "https://canvas.harvard.edu"',
        REPLACEMENT,
    ),
]

# Also update canvas_config.json so canvas_auth.py can read the URL
def write_canvas_config_json(canvas_url: str = "https://canvas.harvard.edu",
                              panopto_url: str = "https://harvard.hosted.panopto.com"):
    cfg = HERE / "canvas_config.json"
    if not cfg.exists():
        cfg.write_text(
            f'{{"canvas_url": "{canvas_url}", '
            f'"panopto_url": "{panopto_url}"}}\n',
            encoding="utf-8",
        )
        print("  ✓  canvas_config.json  (created)")


patched = 0
skipped = 0

for filename, old, new in PATCHES:
    path = HERE / filename
    if not path.exists():
        print(f"  ⚠  {filename} not found — skipping")
        continue

    content = path.read_text(encoding="utf-8")

    # Skip if already patched (avoid double-patching)
    if "from canvas_config import" in content:
        print(f"  –  {filename}  (already patched)")
        skipped += 1
        continue

    if old in content:
        path.write_text(content.replace(old, new, 1), encoding="utf-8")
        print(f"  ✓  {filename}")
        patched += 1
    else:
        print(f"  –  {filename}  (pattern not found — may be a different version)")
        skipped += 1

write_canvas_config_json()

print(f"\n  {patched} file(s) patched, {skipped} skipped.")