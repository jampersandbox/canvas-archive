#!/usr/bin/env python3
"""
panopto_downloader.py
=====================
Downloads all Panopto lecture recordings for each Canvas course.
Deduplicates across courses by Panopto session ID.

REQUIRES:  pip install yt-dlp

QUICK START
-----------
  python panopto_downloader.py --dry-run --skip-ongoing
  python panopto_downloader.py --skip-ongoing
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
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
    import yt_dlp
    HAS_YTDLP = True
except ImportError:
    HAS_YTDLP = False


# ──────────────────────────────  CONFIGURATION  ───────────────────────────────

try:
    from canvas_config import CANVAS_BASE_URL as _canvas_url, PANOPTO_BASE_URL as _pu
    CANVAS_BASE_URL  = _canvas_url
    PANOPTO_BASE_URL = _pu
except ImportError:
    CANVAS_BASE_URL  = "https://canvas.harvard.edu"
    PANOPTO_BASE_URL = "https://harvard.hosted.panopto.com"
PANOPTO_BASE_URL = "https://harvard.hosted.panopto.com"

CANVAS_COOKIES:  list[dict] = []
BROWSER_COOKIES: list[dict] = []

DOWNLOAD_DIR     = Path("./canvas_downloads")
BROWSER_PROFILE  = Path("./browser_profile")
COOKIES_FILE     = Path("./panopto_cookies.txt")
DEDUP_INDEX_FILE = Path("./panopto_dedup_index.json")
REQUEST_DELAY    = 0.2

_LOGIN_RE = re.compile(
    r"login|signin|shibboleth|harvardkey|cas\.harvard|/auth/|/saml", re.I
)
_GUID_RE = re.compile(
    r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", re.I
)


# ─────────────────────────────  LOGGING  ──────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("panopto_downloader.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ──────────────────────────  DEDUP INDEX  ─────────────────────────────────────

class DedupIndex:
    def __init__(self, path: Path):
        self.path  = path
        self._data: dict[str, str] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
                log.info(f"  Loaded dedup index: {len(self._data)} known video(s).")
            except Exception:
                pass

    def already_downloaded(self, session_id: str) -> str | None:
        return self._data.get(session_id)

    def record(self, session_id: str, filepath: str) -> None:
        self._data[session_id] = filepath
        self._save()

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")


# ────────────────────────────  CANVAS API  ────────────────────────────────────

def _canvas_get(url: str, params: dict | None = None) -> requests.Response:
    from canvas_auth import cookies_for_domain
    cookie_str = cookies_for_domain(CANVAS_COOKIES, CANVAS_BASE_URL)
    headers    = {"Cookie": cookie_str} if cookie_str else {}
    for attempt in range(4):
        try:
            r = requests.get(
                url, headers=headers, params=params or {}, timeout=60
            )
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", 15)))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Gave up: {url}")


def _paginate(url: str, params: dict | None = None) -> list:
    params = {**(params or {}), "per_page": 100}
    result, next_url, next_p = [], url, params
    while next_url:
        r = _canvas_get(next_url, next_p)
        body = r.json()
        if isinstance(body, list):
            result.extend(body)
        elif isinstance(body, dict):
            for v in body.values():
                if isinstance(v, list):
                    result.extend(v)
                    break
        next_url, next_p = None, {}
        for seg in r.headers.get("Link", "").split(","):
            if 'rel="next"' in seg:
                next_url = seg.split(";")[0].strip().strip("<>")
                break
        time.sleep(REQUEST_DELAY)
    return result


def fetch_all_courses() -> list[dict]:
    common = {"include[]": ["term"], "state[]": ["available", "completed"]}
    active = _paginate(f"{CANVAS_BASE_URL}/api/v1/courses",
                       {**common, "enrollment_state": "active"})
    done   = _paginate(f"{CANVAS_BASE_URL}/api/v1/courses",
                       {**common, "enrollment_state": "completed"})
    seen: set[int] = set()
    out: list[dict] = []
    for c in active + done:
        if c["id"] not in seen:
            seen.add(c["id"])
            out.append(c)
    return out


# ─────────────────────────────  UTILITIES  ────────────────────────────────────

def _sanitize(s: str, n: int = 160) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s).strip(". ")
    return s[:n] or "unnamed"


def _term_label(course: dict) -> str:
    name = (course.get("term") or {}).get("name", "")
    if name:
        return _sanitize(name)
    d = course.get("start_at") or course.get("created_at") or ""
    if len(d) >= 7:
        m = int(d[5:7])
        season = "Spring" if m <= 5 else ("Summer" if m <= 7 else "Fall")
        return f"{season} {d[:4]}"
    return "Unknown Term"


def _extract_folder_id(text: str) -> str | None:
    """Extract folder ID handling standard, URL-encoded and fragment formats."""
    guid = _GUID_RE.pattern

    # Standard: folderID=guid
    m = re.search(r"folderI[Dd]=(" + guid + r")", text, re.I)
    if m:
        return m.group(1)

    # URL fragment with encoding: #folderID=%22guid%22
    # This is the Harvard LTI pattern seen in frame URLs
    m = re.search(r"folderI[Dd]=%22(" + guid + r")(?:%22|\")", text, re.I)
    if m:
        return m.group(1)

    # JSON-style quotes: folderID="guid"
    m = re.search(r'folderI[Dd]="(' + guid + r')"', text, re.I)
    if m:
        return m.group(1)

    return None


def _extract_session_id(text: str) -> str | None:
    m = re.search(r"[?&]id=(" + _GUID_RE.pattern + r")", text, re.I)
    return m.group(1) if m else None


def _extract_sessions_from_html(html: str) -> list[tuple[str, str]]:
    """
    Extract (session_id, title) pairs from Panopto page HTML.
    Tries multiple patterns to find real titles alongside IDs.
    Falls back to generic names only if nothing else works.
    """
    sessions: list[tuple[str, str]] = []
    seen: set[str] = set()
    guid = _GUID_RE.pattern

    # Pattern 1: JSON with Id then Name
    for m in re.finditer(
        r'"(?:Id|id)"\s*:\s*"(' + guid + r')".*?"(?:Name|SessionName)"\s*:\s*"([^"]{2,120})"',
        html, re.I | re.DOTALL,
    ):
        sid, name = m.group(1), m.group(2)
        if sid not in seen:
            seen.add(sid)
            sessions.append((sid, name))

    # Pattern 2: JSON with Name then Id
    for m in re.finditer(
        r'"(?:Name|SessionName)"\s*:\s*"([^"]{2,120})".*?"(?:Id|id)"\s*:\s*"(' + guid + r')"',
        html, re.I | re.DOTALL,
    ):
        name, sid = m.group(1), m.group(2)
        if sid not in seen:
            seen.add(sid)
            sessions.append((sid, name))

    # Pattern 3: Viewer link with anchor text as title
    for m in re.finditer(
        r'Viewer\.aspx\?id=(' + guid + r')[^"]*"[^>]*>([^<]{2,120})</a',
        html, re.I,
    ):
        sid, name = m.group(1), m.group(2).strip()
        if sid not in seen and name:
            seen.add(sid)
            sessions.append((sid, name))

    # Fall back to ID-only patterns
    for pat in [
        r'Viewer\.aspx\?id=(' + guid + r')',
        r'"[Ii]d"\s*:\s*"(' + guid + r')"',
    ]:
        for m in re.finditer(pat, html, re.I):
            sid = m.group(1)
            if sid not in seen:
                seen.add(sid)
                sessions.append((sid, f"recording_{sid[:8]}"))

    return sessions


def _extract_all_session_ids(html: str) -> list[str]:
    ids: list[str] = []
    for m in re.finditer(
        r"Viewer\.aspx\?id=(" + _GUID_RE.pattern + r")", html, re.I
    ):
        ids.append(m.group(1))
    for m in re.finditer(r'"[Ii]d"\s*:\s*"(' + _GUID_RE.pattern + r')"', html):
        ids.append(m.group(1))
    seen: set[str] = set()
    return [x for x in ids if not (x in seen or seen.add(x))]  # type: ignore


# ────────────────────────  PANOPTO URL DISCOVERY  ─────────────────────────────

def find_panopto_urls_for_course(course_id: int) -> list[str]:
    urls: list[str] = []

    try:
        tabs = _canvas_get(
            f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/tabs"
        ).json()
        for tab in tabs:
            label   = (tab.get("label") or "").lower()
            tab_url = tab.get("url") or tab.get("full_url") or ""
            if "panopto" in label or "panopto" in tab_url.lower():
                tool_id = None
                m = re.search(r'[?&]id=(\d+)', tab_url)
                if m:
                    tool_id = m.group(1)
                else:
                    m = re.search(r'/external_tools/(\d+)', tab_url)
                    if m:
                        tool_id = m.group(1)
                if tool_id:
                    urls.append(
                        f"{CANVAS_BASE_URL}/courses/{course_id}"
                        f"/external_tools/{tool_id}"
                    )
                elif tab_url.startswith("http"):
                    urls.append(tab_url)
                else:
                    urls.append(f"{CANVAS_BASE_URL}{tab_url}")
        time.sleep(REQUEST_DELAY)
    except Exception:
        pass

    try:
        modules = _paginate(
            f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/modules",
            {"include[]": "items"},
        )
        for mod in modules:
            for item in mod.get("items", []):
                if item.get("type") != "ExternalTool":
                    continue
                name     = (item.get("title") or "").lower()
                item_url = item.get("url") or item.get("external_url") or ""
                if "panopto" in name or "panopto" in item_url.lower():
                    tool_id = None
                    m = re.search(r'[?&]id=(\d+)', item_url)
                    if m:
                        tool_id = m.group(1)
                    if tool_id:
                        urls.append(
                            f"{CANVAS_BASE_URL}/courses/{course_id}"
                            f"/external_tools/{tool_id}"
                        )
                    elif item_url and not item_url.startswith("http"):
                        urls.append(f"{CANVAS_BASE_URL}{item_url}")
                    elif item_url:
                        urls.append(item_url)
    except Exception:
        pass

    try:
        pages = _paginate(
            f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/pages"
        )
        for stub in pages:
            try:
                detail = _canvas_get(
                    f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}"
                    f"/pages/{stub['url']}"
                ).json()
                body = detail.get("body") or ""
                time.sleep(REQUEST_DELAY)
                for m in re.finditer(
                    r'src=["\']([^"\']*panopto[^"\']*)["\']', body, re.I
                ):
                    sid = _extract_session_id(m.group(1))
                    if sid:
                        urls.append(
                            f"{PANOPTO_BASE_URL}/Panopto/Pages/Viewer.aspx"
                            f"?id={sid}"
                        )
            except Exception:
                pass
    except Exception:
        pass

    seen: set[str] = set()
    return [u for u in urls if not (u in seen or seen.add(u))]  # type: ignore


# ─────────────────────────────  PANOPTO API  ──────────────────────────────────

def _panopto_session() -> requests.Session:
    s = requests.Session()
    for c in BROWSER_COOKIES:
        if "panopto" in c.get("domain", "").lower():
            try:
                s.cookies.set(c["name"], c["value"],
                              domain=c.get("domain", ""),
                              path=c.get("path", "/"))
            except Exception:
                pass
    return s


def list_panopto_sessions(folder_id: str) -> list[dict]:
    sess = _panopto_session()
    all_items: list[dict] = []
    page = 0
    while True:
        try:
            r = sess.get(
                f"{PANOPTO_BASE_URL}/Panopto/api/v1/folders/{folder_id}/sessions",
                params={"maxResults": 50, "pageNumber": page,
                        "sortField": "CreatedDate", "sortOrder": "Asc"},
                timeout=30,
            )
            r.raise_for_status()
            data  = r.json()
            items = data.get("Results", [])
            if not items:
                break
            all_items.extend(items)
            if len(all_items) >= data.get("TotalNumberResults", 0):
                break
            page += 1
        except Exception as exc:
            log.warning(f"    Panopto API error: {exc}")
            break
    return all_items


def get_session_title(session_id: str) -> str | None:
    try:
        r = _panopto_session().get(
            f"{PANOPTO_BASE_URL}/Panopto/api/v1/sessions/{session_id}",
            timeout=15,
        )
        if r.ok:
            return r.json().get("Name")
    except Exception:
        pass
    return None


# ───────────────────────────  COOKIE BRIDGE  ──────────────────────────────────

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


# ──────────────────────────  VIDEO DOWNLOAD  ──────────────────────────────────

def download_video(
    session_id: str,
    dest_dir:   Path,
    title:      str,
    dedup:      DedupIndex,
    quality:    str  = "best",
    dry_run:    bool = False,
) -> bool:
    viewer_url = (
        f"{PANOPTO_BASE_URL}/Panopto/Pages/Viewer.aspx?id={session_id}"
    )
    safe = _sanitize(title)

    existing_path = dedup.already_downloaded(session_id)
    if existing_path:
        rel = Path(existing_path)
        try:
            rel = Path(existing_path).relative_to(DOWNLOAD_DIR.resolve())
        except ValueError:
            pass
        log.info(f"    – (duplicate)  '{safe}'  →  {rel}")
        if not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
            note = dest_dir / f"[DUPLICATE] {safe}.txt"
            if not note.exists():
                note.write_text(
                    f"Already downloaded to:\n{existing_path}\n"
                    f"Session ID: {session_id}\nViewer URL: {viewer_url}\n",
                    encoding="utf-8",
                )
        return True

    dest_dir.mkdir(parents=True, exist_ok=True)
    existing = [f for f in dest_dir.glob(f"{safe}.*") if f.suffix != ".txt"]
    if existing:
        log.info(f"    – (exists)     {existing[0].name}")
        dedup.record(session_id, str(existing[0].resolve()))
        return True

    if dry_run:
        log.info(f"    ~ (dry-run)    {safe}")
        return True

    log.info(f"    ↓ {safe}")
    ydl_opts = {
        "cookiefile":  str(COOKIES_FILE),
        "outtmpl":     str(dest_dir / f"{safe}.%(ext)s"),
        "format":      quality,
        "quiet":       False,
        "no_warnings": False,
        "retries":     3,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([viewer_url])
        saved = next(
            (f for f in dest_dir.glob(f"{safe}.*") if f.suffix != ".txt"), None
        )
        if saved:
            dedup.record(session_id, str(saved.resolve()))
            log.info(f"    ✓  {saved.name}")
        return True
    except Exception as exc:
        log.warning(f"    ✗  {exc}")
        return False


# ─────────────────────────────  BROWSER  ──────────────────────────────────────

def _is_login(page) -> bool:
    try:
        return bool(_LOGIN_RE.search(page.url) or _LOGIN_RE.search(page.title()))
    except Exception:
        return False


class PanoptoBrowser:

    def __init__(self):
        self._pw     = None
        self._ctx    = None
        self._page   = None
        self._authed = False

    def __enter__(self):
        BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)
        self._pw  = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE),
            headless=False,
            accept_downloads=True,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )

        # Load Canvas session cookies into the browser
        canvas_cookie_file = Path("./canvas_cookies.json")
        if canvas_cookie_file.exists():
            try:
                cookies = json.loads(
                    canvas_cookie_file.read_text(encoding="utf-8")
                )
                if cookies:
                    self._ctx.add_cookies(cookies)
                    log.info(
                        f"  Loaded {len(cookies)} Canvas session cookie(s) "
                        f"into browser."
                    )
            except Exception as exc:
                log.warning(f"  Could not load Canvas cookies: {exc}")

        self._page = self._ctx.new_page()
        return self

    def __exit__(self, *_):
        try:
            self._ctx.close()
            self._pw.stop()
        except Exception:
            pass

    def _handle_auth(self):
        if self._authed:
            return
        print()
        print("═" * 62)
        print("  🔐  Login required — Panopto / HarvardKey")
        print("      Sign in in the browser window,")
        print("      then come back here and press ENTER.")
        print("═" * 62)
        from canvas_auth import wait_for_login_ready
        wait_for_login_ready("  [Press ENTER once signed in] ")
        try:
            self._page.wait_for_load_state("networkidle", timeout=20_000)
        except PWTimeout:
            pass
        self._authed = True
        self._save_cookies()
        log.info("  ✅  Panopto authenticated.\n")

    def _save_cookies(self):
        global BROWSER_COOKIES
        BROWSER_COOKIES = self._ctx.cookies()
        write_netscape_cookies(BROWSER_COOKIES, COOKIES_FILE)

    def resolve_sessions(self, canvas_url: str) -> list[tuple[str, str]]:
        """
        Navigate to a Canvas LTI Panopto URL and return
        [(session_id, title), ...] for all recordings.

        KEY FIXES:
        1. Intercepts Panopto API network responses to capture
           folder/session IDs directly from network traffic.
        2. Re-navigates to the LTI URL after authentication.
        3. Reads folder ID from JS hash (frame.url strips fragments).
        4. Scrolls inside iframe to trigger lazy loading of all recordings.
        5. Final late-frame check after timeout.
        """
        page = self._page

        # ── Network interception ───────────────────────────────────────────
        captured_folder_ids: list[str] = []
        captured_sessions:   list[tuple[str, str]] = []

        def _on_response(response):
            try:
                url = response.url
                if "panopto" not in url.lower():
                    return
                if response.status != 200:
                    return
                if not any(p in url for p in
                           ["/api/v1/", "/Sessions/", "getFolderInfo",
                            "getSessionList", "BrowseList"]):
                    return
                try:
                    body = response.json()
                except Exception:
                    return
                if not isinstance(body, dict):
                    return

                # Capture folder IDs
                for key in ("ID", "Id", "id", "FolderId", "folderId",
                            "ParentFolder", "ParentId"):
                    val = body.get(key)
                    if val and isinstance(val, str) and _GUID_RE.match(val):
                        if val not in captured_folder_ids:
                            captured_folder_ids.append(val)
                            log.info(f"    ✓ Folder ID intercepted: {val}")

                # Capture sessions directly
                results = body.get("Results") or body.get("results") or []
                for s in results:
                    if not isinstance(s, dict):
                        continue
                    sid  = s.get("Id")   or s.get("id")   or ""
                    name = s.get("Name") or s.get("name") or "recording"
                    if sid and _GUID_RE.match(sid):
                        if not any(x[0] == sid for x in captured_sessions):
                            captured_sessions.append((sid, name))

            except Exception:
                pass

        page.on("response", _on_response)

        def _navigate_and_wait(url: str) -> None:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=40_000)
            except PWTimeout:
                log.warning("    (page load timed out — continuing)")
            except Exception as exc:
                log.warning(f"    Navigation error: {exc}")
            time.sleep(6)

        try:
            log.info(f"    Navigating: {canvas_url}")
            _navigate_and_wait(canvas_url)

            if _is_login(page):
                self._handle_auth()
                time.sleep(3)
                log.info("    Re-navigating after authentication…")
                _navigate_and_wait(canvas_url)

            # ── Wait for Panopto iframe ────────────────────────────────────
            panopto_frame = None
            for i in range(25):
                for frame in page.frames:
                    try:
                        furl = frame.url or ""
                        if "panopto" in furl.lower() and furl != canvas_url:
                            panopto_frame = frame
                            break
                    except Exception:
                        pass
                if panopto_frame:
                    log.info(
                        f"    ✓ Found Panopto frame: {panopto_frame.url[:80]}"
                    )
                    break
                log.info(f"    [{i+1}/25] Waiting for Panopto frame…")
                time.sleep(1)

            # ── Final late-frame check ─────────────────────────────────────
            if not panopto_frame:
                log.info("    Doing one final frame check after timeout…")
                time.sleep(5)
                for frame in page.frames:
                    try:
                        furl = frame.url or ""
                        if "panopto" in furl.lower() and furl != canvas_url:
                            panopto_frame = frame
                            log.info(
                                f"    ✓ Found Panopto frame (late): "
                                f"{furl[:80]}"
                            )
                            break
                    except Exception:
                        pass

            if not panopto_frame:
                log.warning("    No Panopto frame found after 30s.")
                log.info(f"    Page URL   : {page.url}")
                try:
                    log.info(f"    Page title : {page.title()}")
                except Exception:
                    pass
                all_frames = [f.url for f in page.frames if f.url]
                if all_frames:
                    log.info(f"    Frames present: {all_frames}")
                else:
                    log.info("    No sub-frames present on this page.")
                return []

            self._save_cookies()

            # ── Strategy 1: sessions captured via network interception ─────
            log.info("    Waiting for Panopto API calls to complete…")
            for _ in range(20):
                if captured_sessions:
                    log.info(
                        f"    ✓ {len(captured_sessions)} session(s) captured "
                        f"via network interception."
                    )
                    return captured_sessions
                if captured_folder_ids:
                    break
                time.sleep(1)

            # ── Strategy 2: use intercepted folder ID with REST API ────────
            folder_id = captured_folder_ids[0] if captured_folder_ids else None

            # ── Strategy 3: folder ID from frame URL ──────────────────────
            if not folder_id:
                folder_id = _extract_folder_id(panopto_frame.url)
                if folder_id:
                    log.info(f"    ✓ Folder ID from frame URL: {folder_id}")

            # ── Strategy 3b: folder ID from JS hash ───────────────────────
            # frame.url strips URL fragments — must use JS to read them
            if not folder_id:
                try:
                    result = panopto_frame.evaluate("""
                        () => {
                            try {
                                const hash = window.location.hash;
                                const m = hash.match(
                                    /folderID=%22([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})(?:%22|")/i
                                );
                                if (m) return m[1];
                                const params = new URLSearchParams(
                                    window.location.search
                                );
                                return params.get('folderID')
                                    || params.get('folderId')
                                    || null;
                            } catch(e) { return null; }
                        }
                    """)
                    if result and _GUID_RE.match(str(result)):
                        folder_id = result
                        log.info(
                            f"    ✓ Folder ID from JS hash: {folder_id}"
                        )
                except Exception:
                    pass

            # ── Strategy 4: folder ID via JavaScript ──────────────────────
            if not folder_id:
                try:
                    result = panopto_frame.evaluate("""
                        () => {
                            try {
                                const p = new URL(
                                    window.location.href
                                ).searchParams;
                                return p.get('folderID') ||
                                       p.get('folderId') ||
                                       p.get('folder')   ||
                                       window.folderId   ||
                                       window.folderID   || null;
                            } catch(e) { return null; }
                        }
                    """)
                    if result and _GUID_RE.match(str(result)):
                        folder_id = result
                        log.info(f"    ✓ Folder ID from JS: {folder_id}")
                except Exception:
                    pass

            # ── Strategy 5: folder ID from frame HTML ─────────────────────
            if not folder_id:
                try:
                    html = panopto_frame.content()
                    folder_id = _extract_folder_id(html)
                    if folder_id:
                        log.info(
                            f"    ✓ Folder ID from frame HTML: {folder_id}"
                        )
                except Exception:
                    pass

            # ── Use folder ID with REST API ────────────────────────────────
            if folder_id:
                sessions = list_panopto_sessions(folder_id)
                log.info(f"    API returned {len(sessions)} session(s).")
                if sessions:
                    return [
                        (
                            s.get("Id") or s.get("id", ""),
                            s.get("Name") or s.get("name") or "recording",
                        )
                        for s in sessions
                        if s.get("Id") or s.get("id")
                    ]

            # ── Strategy 6: scroll inside iframe + scrape ─────────────────
            log.info("    Scraping frame HTML for session IDs…")
            try:
                prev_count = 0
                for i in range(20):
                    try:
                        panopto_frame.evaluate("""
                            () => {
                                const containers = [
                                    document.querySelector('#folderList'),
                                    document.querySelector('.folder-list'),
                                    document.querySelector('.session-list'),
                                    document.querySelector(
                                        '[class*="sessionList"]'
                                    ),
                                    document.querySelector(
                                        '[class*="session-list"]'
                                    ),
                                    document.querySelector('[class*="scroll"]'),
                                    document.documentElement,
                                    document.body,
                                ];
                                for (const el of containers) {
                                    if (el && el.scrollHeight > el.clientHeight) {
                                        el.scrollTop = el.scrollHeight;
                                    }
                                }
                                window.scrollTo(
                                    0, document.body.scrollHeight
                                );
                            }
                        """)
                    except Exception:
                        break
                    time.sleep(1.5)

                    # Stop scrolling once count stabilises
                    try:
                        html = panopto_frame.content()
                        current_ids = _extract_all_session_ids(html)
                        if i >= 2 and len(current_ids) == prev_count:
                            log.info(
                                f"    Scroll stable at {prev_count} "
                                f"session(s)"
                            )
                            break
                        prev_count = len(current_ids)
                    except Exception:
                        break

                html     = panopto_frame.content()
                sessions = _extract_sessions_from_html(html)
                log.info(f"    Found {len(sessions)} session(s) in frame HTML.")

                # For any sessions still with generic names, try the API
                result = []
                for sid, name in sessions:
                    if name.startswith("recording_"):
                        fetched = get_session_title(sid)
                        if fetched:
                            log.info(f"    ✓ Title from API: {fetched}")
                        result.append((sid, fetched if fetched else name))
                    else:
                        result.append((sid, name))

                if result:
                    log.info(
                        f"    Fetching real titles for "
                        f"{len(result)} recording(s)…"
                    )
                    final = []
                    for sid, name in result:
                        if name.startswith("recording_"):
                            fetched = get_session_title(sid)
                            if fetched:
                                log.info(
                                    f"    ✓ {name}  →  {fetched}.mp4"
                                )
                                final.append((sid, fetched))
                            else:
                                final.append((sid, name))
                        else:
                            final.append((sid, name))
                    return final

                return result

            except Exception as exc:
                log.warning(f"    Frame scrape error: {exc}")

            return []

        finally:
            try:
                page.remove_listener("response", _on_response)
            except Exception:
                pass

    def ensure_panopto_auth(self):
        try:
            self._page.goto(
                f"{PANOPTO_BASE_URL}/Panopto/Pages/Home.aspx",
                wait_until="domcontentloaded",
                timeout=20_000,
            )
            time.sleep(3)
            if _is_login(self._page):
                self._handle_auth()
            self._save_cookies()
        except Exception:
            pass


# ──────────────────────────  COURSE PROCESSING  ───────────────────────────────

def process_course(
    course:       dict,
    panopto_urls: list[str],
    browser:      PanoptoBrowser,
    dedup:        DedupIndex,
    quality:      str  = "best",
    dry_run:      bool = False,
) -> dict[str, int]:
    course_id   = course["id"]
    course_name = _sanitize(course.get("name") or f"course_{course_id}")
    term        = _term_label(course)
    dest_dir    = DOWNLOAD_DIR / term / course_name / "panopto"

    log.info(f"\n{'─' * 70}")
    log.info(f"  📹  {term}  /  {course_name}")
    log.info(f"{'─' * 70}")
    log.info(f"    Processing {len(panopto_urls)} Panopto link(s).")

    counts = {"downloaded": 0, "duplicate": 0, "failed": 0}
    to_download: list[tuple[str, str]] = []
    seen_sids:   set[str] = set()

    for url in panopto_urls:
        sid = _extract_session_id(url)
        if sid:
            if sid not in seen_sids:
                seen_sids.add(sid)
                title = get_session_title(sid) or f"recording_{sid[:8]}"
                to_download.append((sid, title))
            continue

        sessions = browser.resolve_sessions(url)
        for sid, title in sessions:
            if sid not in seen_sids:
                seen_sids.add(sid)
                to_download.append((sid, title))

    log.info(f"    Total recordings to process: {len(to_download)}")

    for sid, title in to_download:
        was_already_known = dedup.already_downloaded(sid) is not None
        ok = download_video(
            session_id=sid,
            dest_dir=dest_dir,
            title=title,
            dedup=dedup,
            quality=quality,
            dry_run=dry_run,
        )
        if ok:
            if was_already_known:
                counts["duplicate"] += 1
            else:
                counts["downloaded"] += 1
        else:
            counts["failed"] += 1

    log.info(
        f"  → downloaded: {counts['downloaded']}  "
        f"duplicate: {counts['duplicate']}  "
        f"failed: {counts['failed']}"
    )
    return counts


# ──────────────────────────────────  MAIN  ────────────────────────────────────

def main() -> None:
    global DOWNLOAD_DIR, CANVAS_COOKIES, BROWSER_COOKIES

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run",      action="store_true")
    parser.add_argument("--dir",          metavar="PATH", default=str(DOWNLOAD_DIR))
    parser.add_argument("--skip-ongoing", action="store_true")
    parser.add_argument("--course",       metavar="NAME")
    parser.add_argument("--quality",      metavar="QUAL", default="best")
    args = parser.parse_args()

    DOWNLOAD_DIR = Path(args.dir)

    if not HAS_YTDLP:
        print("\n[ERROR] yt-dlp not installed.  Run:  pip install yt-dlp\n")
        sys.exit(1)
    if not HAS_PLAYWRIGHT:
        print("\n[ERROR] playwright not installed.\n")
        sys.exit(1)

    from canvas_auth import get_cookies
    CANVAS_COOKIES  = get_cookies()
    BROWSER_COOKIES = list(CANVAS_COOKIES)
    if not CANVAS_COOKIES:
        log.error("Could not get Canvas session.")
        sys.exit(1)

    dedup = DedupIndex(DEDUP_INDEX_FILE)

    log.info("═" * 70)
    log.info("  📹  Panopto Downloader  (network interception + re-nav fix)")
    log.info("═" * 70)
    if args.dry_run:
        log.info("  DRY-RUN — nothing will be written.\n")

    log.info("\nFetching courses …")
    courses = fetch_all_courses()
    if args.skip_ongoing:
        courses = [c for c in courses if _term_label(c).lower() != "ongoing"]
    if args.course:
        courses = [c for c in courses
                   if args.course.lower() in (c.get("name") or "").lower()]

    log.info(f"Scanning {len(courses)} course(s) for Panopto content …\n")
    courses_with_panopto: list[tuple[dict, list[str]]] = []
    for course in courses:
        if not course.get("name"):
            continue
        urls = find_panopto_urls_for_course(course["id"])
        if urls:
            log.info(f"  ✓  {course['name']}  ({len(urls)} link(s))")
            courses_with_panopto.append((course, urls))
        else:
            log.info(f"  –  {course['name']}")

    if not courses_with_panopto:
        log.info("\nNo Panopto content found in any course.")
        return

    log.info(f"\nDownloading from {len(courses_with_panopto)} course(s).\n")

    totals = {"downloaded": 0, "duplicate": 0, "failed": 0}
    with PanoptoBrowser() as browser:
        browser.ensure_panopto_auth()
        for course, urls in courses_with_panopto:
            try:
                counts = process_course(
                    course, urls, browser, dedup, args.quality, args.dry_run
                )
                for k in totals:
                    totals[k] += counts[k]
            except Exception as exc:
                log.error(f"  ✗  Error on '{course.get('name')}': {exc}")

    log.info(f"\n{'═' * 70}")
    log.info("  ✅  FINISHED")
    log.info(f"  Downloaded        : {totals['downloaded']}")
    log.info(f"  Duplicates skipped: {totals['duplicate']}")
    log.info(f"  Failed            : {totals['failed']}")
    if not args.dry_run:
        log.info(f"  Saved to          : {DOWNLOAD_DIR.resolve()}")
    log.info("═" * 70)


if __name__ == "__main__":
    main()