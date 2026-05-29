#!/usr/bin/env python3
"""
reserves_downloader.py  (framenavigated fix)
=============================================
Downloads library reserve readings for each Canvas course.

QUICK START
-----------
  python reserves_downloader.py --dry-run --skip-ongoing
  python reserves_downloader.py --skip-ongoing
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse, quote

import requests

try:
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False


# ──────────────────────────────  CONFIGURATION  ───────────────────────────────

CANVAS_BASE_URL  = "https://canvas.harvard.edu"
EZPROXY_PREFIX   = "https://login.ezp-prod1.hul.harvard.edu/login?url="
CANVAS_COOKIES:  list[dict] = []
BROWSER_COOKIES: list[dict] = []
DOWNLOAD_DIR     = Path("./canvas_downloads")
BROWSER_PROFILE  = Path("./browser_profile")
REQUEST_DELAY    = 0.2
CHUNK_SIZE       = 65_536
DEDUP_INDEX_FILE = Path("./reserves_dedup_index.json")

_RESERVES_LABEL_RE = re.compile(
    r"\b(library|reserve|e.?reserve|reading|course.?pack"
    r"|course.?material|leganto|hollis|ares)\b",
    re.I,
)

_LOGIN_RE = re.compile(
    r"login|signin|shibboleth|harvardkey|cas\.harvard|/auth/|/saml", re.I
)

# Domains that indicate we have reached the Leganto reading list
_LEGANTO_RE = re.compile(
    r"leganto|exlibrisgroup|alma\.|hvd\.alma", re.I
)


# ─────────────────────────────  LOGGING  ──────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("reserves_downloader.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ──────────────────────────  DEDUP INDEX  ─────────────────────────────────────

class DedupIndex:
    def __init__(self, path: Path):
        self.path = path
        self._urls:   dict[str, str] = {}
        self._hashes: dict[str, str] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._urls   = data.get("urls",   {})
                self._hashes = data.get("hashes", {})
                log.info(
                    f"  Loaded reserves dedup index: "
                    f"{len(self._urls)} URL(s), "
                    f"{len(self._hashes)} hash(es)."
                )
            except Exception:
                pass

    def known_url(self, url: str) -> str | None:
        return self._urls.get(self._norm_url(url))

    def known_hash(self, filepath: Path) -> str | None:
        h = self._md5(filepath)
        return self._hashes.get(h) if h else None

    def record(self, url: str, filepath: Path) -> None:
        abs_path = str(filepath.resolve())
        self._urls[self._norm_url(url)] = abs_path
        h = self._md5(filepath)
        if h:
            self._hashes[h] = abs_path
        self._save()

    def record_url_only(self, url: str, filepath_str: str) -> None:
        self._urls[self._norm_url(url)] = filepath_str
        self._save()

    @staticmethod
    def _norm_url(url: str) -> str:
        url = re.sub(
            r"https?://[^/]*ezp[^/]*/login\?url=", "", url, flags=re.I
        )
        url = unquote(url).rstrip("/").strip()
        try:
            p = urlparse(url)
            url = p._replace(
                scheme=p.scheme.lower(), netloc=p.netloc.lower()
            ).geturl()
        except Exception:
            pass
        return url

    @staticmethod
    def _md5(filepath: Path) -> str:
        try:
            h = hashlib.md5()
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return ""

    def _save(self) -> None:
        self.path.write_text(
            json.dumps(
                {"urls": self._urls, "hashes": self._hashes}, indent=2
            ),
            encoding="utf-8",
        )


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
        r    = _canvas_get(next_url, next_p)
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
    common = {
        "include[]": ["term", "syllabus_body"],
        "state[]":   ["available", "completed"],
    }
    active = _paginate(
        f"{CANVAS_BASE_URL}/api/v1/courses",
        {**common, "enrollment_state": "active"},
    )
    done = _paginate(
        f"{CANVAS_BASE_URL}/api/v1/courses",
        {**common, "enrollment_state": "completed"},
    )
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


def _guess_filename(
    url: str, text: str = "", default_ext: str = ".pdf"
) -> str:
    path = unquote(urlparse(url).path)
    base = path.rstrip("/").rsplit("/", 1)[-1]
    if "." in base:
        suffix = base.rsplit(".", 1)[-1]
        if 1 < len(suffix) <= 5:
            return _sanitize(base)
    if text and len(text.strip()) > 3:
        return _sanitize(re.sub(r"\s+", "_", text.strip())) + default_ext
    return _sanitize(base or "reading") + default_ext


def _build_proxy_url(url: str) -> str:
    if "ezp-prod" in url or "ezproxy" in url:
        return url
    return EZPROXY_PREFIX + quote(url, safe=":/?=&%#@!$'()*+,;")


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n //= 1024
    return f"{n:.1f} TB"


def _leave_duplicate_note(
    dest_dir: Path, filename: str, original_path: str, url: str
) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    note = dest_dir / f"[DUPLICATE] {_sanitize(filename)}.txt"
    if not note.exists():
        note.write_text(
            f"This file is a duplicate and was not re-downloaded.\n\n"
            f"Original copy: {original_path}\n"
            f"Source URL:    {url}\n",
            encoding="utf-8",
        )


def _canvas_tool_web_url(course_id: int, raw_url: str) -> str:
    tool_id = None
    m = re.search(r'[?&]id=(\d+)', raw_url)
    if m:
        tool_id = m.group(1)
    else:
        m = re.search(r'/external_tools/(\d+)', raw_url)
        if m:
            tool_id = m.group(1)
    if tool_id:
        return (
            f"{CANVAS_BASE_URL}/courses/{course_id}"
            f"/external_tools/{tool_id}"
        )
    if raw_url.startswith("http"):
        return raw_url
    return f"{CANVAS_BASE_URL}{raw_url}"


# ───────────────────────  RESERVES TAB DISCOVERY  ─────────────────────────────

def find_reserves_urls_for_course(course_id: int) -> list[dict]:
    found: list[dict] = []

    try:
        tabs = _canvas_get(
            f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/tabs"
        ).json()
        for tab in tabs:
            label   = tab.get("label") or ""
            tab_url = tab.get("url") or tab.get("full_url") or ""
            if _RESERVES_LABEL_RE.search(label):
                web_url = _canvas_tool_web_url(course_id, tab_url)
                found.append({"url": web_url, "label": label})
                log.info(f"    Found reserves tab: {label}")
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
                name     = item.get("title") or ""
                item_url = item.get("url") or item.get("external_url") or ""
                if _RESERVES_LABEL_RE.search(name) and item_url:
                    web_url = _canvas_tool_web_url(course_id, item_url)
                    found.append({"url": web_url, "label": name})
        time.sleep(REQUEST_DELAY)
    except Exception:
        pass

    seen: set[str] = set()
    return [
        r for r in found
        if not (r["url"] in seen or seen.add(r["url"]))  # type: ignore
    ]


# ────────────────────────────  FILE DOWNLOAD  ─────────────────────────────────

def download_file(
    url:      str,
    dest_dir: Path,
    filename: str,
    cookies:  list[dict],
    dedup:    DedupIndex,
    dry_run:  bool = False,
) -> bool:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / _sanitize(filename)

    existing_path = dedup.known_url(url)
    if existing_path:
        try:
            rel = Path(existing_path).relative_to(DOWNLOAD_DIR.resolve())
        except ValueError:
            rel = Path(existing_path)
        log.info(f"    – (URL dup)  {dest.name}  →  {rel}")
        if not dry_run:
            _leave_duplicate_note(dest_dir, filename, existing_path, url)
        return True

    if dest.exists():
        log.info(f"    – (exists)   {dest.name}")
        dedup.record(url, dest)
        return True

    if dry_run:
        log.info(f"    ~ (dry-run)  {dest.name}")
        return True

    sess = requests.Session()
    for c in cookies:
        try:
            sess.cookies.set(
                c["name"], c["value"],
                domain=c.get("domain", ""),
                path=c.get("path", "/"),
            )
        except Exception:
            pass

    try:
        r = sess.get(
            url, stream=True, timeout=60,
            headers={"User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )},
        )
        r.raise_for_status()
        ct = r.headers.get("content-type", "").lower()
        if "text/html" in ct and ".pdf" not in url.lower():
            log.warning("    ✗  Got HTML instead of file")
            return False
        total = int(r.headers.get("content-length", 0))
        with open(dest, "wb") as fh:
            if HAS_TQDM and total:
                with tqdm(
                    total=total, unit="B", unit_scale=True,
                    unit_divisor=1024,
                    desc=f"    ↓ {dest.name[:48]}", leave=False,
                ) as bar:
                    for chunk in r.iter_content(CHUNK_SIZE):
                        fh.write(chunk)
                        bar.update(len(chunk))
            else:
                for chunk in r.iter_content(CHUNK_SIZE):
                    fh.write(chunk)
    except Exception as exc:
        log.warning(f"    ✗  {filename}: {exc}")
        if dest.exists():
            dest.unlink()
        return False

    existing_by_hash = dedup.known_hash(dest)
    if existing_by_hash:
        try:
            rel = Path(existing_by_hash).relative_to(DOWNLOAD_DIR.resolve())
        except ValueError:
            rel = Path(existing_by_hash)
        log.info(f"    – (content dup)  {dest.name}  →  {rel}")
        dest.unlink()
        _leave_duplicate_note(dest_dir, filename, existing_by_hash, url)
        dedup.record_url_only(url, existing_by_hash)
        return True

    dedup.record(url, dest)
    log.info(f"    ✓  {dest.name}  [{fmt_size(dest.stat().st_size)}]")
    return True


# ─────────────────────────────  BROWSER  ──────────────────────────────────────

class ReservesBrowser:

    def __init__(self):
        self._pw     = None
        self._ctx    = None
        self._page   = None
        self._authed: set[str] = set()

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
        self._page = self._ctx.new_page()
        return self

    def __exit__(self, *_):
        try:
            self._ctx.close()
            self._pw.stop()
        except Exception:
            pass

    def _is_login(self) -> bool:
        try:
            return bool(
                _LOGIN_RE.search(self._page.url)
                or _LOGIN_RE.search(self._page.title())
            )
        except Exception:
            return False

    def _handle_auth(self):
        domain = urlparse(self._page.url).netloc
        if domain in self._authed:
            return
        print()
        print("═" * 62)
        print(f"  🔐  Login required  —  {domain}")
        print("      Sign in with HarvardKey in the browser,")
        print("      then come back here and press ENTER.")
        print("═" * 62)
        input("  [Press ENTER once signed in and on the reading list] ")
        try:
            self._page.wait_for_load_state("networkidle", timeout=20_000)
        except PWTimeout:
            pass
        self._authed.add(domain)
        self._authed.add(urlparse(self._page.url).netloc)
        log.info(f"  ✅  Authenticated — {domain}\n")

    def get_reading_links(self, reserves_url: str) -> list[dict]:
        """
        Navigate to a Canvas LTI reserves URL and extract all reading
        links from the Leganto reading list.

        KEY FIX: Uses page.on("framenavigated") to capture the Leganto
        URL the instant it loads in the iframe (even via POST navigation
        which does not update frame.url). Then navigates the main page
        directly to that URL so we can read its content without any
        cross-origin iframe restrictions.
        """
        page  = self._page
        links: list[dict] = []

        # ── Set up framenavigated listener BEFORE navigating ──────────────────
        # This fires for ALL frame navigations including iframes,
        # even when the navigation is via form POST.
        leganto_urls: list[str] = []

        def on_frame_nav(frame):
            try:
                url = frame.url or ""
                if url and url not in ("about:blank", "", reserves_url):
                    log.info(f"    Frame nav → {url[:90]}")
                if _LEGANTO_RE.search(url):
                    if url not in leganto_urls:
                        leganto_urls.append(url)
                        log.info(f"    ✓ Leganto URL captured: {url[:80]}")
            except Exception:
                pass

        page.on("framenavigated", on_frame_nav)

        try:
            # ── Navigate to Canvas LTI URL ────────────────────────────────────
            log.info(f"    Navigating to: {reserves_url}")
            try:
                page.goto(
                    reserves_url,
                    wait_until="domcontentloaded",
                    timeout=45_000,
                )
            except PWTimeout:
                log.warning("    (page load timed out — continuing)")
            except Exception as exc:
                log.warning(f"    Navigation error: {exc}")

            time.sleep(5)

            if self._is_login():
                self._handle_auth()
                time.sleep(5)

            # ── Wait up to 45s for Leganto URL to appear ──────────────────────
            log.info("    Waiting for Leganto to load (up to 45s)…")
            for i in range(45):
                if leganto_urls:
                    break
                time.sleep(1)

            if not leganto_urls:
                log.warning(
                    "    No Leganto URL found after 45s — "
                    "reading list may be empty."
                )
                return links

            leganto_url = leganto_urls[0]
            log.info(f"    Navigating main page to: {leganto_url[:80]}")

            # ── Navigate MAIN PAGE directly to the Leganto URL ────────────────
            # This avoids cross-origin iframe restrictions entirely.
            try:
                page.goto(
                    leganto_url,
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
            except PWTimeout:
                log.warning("    (Leganto page load timed out — continuing)")
            except Exception as exc:
                log.warning(f"    Leganto navigation error: {exc}")

            time.sleep(3)

            if self._is_login():
                self._handle_auth()
                time.sleep(3)

            log.info(f"    On Leganto page: {page.url[:80]}")

            # ── Wait for Angular SPA to render ────────────────────────────────
            log.info("    Waiting for Leganto SPA to render…")
            time.sleep(12)

            # ── Expand all collapsed sections ─────────────────────────────────
            try:
                btns = page.query_selector_all(
                    'button[aria-expanded="false"], '
                    'mat-expansion-panel-header:not([aria-expanded="true"]), '
                    '[role="button"][aria-expanded="false"]'
                )
                log.info(
                    f"    Found {len(btns)} collapsed section(s) to expand."
                )
                for btn in btns[:30]:
                    try:
                        btn.click()
                        time.sleep(0.3)
                    except Exception:
                        pass
                if btns:
                    time.sleep(4)
            except Exception:
                pass

            # ── Scroll to trigger lazy loading ────────────────────────────────
            for _ in range(10):
                try:
                    page.evaluate(
                        "window.scrollTo(0, document.body.scrollHeight)"
                    )
                    time.sleep(1.5)
                except Exception:
                    break

            time.sleep(5)

            # ── Extract links from the Leganto page ───────────────────────────
            try:
                html = page.content()
                log.info(
                    f"    Got {len(html):,} bytes from Leganto page."
                )

                # Leganto citation links
                for m in re.finditer(
                    r'href=["\']([^"\']*leganto[^"\']*citation/[^"\']+)["\']',
                    html, re.I,
                ):
                    url = m.group(1)
                    if not url.startswith("http"):
                        url = urljoin(page.url, url)
                    links.append(
                        {"title": "", "url": url, "page_url": page.url}
                    )

                # EZProxy full-text links
                for m in re.finditer(
                    r'href=["\']([^"\']*ezp-prod[^"\']*)["\']', html, re.I
                ):
                    url = m.group(1)
                    if not url.startswith("http"):
                        url = urljoin(page.url, url)
                    links.append(
                        {"title": "", "url": url, "page_url": page.url}
                    )

                # Direct PDF links
                for m in re.finditer(
                    r'href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']',
                    html, re.I,
                ):
                    url = m.group(1)
                    if url.startswith("http"):
                        links.append(
                            {"title": "", "url": url, "page_url": page.url}
                        )

                # ExLibris / Alma links
                for m in re.finditer(
                    r'href=["\']([^"\']*exlibrisgroup[^"\']+)["\']',
                    html, re.I,
                ):
                    url = m.group(1)
                    if url.startswith("http"):
                        links.append(
                            {"title": "", "url": url, "page_url": page.url}
                        )

                log.info(f"    Found {len(links)} link(s) in page HTML.")

                # If nothing found, try Playwright element selectors
                if not links:
                    log.info(
                        "    No links in HTML — trying Playwright selectors…"
                    )
                    for sel in [
                        'a[href*="leganto"]',
                        'a[href*="exlibrisgroup"]',
                        'a[href*="ezp-prod"]',
                        'a[href$=".pdf"]',
                        'a:has-text("Full text")',
                        'a:has-text("Full Text")',
                        'a:has-text("PDF")',
                        'a:has-text("Download")',
                    ]:
                        try:
                            for el in page.query_selector_all(sel):
                                href = el.get_attribute("href") or ""
                                text = (el.inner_text() or "").strip()
                                if href and not href.startswith("javascript"):
                                    links.append({
                                        "title":    text,
                                        "url":      urljoin(page.url, href),
                                        "page_url": page.url,
                                    })
                        except Exception:
                            pass
                    log.info(
                        f"    Found {len(links)} link(s) via selectors."
                    )

            except Exception as exc:
                log.warning(f"    Page content error: {exc}")

        finally:
            try:
                page.remove_listener("framenavigated", on_frame_nav)
            except Exception:
                pass

        # Deduplicate
        seen: set[str] = set()
        return [
            lnk for lnk in links
            if not (lnk["url"] in seen or seen.add(lnk["url"]))  # type: ignore
        ]

    def download_reading(
        self,
        item:     dict,
        dest_dir: Path,
        dedup:    DedupIndex,
        dry_run:  bool = False,
    ) -> bool:
        url   = item["url"]
        title = item.get("title", "")
        page  = self._page

        existing = dedup.known_url(url)
        if existing:
            try:
                rel = Path(existing).relative_to(DOWNLOAD_DIR.resolve())
            except ValueError:
                rel = Path(existing)
            log.info(
                f"    – (URL dup)  {_guess_filename(url, title)}  →  {rel}"
            )
            if not dry_run:
                _leave_duplicate_note(
                    dest_dir, _guess_filename(url, title), existing, url
                )
            return True

        if dry_run:
            log.info(f"    ~ (dry-run)  {url[:90]}")
            return True

        log.info(f"    ↓ {url[:90]}")
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Strategy 1 — auto-download event
        _dl: dict = {}

        def _on_dl(dl):
            _dl["dl"] = dl

        page.on("download", _on_dl)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=35_000)
        except Exception:
            pass
        finally:
            time.sleep(2)
            page.remove_listener("download", _on_dl)

        if "dl" in _dl:
            fname = _sanitize(
                _dl["dl"].suggested_filename or _guess_filename(url, title)
            )
            dest = dest_dir / fname
            _dl["dl"].save_as(str(dest))
            dedup.record(url, dest)
            log.info(f"    ✓  {fname}")
            return True

        if self._is_login():
            self._handle_auth()

        # Strategy 2 — page is a PDF
        final_url = page.url
        try:
            is_pdf = page.evaluate(
                "() => document.contentType === 'application/pdf' || "
                "window.location.pathname.toLowerCase().endsWith('.pdf')"
            )
        except Exception:
            is_pdf = final_url.lower().rstrip("?&").endswith(".pdf")

        if is_pdf:
            fname = _guess_filename(final_url, title, ".pdf")
            dest  = dest_dir / fname
            if dest.exists():
                dedup.record(url, dest)
                return True
            sess = requests.Session()
            for c in self._ctx.cookies():
                try:
                    sess.cookies.set(
                        c["name"], c["value"],
                        domain=c.get("domain", ""),
                        path=c.get("path", "/"),
                    )
                except Exception:
                    pass
            try:
                r = sess.get(
                    final_url, stream=True, timeout=60,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                r.raise_for_status()
                with open(dest, "wb") as fh:
                    for chunk in r.iter_content(CHUNK_SIZE):
                        fh.write(chunk)
                dedup.record(url, dest)
                log.info(f"    ✓  {fname}")
                return True
            except Exception as exc:
                log.warning(f"    ✗  {exc}")
                return False

        # Strategy 3 — click a full text / PDF link
        for sel in [
            'a:has-text("Full text")',
            'a:has-text("Full Text")',
            'a:has-text("Download PDF")',
            'a:has-text("PDF")',
            'a[href*="ezp-prod"]',
            'a[href*="/doi/pdf"]',
            'a[href$=".pdf"]',
        ]:
            try:
                el = page.query_selector(sel)
                if el:
                    href = el.get_attribute("href") or ""
                    if href and not href.startswith("javascript"):
                        abs_href = urljoin(page.url, href)
                        fname    = _guess_filename(abs_href, title, ".pdf")
                        dest     = dest_dir / fname
                        if dest.exists():
                            dedup.record(url, dest)
                            return True
                        _dl2: dict = {}

                        def _on_dl2(dl):
                            _dl2["dl"] = dl

                        page.on("download", _on_dl2)
                        try:
                            el.click()
                            time.sleep(2.5)
                        except Exception:
                            pass
                        finally:
                            page.remove_listener("download", _on_dl2)
                        if "dl" in _dl2:
                            fname2 = _sanitize(
                                _dl2["dl"].suggested_filename or fname
                            )
                            dest2 = dest_dir / fname2
                            _dl2["dl"].save_as(str(dest2))
                            dedup.record(url, dest2)
                            log.info(f"    ✓  {fname2}")
                            return True
            except Exception:
                pass

        # Strategy 4 — render page as PDF
        slug  = _sanitize(title or _guess_filename(final_url, "", ""))
        fname = f"reading_{slug}.pdf"
        dest  = dest_dir / fname
        if dest.exists():
            dedup.record(url, dest)
            return True
        try:
            page.pdf(path=str(dest), format="A4", print_background=True)
            dedup.record(url, dest)
            log.info(f"    ✓  {fname}  (rendered as PDF)")
            return True
        except Exception as exc:
            log.warning(f"    ✗  Could not render: {exc}")

        log.warning("    ✗  Could not download reading")
        return False

    def get_cookies(self) -> list[dict]:
        return self._ctx.cookies()


# ──────────────────────────  COURSE PROCESSING  ───────────────────────────────

def process_course(
    course:  dict,
    browser: ReservesBrowser,
    dedup:   DedupIndex,
    dry_run: bool = False,
) -> dict[str, int]:
    course_id   = course["id"]
    course_name = _sanitize(course.get("name") or f"course_{course_id}")
    term        = _term_label(course)
    dest_dir    = DOWNLOAD_DIR / term / course_name / "library_reserves"

    log.info(f"\n{'─' * 70}")
    log.info(f"  📖  {term}  /  {course_name}")
    log.info(f"{'─' * 70}")

    counts = {"downloaded": 0, "duplicate": 0, "failed": 0}

    reserves = find_reserves_urls_for_course(course_id)
    if not reserves:
        log.info("    No library reserves found.")
        return counts

    log.info(f"    Found {len(reserves)} reserves link(s).")
    cookies = browser.get_cookies()

    for res in reserves:
        log.info(f"\n    [{res['label']}]  {res['url']}")
        reading_links = browser.get_reading_links(res["url"])
        cookies       = browser.get_cookies()
        log.info(f"    Found {len(reading_links)} reading(s).")

        if not reading_links:
            log.info("    (reading list may be empty or still loading)")
            continue

        for reading in reading_links:
            url   = reading["url"]
            title = reading.get("title", "")

            if any(url.startswith(p) for p in ("javascript:", "mailto:", "#")):
                continue

            existing = dedup.known_url(url)
            if existing:
                try:
                    rel = Path(existing).relative_to(DOWNLOAD_DIR.resolve())
                except ValueError:
                    rel = Path(existing)
                log.info(
                    f"    – (URL dup)  {_guess_filename(url, title)}"
                    f"  →  {rel}"
                )
                if not dry_run:
                    _leave_duplicate_note(
                        dest_dir,
                        _guess_filename(url, title),
                        existing,
                        url,
                    )
                counts["duplicate"] += 1
                continue

            # Use browser for Leganto / EZProxy / DOI links
            if any(
                s in url.lower()
                for s in [
                    "leganto", "exlibrisgroup", "ezp-prod",
                    "ezproxy", "hollis", "alma.", "doi.org",
                ]
            ):
                ok = browser.download_reading(
                    reading, dest_dir, dedup, dry_run
                )
            else:
                filename = _guess_filename(url, title)
                ok = download_file(
                    url, dest_dir, filename, cookies, dedup, dry_run
                )
                if not ok:
                    log.info("    Retrying via EZProxy…")
                    ok = download_file(
                        _build_proxy_url(url), dest_dir, filename,
                        cookies, dedup, dry_run,
                    )
                if not ok:
                    ok = browser.download_reading(
                        reading, dest_dir, dedup, dry_run
                    )

            note = dest_dir / (
                f"[DUPLICATE] "
                f"{_sanitize(_guess_filename(url, title))}.txt"
            )
            if ok and note.exists():
                counts["duplicate"] += 1
            elif ok:
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
    parser.add_argument("--dir",          metavar="PATH",
                        default=str(DOWNLOAD_DIR))
    parser.add_argument("--skip-ongoing", action="store_true")
    parser.add_argument("--course",       metavar="NAME")
    args = parser.parse_args()

    DOWNLOAD_DIR = Path(args.dir)

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
    log.info("  📖  Library Reserves Downloader  (framenavigated fix)")
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
    log.info(f"Processing {len(courses)} course(s).\n")

    totals = {"downloaded": 0, "duplicate": 0, "failed": 0}

    with ReservesBrowser() as browser:
        for course in courses:
            if not course.get("name"):
                continue
            try:
                counts = process_course(
                    course, browser, dedup, args.dry_run
                )
                for k in totals:
                    totals[k] += counts[k]
            except Exception as exc:
                log.error(f"  ✗  Error on '{course.get('name')}': {exc}")

    log.info(f"\n{'═' * 70}")
    log.info("  ✅  FINISHED")
    log.info(f"  Downloaded         : {totals['downloaded']}")
    log.info(f"  Duplicates skipped : {totals['duplicate']}")
    log.info(f"  Failed             : {totals['failed']}")
    log.info(f"  Dedup index        : {DEDUP_INDEX_FILE}")
    if not args.dry_run:
        log.info(f"  Saved to           : {DOWNLOAD_DIR.resolve()}")
    log.info("═" * 70)


if __name__ == "__main__":
    main()