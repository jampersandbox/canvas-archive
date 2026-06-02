#!/usr/bin/env python3
"""
canvas_auth.py
==============
Handles Canvas login via browser session.
Works in both terminal mode (press Enter) and GUI mode
(waits for gui_login_ready.txt sentinel file).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)

BROWSER_PROFILE   = Path("./browser_profile")
COOKIE_FILE       = Path("./canvas_cookies.json")
GUI_SENTINEL_FILE = Path("./gui_login_ready.txt")
_CONFIG_FILE      = Path("./canvas_config.json")
_CONFIG_PY_FILE   = Path("./canvas_config.py")


def _get_canvas_base_url() -> str:
    """
    Read the Canvas URL from config without importing anything.
    Falls back to Harvard if no config is found.
    Checks both canvas_config.json (written by the GUI) and
    canvas_config.py (written by patch_scripts.py).
    """
    # Try JSON config first (written by canvas_archive.py)
    if _CONFIG_FILE.exists():
        try:
            data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
            url = data.get("canvas_url", "").strip()
            if url.startswith("http"):
                return url
        except Exception:
            pass

    # Try .py config (written by patch_scripts / old setup)
    if _CONFIG_PY_FILE.exists():
        try:
            content = _CONFIG_PY_FILE.read_text(encoding="utf-8")
            m = re.search(
                r'CANVAS_BASE_URL\s*=\s*["\']([^"\']+)["\']', content
            )
            if m:
                url = m.group(1).strip()
                if url.startswith("http"):
                    return url
        except Exception:
            pass

    # Default fallback
    return "https://canvas.harvard.edu"


def wait_for_login_ready(
    prompt: str = "  [Press ENTER once signed in] ",
    timeout_minutes: int = 10,
) -> None:
    """
    Block until the user signals that they have finished signing in.

    Under the Canvas Archive GUI (CANVAS_ARCHIVE_GUI=1), the parent process
    spawns scripts with stdin closed, so input() would raise EOFError
    immediately. Instead, poll for the sentinel file the GUI writes when
    the user clicks the login popup's button. In terminal mode, fall back
    to input().
    """
    if os.environ.get("CANVAS_ARCHIVE_GUI"):
        print("  [Waiting for GUI login confirmation...]", flush=True)
        for _ in range(timeout_minutes * 120):
            if GUI_SENTINEL_FILE.exists():
                try:
                    GUI_SENTINEL_FILE.unlink()
                except Exception:
                    pass
                return
            time.sleep(0.5)
        return
    try:
        input(prompt)
    except EOFError:
        time.sleep(5)


def get_cookies() -> list[dict]:
    """
    Get Canvas session cookies.
    Uses saved cookies if available — only opens a browser when needed.
    Validates saved cookies before using them to catch expiry.
    """
    canvas_base_url = _get_canvas_base_url()

    # ── Fast path: saved cookies ───────────────────────────────────────────────
    if COOKIE_FILE.exists():
        try:
            cookies = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
            if cookies:
                try:
                    import requests as _req
                    test = _req.get(
                        f"{canvas_base_url}/api/v1/users/self",
                        headers={"Cookie": cookies_for_domain(
                            cookies, canvas_base_url
                        )},
                        timeout=10,
                    )
                    if test.status_code == 200:
                        log.info("  ✅  Using saved session cookies.")
                        return cookies
                    else:
                        log.info(
                            f"  ⚠  Saved cookies expired ({test.status_code})"
                            f" — logging in again."
                        )
                        COOKIE_FILE.unlink()
                except Exception:
                    log.info("  ✅  Using saved session cookies (unvalidated).")
                    return cookies
        except Exception:
            pass

    # ── Slow path: open browser ────────────────────────────────────────────────
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "playwright not installed.\n"
            "Run:  pip install playwright && playwright install chromium"
        )

    BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)

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
                f"{canvas_base_url}/",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

        import time as _time
        _time.sleep(2)

        def _is_logged_in() -> bool:
            try:
                current_url = page.url
                return (
                    "login"          not in current_url.lower()
                    and "saml"       not in current_url.lower()
                    and "sign_in"    not in current_url.lower()
                    and "shibboleth" not in current_url.lower()
                    and canvas_base_url.replace("https://", "") in current_url
                    and bool(ctx.cookies())
                )
            except Exception:
                return False

        already_in = _is_logged_in()

        if not already_in:
            print()
            print("═" * 62)
            print("  🔐  Canvas Login Required")
            print()
            print("  A browser window has just opened.")
            print("  Log in with your university credentials as normal.")
            print("  The app will continue automatically once you are in.")
            print("═" * 62)

            if in_gui:
                print("  [Watching browser for login — no button needed…]",
                      flush=True)
                for _ in range(1200):
                    if GUI_SENTINEL_FILE.exists():
                        try:
                            GUI_SENTINEL_FILE.unlink()
                        except Exception:
                            pass
                        print("  ✅  Login confirmed via button.", flush=True)
                        break
                    if _is_logged_in():
                        print("  ✅  Login detected automatically.", flush=True)
                        break
                    _time.sleep(0.5)
            else:
                try:
                    input("\n  [Press ENTER after you are logged in] ")
                except EOFError:
                    _time.sleep(5)

            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass

            print("\n  ✅  Logged in — session saved for future runs.\n")
        else:
            log.info("  ✅  Already logged in (using saved session).")

        cookies = ctx.cookies()
        ctx.close()

    COOKIE_FILE.write_text(
        json.dumps(cookies, indent=2), encoding="utf-8"
    )
    return cookies


def cookies_for_domain(cookies: list[dict], base_url: str) -> str:
    domain = (
        base_url.replace("https://", "")
                .replace("http://", "")
                .split("/")[0]
    )
    relevant = [c for c in cookies if domain in c.get("domain", "")]
    return "; ".join(f"{c['name']}={c['value']}" for c in relevant)
