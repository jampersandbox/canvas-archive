#!/usr/bin/env python3
"""
canvas_auth.py
==============
Handles Canvas login via browser session.
Works in both terminal mode and GUI mode.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

try:
    from canvas_config import CANVAS_BASE_URL as _canvas_url
    CANVAS_BASE_URL = _canvas_url
except ImportError:
    CANVAS_BASE_URL = "https://canvas.harvard.edu"

BROWSER_PROFILE   = Path("./browser_profile")
COOKIE_FILE       = Path("./canvas_cookies.json")
GUI_SENTINEL_FILE = Path("./gui_login_ready.txt")


def get_cookies() -> list[dict]:
    """
    Get Canvas session cookies.
    - If saved cookies exist, return them immediately (no browser needed).
    - Otherwise open a browser and ask the user to log in.
    """
    # ── Fast path: use saved cookies from a previous run ──────────────────────
    if COOKIE_FILE.exists():
        try:
            cookies = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
            if cookies:
                log.info("  ✅  Using saved session cookies.")
                return cookies
        except Exception:
            pass

    # ── Slow path: open browser and log in ────────────────────────────────────
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "playwright not installed.\n"
            "Run:  pip install playwright && playwright install chromium"
        )

    BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)

    # Clean up any leftover sentinel from a previous run
    if GUI_SENTINEL_FILE.exists():
        try:
            GUI_SENTINEL_FILE.unlink()
        except Exception:
            pass

    in_gui = bool(os.environ.get("CANVAS_ARCHIVE_GUI"))

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE),
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.new_page()

        log.info("  Checking Canvas session …")

        try:
            page.goto(
                f"{CANVAS_BASE_URL}/",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

        already_in = (
            "login" not in page.url.lower()
            and "saml"  not in page.url.lower()
            and CANVAS_BASE_URL.replace("https://", "") in page.url
        )

        if not already_in:
            print()
            print("═" * 62)
            print("  🔐  Canvas Login Required")
            print()
            print("  A browser window has just opened.")
            print("  Log in with your university credentials as normal.")
            print("  Once you can see the Canvas dashboard,")
            if in_gui:
                print("  click the green button in the app to continue.")
            else:
                print("  come back here and press ENTER.")
            print("═" * 62)

            if in_gui:
                # GUI mode — poll for sentinel file written by canvas_archive.py
                print("  [Waiting for GUI login confirmation...]")
                timeout_seconds = 600   # 10 minutes
                for _ in range(timeout_seconds * 2):
                    if GUI_SENTINEL_FILE.exists():
                        try:
                            GUI_SENTINEL_FILE.unlink()
                        except Exception:
                            pass
                        break
                    time.sleep(0.5)
            else:
                # Terminal mode — wait for Enter key
                # Wrap in try/except in case stdin is closed
                try:
                    input("\n  [Press ENTER after you are logged in] ")
                except EOFError:
                    # stdin was closed — wait a bit and hope they logged in
                    time.sleep(5)

            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass

            print("\n  ✅  Logged in — session saved for future runs.\n")
        else:
            log.info("  ✅  Already logged in (using saved session).")

        cookies = ctx.cookies()
        ctx.close()

    # Save cookies so subsequent scripts skip the browser entirely
    COOKIE_FILE.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
    return cookies


def cookies_for_domain(cookies: list[dict], base_url: str) -> str:
    domain = (
        base_url.replace("https://", "")
                .replace("http://", "")
                .split("/")[0]
    )
    relevant = [c for c in cookies if domain in c.get("domain", "")]
    return "; ".join(f"{c['name']}={c['value']}" for c in relevant)