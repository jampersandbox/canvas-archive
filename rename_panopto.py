#!/usr/bin/env python3
"""
rename_panopto.py
=================
Renames Panopto recordings from generic IDs to real titles.
Opens a browser to authenticate with Panopto, then fetches
titles from the API using fresh session cookies.

Usage:
    python3 rename_panopto.py --dir ~/Desktop/canvas_downloads_noapp_530
    python3 rename_panopto.py --dry-run --dir ~/Desktop/canvas_downloads_noapp_530
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import requests

try:
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


try:
    from canvas_config import PANOPTO_BASE_URL as _pu
    PANOPTO_BASE_URL = _pu
except ImportError:
    PANOPTO_BASE_URL = "https://harvard.hosted.panopto.com"
BROWSER_PROFILE  = Path("./browser_profile")
COOKIES_FILE     = Path("./panopto_cookies.txt")
DEDUP_INDEX_FILE = Path("./panopto_dedup_index.json")

_GUID_RE = re.compile(
    r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", re.I
)
_LOGIN_RE = re.compile(
    r"login|signin|shibboleth|harvardkey|cas\.harvard|/auth/|/saml", re.I
)


def _sanitize(s: str, n: int = 160) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s).strip(". ")
    return s[:n] or "unnamed"


def _is_login(page) -> bool:
    try:
        return bool(_LOGIN_RE.search(page.url) or _LOGIN_RE.search(page.title()))
    except Exception:
        return False


def write_netscape_cookies(cookies: list[dict], filepath: Path) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for c in cookies:
            domain  = c.get("domain", "")
            flag    = "TRUE" if domain.startswith(".") else "FALSE"
            path    = c.get("path", "/")
            secure  = "TRUE" if c.get("secure", False) else "FALSE"
            expires = max(0, int(c.get("expires") or 0))
            f.write(
                f"{domain}\t{flag}\t{path}\t{secure}\t{expires}"
                f"\t{c.get('name','')}\t{c.get('value','')}\n"
            )


def get_fresh_cookies() -> list[dict]:
    """Open browser, authenticate with Panopto, return fresh cookies."""
    if not HAS_PLAYWRIGHT:
        print("  ✗  playwright not installed. Run: pip install playwright")
        return []

    print("  Opening browser to authenticate with Panopto…")
    BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)

    pw  = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(BROWSER_PROFILE),
        headless=False,
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )

    # Load Canvas cookies if available
    canvas_cookie_file = Path("./canvas_cookies.json")
    if canvas_cookie_file.exists():
        try:
            cookies = json.loads(canvas_cookie_file.read_text(encoding="utf-8"))
            if cookies:
                ctx.add_cookies(cookies)
                print(f"  Loaded {len(cookies)} Canvas cookie(s) into browser.")
        except Exception:
            pass

    page = ctx.new_page()

    try:
        page.goto(
            f"{PANOPTO_BASE_URL}/Panopto/Pages/Home.aspx",
            wait_until="domcontentloaded",
            timeout=20_000,
        )
        time.sleep(3)

        if _is_login(page):
            print()
            print("═" * 62)
            print("  🔐  Login required — Panopto / HarvardKey")
            print("      Sign in in the browser window,")
            print("      then come back here and press ENTER.")
            print("═" * 62)
            try:
                input("  [Press ENTER once signed in] ")
            except EOFError:
                time.sleep(5)
            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except PWTimeout:
                pass

        cookies = ctx.cookies()
        write_netscape_cookies(cookies, COOKIES_FILE)
        print(f"  ✅  Authenticated — {len(cookies)} cookie(s) saved.\n")
        return cookies

    finally:
        ctx.close()
        pw.stop()


def _panopto_session(browser_cookies: list[dict]) -> requests.Session:
    s = requests.Session()
    for c in browser_cookies:
        if "panopto" in c.get("domain", "").lower():
            try:
                # Don't pass domain/path — let requests handle matching
                # Passing the dot-prefixed domain causes 401s
                s.cookies.set(c["name"], c["value"])
            except Exception:
                pass
    return s


def get_session_title_via_browser(session_id: str, page) -> str | None:
    """Fetch the real title by visiting the Panopto viewer page in the browser."""
    try:
        url = f"{PANOPTO_BASE_URL}/Panopto/Pages/Viewer.aspx?id={session_id}"
        page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        time.sleep(2)
        
        # Try page title first — usually "Recording Title - Panopto"
        title = page.title()
        if title:
            # Strip " - Panopto" suffix if present
            title = re.sub(r"\s*[-|]\s*Panopto.*$", "", title, flags=re.I).strip()
            if title and len(title) > 2:
                return title

        # Try the heading element on the page
        for sel in [
            "h1.title",
            ".title-text",
            '[class*="title"]',
            "h1",
        ]:
            try:
                el = page.query_selector(sel)
                if el:
                    text = (el.inner_text() or "").strip()
                    if text and len(text) > 2:
                        return text
            except Exception:
                pass

    except Exception as exc:
        print(f"    ⚠  Browser error for {session_id[:8]}: {exc}")
    return None


def load_dedup_index() -> dict[str, str]:
    if DEDUP_INDEX_FILE.exists():
        try:
            return json.loads(DEDUP_INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_dedup_index(data: dict[str, str]) -> None:
    DEDUP_INDEX_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rename Panopto recordings from IDs to real titles."
    )
    parser.add_argument(
        "--dir", metavar="PATH",
        default="./canvas_downloads",
        help="Root download directory to search.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be renamed without actually doing it.",
    )
    args = parser.parse_args()

    root = Path(args.dir).expanduser().resolve()
    if not root.exists():
        print(f"  ✗  Directory not found: {root}")
        return

    if not HAS_PLAYWRIGHT:
        print("  ✗  playwright not installed.")
        return

    print("  Opening browser to authenticate with Panopto…")
    BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)

    pw  = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(BROWSER_PROFILE),
        headless=False,
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )

    # Load Canvas cookies
    canvas_cookie_file = Path("./canvas_cookies.json")
    if canvas_cookie_file.exists():
        try:
            cookies = json.loads(canvas_cookie_file.read_text(encoding="utf-8"))
            if cookies:
                ctx.add_cookies(cookies)
        except Exception:
            pass

    page = ctx.new_page()

    # Authenticate
    try:
        page.goto(
            f"{PANOPTO_BASE_URL}/Panopto/Pages/Home.aspx",
            wait_until="domcontentloaded",
            timeout=20_000,
        )
        time.sleep(3)
        if _is_login(page):
            print()
            print("═" * 62)
            print("  🔐  Login required — sign in then press ENTER")
            print("═" * 62)
            try:
                input("  [Press ENTER once signed in] ")
            except EOFError:
                time.sleep(5)
        print("  ✅  Authenticated.\n")
    except Exception as exc:
        print(f"  ⚠  Auth error: {exc}")

    dedup = load_dedup_index()
    panopto_dirs = list(root.rglob("panopto"))
    if not panopto_dirs:
        print(f"  ✗  No panopto folders found under {root}")
        ctx.close(); pw.stop()
        return

    print(f"  Found {len(panopto_dirs)} panopto folder(s).\n")
    renamed = 0
    skipped = 0

    try:
        for pdir in panopto_dirs:
            mp4_files = list(pdir.glob("recording_*.mp4"))
            if not mp4_files:
                continue
            used_names = set()   # ← add this line
            print(f"  📹  {pdir.relative_to(root)}")

            for f in mp4_files:
                partial  = f.stem.replace("recording_", "")
                full_sid = None
                for sid in dedup:
                    if sid.startswith(partial):
                        full_sid = sid
                        break

                if not full_sid:
                    print(f"    ⚠  No session ID found for {f.name} — skipping")
                    skipped += 1
                    continue

                title = get_session_title_via_browser(full_sid, page)
                time.sleep(0.5)

                if not title:
                    print(f"    ⚠  Could not fetch title for {f.name} — skipping")
                    skipped += 1
                    continue

                new_name = f"{_sanitize(title)}.mp4"
                new_path = f.parent / new_name

                if new_path == f:
                    print(f"    – (unchanged)  {f.name}")
                    skipped += 1
                    continue

                # Handle collisions by appending (2), (3) etc.
                if new_path.exists() or new_path in used_names:
                    counter = 2
                    stem = new_path.stem
                    while True:
                        new_path = f.parent / f"{stem} ({counter}).mp4"
                        if not new_path.exists() and new_path not in used_names:
                            break
                        counter += 1
                    new_name = new_path.name

                if args.dry_run:
                    print(f"    ~ (dry-run)    {f.name}  →  {new_name}")
                    used_names.add(new_path)   # ← add this line
                    renamed += 1
                    continue

                f.rename(new_path)
                print(f"    ✓  {f.name}  →  {new_name}")
                used_names.add(new_path)   # ← add this line
                dedup[full_sid] = str(new_path)
                renamed += 1

    finally:
        ctx.close()
        pw.stop()

    if not args.dry_run and renamed > 0:
        save_dedup_index(dedup)

    print(f"\n  Renamed : {renamed}")
    print(f"  Skipped : {skipped}")
    if args.dry_run:
        print("\n  (dry-run — nothing was actually renamed)")


if __name__ == "__main__":
    main()