#!/usr/bin/env python3
"""
canvas_auth.py
==============
Handles Canvas login via browser session.
Supports both terminal mode (press Enter) and GUI mode
(waits for a sentinel file written by canvas_archive.py).
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

BROWSER_PROFILE  = Path("./browser_profile")
COOKIE_FILE      = Path("./canvas_cookies.json")

# When running inside the GUI, canvas_archive.py creates this file
# instead of sending Enter via stdin.
GUI_SENTINEL_FILE = Path("./gui_login_ready.txt")


def _wait_for_login():
    """
    Wait for the user to finish logging in.
    - In GUI mode: waits for gui_login_ready.txt to appear
    - In terminal mode: waits for the user to press Enter
    """
    # Clean up any leftover sentinel from a previous run
    if GUI_SENTINEL_FILE.exists():
        GUI_SENTINEL_FILE.unlink()

    # Are we running inside the GUI?
    if os.environ.get("CANVAS_ARCHIVE_GUI"):
        print("  [Waiting for GUI login confirmation...]")
        # Poll for the sentinel file every 0.5 seconds
        while not GUI_SENTINEL_FILE.exists():
            time.sleep(0.5)
        # Clean up
        try:
            GUI_SENTINEL_FILE.unlink()
        except Exception:
            pass
        print("  [GUI login confirmed]")
    else:
        input("\n  [Press ENTER after you are logged in] ")


def get_cookies() -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "playwright not installed.\n"
            "Run:  pip install playwright && playwright install chromium"
        )

    BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)

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
            print("  come back here and press ENTER.")
            print("═" * 62)

            _wait_for_login()

            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass

            print("\n  ✅  Logged in — session saved for future runs.\n")
        else:
            log.info("  ✅  Already logged in (using saved session).")

        cookies = ctx.cookies()
        ctx.close()

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