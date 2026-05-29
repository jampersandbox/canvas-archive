#!/usr/bin/env python3
"""
canvas_auth.py
==============
Handles Canvas login via HarvardKey browser session.
Imported by both canvas_downloader.py and external_downloader.py.
Only asks you to log in once — saves the session in ./browser_profile/
and reuses it on every future run until the session expires.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

CANVAS_BASE_URL = "https://canvas.harvard.edu"
BROWSER_PROFILE = Path("./browser_profile")
COOKIE_FILE     = Path("./canvas_cookies.json")


def get_cookies() -> list[dict]:
    """
    Open a browser if needed, prompt for HarvardKey login,
    then return the session cookies for use in API requests.
    """
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
            print("  Log in with your HarvardKey as normal.")
            print("  Once you can see the Canvas dashboard,")
            print("  come back here and press ENTER.")
            print("═" * 62)
            input("\n  [Press ENTER after you are logged in] ")

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
    """
    Filter cookies to those belonging to base_url's domain and
    return them formatted as a Cookie: header value.
    """
    domain = base_url.replace("https://", "").replace("http://", "").split("/")[0]
    relevant = [c for c in cookies if domain in c.get("domain", "")]
    return "; ".join(f"{c['name']}={c['value']}" for c in relevant)