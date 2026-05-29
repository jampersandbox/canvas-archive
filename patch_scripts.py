#!/usr/bin/env python3
"""
patch_scripts.py
================
Adds multi-school support to all downloader scripts.
Run automatically by setup_mac.sh / setup_windows.bat.
"""
from pathlib import Path

HERE = Path(__file__).parent

# Each entry: filename → (text to find, text to replace with)
PATCHES = [
    (
        "canvas_auth.py",
        'CANVAS_BASE_URL = "https://canvas.harvard.edu"',
        '''try:
    from canvas_config import CANVAS_BASE_URL as _canvas_url
    CANVAS_BASE_URL = _canvas_url
except ImportError:
    CANVAS_BASE_URL = "https://canvas.harvard.edu"''',
    ),
    (
        "canvas_downloader.py",
        'CANVAS_BASE_URL = "https://canvas.harvard.edu"',
        '''try:
    from canvas_config import CANVAS_BASE_URL as _canvas_url
    CANVAS_BASE_URL = _canvas_url
except ImportError:
    CANVAS_BASE_URL = "https://canvas.harvard.edu"''',
    ),
    (
        "external_downloader.py",
        'CANVAS_BASE_URL = "https://canvas.harvard.edu"',
        '''try:
    from canvas_config import CANVAS_BASE_URL as _canvas_url
    CANVAS_BASE_URL = _canvas_url
except ImportError:
    CANVAS_BASE_URL = "https://canvas.harvard.edu"''',
    ),
    (
        "panopto_downloader.py",
        'CANVAS_BASE_URL  = "https://canvas.harvard.edu"\n'
        'PANOPTO_BASE_URL = "https://harvard.hosted.panopto.com"',
        '''try:
    from canvas_config import CANVAS_BASE_URL as _cu, PANOPTO_BASE_URL as _pu
    CANVAS_BASE_URL  = _cu
    PANOPTO_BASE_URL = _pu
except ImportError:
    CANVAS_BASE_URL  = "https://canvas.harvard.edu"
    PANOPTO_BASE_URL = "https://harvard.hosted.panopto.com"''',
    ),
    (
        "reserves_downloader.py",
        'CANVAS_BASE_URL  = "https://canvas.harvard.edu"',
        '''try:
    from canvas_config import CANVAS_BASE_URL as _canvas_url
    CANVAS_BASE_URL = _canvas_url
except ImportError:
    CANVAS_BASE_URL  = "https://canvas.harvard.edu"''',
    ),
]

patched = 0
for filename, old, new in PATCHES:
    path = HERE / filename
    if not path.exists():
        print(f"  ⚠  {filename} not found — skipping")
        continue
    content = path.read_text(encoding="utf-8")
    if old in content:
        path.write_text(content.replace(old, new, 1), encoding="utf-8")
        print(f"  ✓  {filename}")
        patched += 1
    else:
        print(f"  –  {filename}  (already patched or URL not found)")

print(f"\n  {patched} file(s) updated.")