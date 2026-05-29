#!/usr/bin/env python3
"""
external_downloader.py
======================
Companion to canvas_downloader.py.
Finds every external link in your Canvas courses — JSTOR articles,
Google Drive PDFs, linked readings, etc. — and downloads them using
an authenticated browser window.

Because many Harvard resources require HarvardKey / Shibboleth login,
a visible Chrome window is opened.  Sign in once per service and the
script handles the rest.  Login cookies are saved in ./browser_profile/
so you won't need to sign in again on future runs.

QUICK START
-----------
  pip install requests playwright tqdm
  playwright install chromium

  python external_downloader.py --dry-run    # safe preview
  python external_downloader.py              # download everything

FLAGS
-----
  --dry-run          Preview without downloading anything.
  --dir     PATH     Output root  (default: ./canvas_downloads).
  --manifest-only    Collect and save all URLs to JSON, then stop.
  --course   NAME    Only process courses whose name contains NAME.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

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

CANVAS_BASE_URL = "https://canvas.harvard.edu"
COOKIES: list[dict] = []          # filled in at startup by canvas_auth.py
DOWNLOAD_DIR    = Path("./canvas_downloads")
BROWSER_PROFILE = Path("./browser_profile")
REQUEST_DELAY   = 0.20
CHUNK_SIZE      = 65_536

_SKIP_RE = re.compile(
    r"^(mailto:|tel:|javascript:)"
    r"|canvas\.harvard\.edu"
    r"|youtube\.com/watch|youtu\.be/"
    r"|vimeo\.com"
    r"|(twitter|x|facebook|instagram|linkedin)\.com"
    r"|wikipedia\.org"
    r"|zoom\.us",
    re.I,
)

_LOGIN_RE = re.compile(
    r"login|signin|sign.in|shibboleth|harvardkey|cas\.harvard"
    r"|accounts\.google\.com|login\.microsoftonline|/oauth|/saml|/auth/",
    re.I,
)


# ──────────────────────────────────  LOGGING  ─────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("external_downloader.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ──────────────────────────  HTML LINK EXTRACTION  ────────────────────────────

class _AnchorParser(HTMLParser):
    """Minimal HTML parser that collects (href, visible_text) pairs."""

    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._buf  = ""
        self._open = False

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for name, val in attrs:
                if name == "href" and val:
                    self._href = val.strip()
                    self._buf  = ""
                    self._open = True
                    break

    def handle_endtag(self, tag):
        if tag == "a" and self._open:
            self.links.append((self._href, self._buf.strip()))
            self._open = False

    def handle_data(self, data):
        if self._open:
            self._buf += data


def _extract_links(html: str, base_url: str = "") -> list[tuple[str, str]]:
    """Return [(absolute_url, link_text), …] for every <a href> in html."""
    parser = _AnchorParser()
    try:
        parser.feed(html or "")
    except Exception:
        return []
    out = []
    for href, text in parser.links:
        href = href.strip()
        if not href:
            continue
        if base_url and not href.startswith(
            ("http://", "https://", "mailto:", "tel:", "javascript:")
        ):
            href = urljoin(base_url, href)
        out.append((href, text))
    return out


# ─────────────────────────────  CANVAS API  ───────────────────────────────────

def _api_get(url: str, params: dict | None = None) -> requests.Response:
    """Session-cookie-authenticated GET with automatic retry."""
    from canvas_auth import cookies_for_domain
    cookie_str = cookies_for_domain(COOKIES, CANVAS_BASE_URL)
    headers    = {}
    if cookie_str:
        headers["Cookie"] = cookie_str

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
        r    = _api_get(next_url, next_p)
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


def _fetch_courses() -> list[dict]:
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
    out:  list[dict] = []
    for c in active + done:
        if c["id"] not in seen:
            seen.add(c["id"])
            out.append(c)
    return out


# ──────────────────────────  URL COLLECTION  ──────────────────────────────────

def _term_label(course: dict) -> str:
    name = (course.get("term") or {}).get("name", "")
    if name:
        return _sanitize(name)
    d = course.get("start_at") or course.get("created_at") or ""
    if len(d) >= 7:
        m      = int(d[5:7])
        season = "Spring" if m <= 5 else ("Summer" if m <= 7 else "Fall")
        return f"{season} {d[:4]}"
    return "Unknown Term"


def _sanitize(s: str, n: int = 160) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s).strip(". ")
    return s[:n] or "unnamed"


def collect_urls_for_course(course: dict) -> list[dict]:
    """
    Harvest all external URLs from a single course.
    Sources: Syllabus, Module ExternalUrl items, Pages, Assignments.
    """
    cid   = course["id"]
    cname = course.get("name", f"course_{cid}")
    term  = _term_label(course)
    items: list[dict] = []

    def add(href: str, text: str, src_type: str, src_name: str):
        href = href.strip()
        if not href or _SKIP_RE.search(href):
            return
        items.append({
            "url":         href,
            "text":        (text or href).strip(),
            "course":      cname,
            "term":        term,
            "source_type": src_type,
            "source_name": src_name,
        })

    # ── Syllabus ───────────────────────────────────────────────────────────────
    for href, text in _extract_links(course.get("syllabus_body") or ""):
        add(href, text, "syllabus", "Syllabus")

    # ── Modules ────────────────────────────────────────────────────────────────
    try:
        for mod in _paginate(
            f"{CANVAS_BASE_URL}/api/v1/courses/{cid}/modules",
            {"include[]": "items"},
        ):
            for item in mod.get("items", []):
                if item.get("type") == "ExternalUrl":
                    add(
                        item.get("external_url", ""),
                        item.get("title", ""),
                        "module",
                        mod.get("name", ""),
                    )
    except requests.HTTPError:
        pass

    # ── Pages ──────────────────────────────────────────────────────────────────
    try:
        for stub in _paginate(f"{CANVAS_BASE_URL}/api/v1/courses/{cid}/pages"):
            try:
                detail = _api_get(
                    f"{CANVAS_BASE_URL}/api/v1/courses/{cid}/pages/{stub['url']}"
                ).json()
                time.sleep(REQUEST_DELAY)
                for href, text in _extract_links(detail.get("body") or ""):
                    add(href, text, "page", stub.get("title", ""))
            except (requests.HTTPError, KeyError):
                pass
    except requests.HTTPError:
        pass

    # ── Assignments ────────────────────────────────────────────────────────────
    try:
        for asgn in _paginate(
            f"{CANVAS_BASE_URL}/api/v1/courses/{cid}/assignments"
        ):
            for href, text in _extract_links(asgn.get("description") or ""):
                add(href, text, "assignment", asgn.get("name", ""))
    except requests.HTTPError:
        pass

    # Deduplicate within this course
    seen: set[str] = set()
    unique = []
    for it in items:
        if it["url"] not in seen:
            seen.add(it["url"])
            unique.append(it)
    return unique


# ──────────────────────────────  UTILITIES  ───────────────────────────────────

def _guess_filename(url: str, text: str, default_ext: str = ".pdf") -> str:
    path = unquote(urlparse(url).path)
    base = path.rstrip("/").rsplit("/", 1)[-1]
    if "." in base:
        suffix = base.rsplit(".", 1)[-1]
        if 1 < len(suffix) <= 5:
            return _sanitize(base)
    if text and len(text.strip()) > 2:
        slug = re.sub(r"\s+", "_", text.strip())
        return _sanitize(slug) + default_ext
    return _sanitize(base or "file") + default_ext


def _download_bytes(
    url:     str,
    dest:    Path,
    cookies: list[dict],
    dry_run: bool = False,
) -> bool:
    if dry_run:
        log.info(f"    ~ (dry-run) {dest.name}")
        return True

    session = requests.Session()
    for c in cookies:
        try:
            session.cookies.set(
                c["name"], c["value"],
                domain=c.get("domain", ""),
                path=c.get("path", "/"),
            )
        except Exception:
            pass

    try:
        r = session.get(
            url, stream=True, timeout=60,
            headers={"User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )},
        )
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as fh:
            if HAS_TQDM and total:
                with tqdm(
                    total=total, unit="B", unit_scale=True, unit_divisor=1024,
                    desc=f"    ↓ {dest.name[:50]}", leave=False,
                ) as bar:
                    for chunk in r.iter_content(CHUNK_SIZE):
                        fh.write(chunk)
                        bar.update(len(chunk))
            else:
                for chunk in r.iter_content(CHUNK_SIZE):
                    fh.write(chunk)
        return True
    except Exception as exc:
        log.warning(f"    ✗ FAILED: {exc}")
        if dest.exists():
            dest.unlink()
        return False


# ─────────────────────────────  BROWSER  ──────────────────────────────────────

def _is_login_page(page) -> bool:
    try:
        return bool(
            _LOGIN_RE.search(page.url) or _LOGIN_RE.search(page.title())
        )
    except Exception:
        return False


def _google_drive_direct_url(url: str) -> str | None:
    for pat in [
        r"drive\.google\.com/file/d/([a-zA-Z0-9_-]+)",
        r"drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)",
        r"[?&]id=([a-zA-Z0-9_-]{25,})",
    ]:
        m = re.search(pat, url)
        if m:
            return f"https://drive.google.com/uc?id={m.group(1)}&export=download"
    return None


class BrowserDownloader:
    """
    Playwright-backed downloader with a persistent browser profile.
    Sign in to each service once — cookies are reused automatically
    on every subsequent URL and every future run of the script.
    """

    def __init__(self, dry_run: bool = False):
        self.dry_run  = dry_run
        self._pw      = None
        self._ctx     = None
        self._page    = None
        self._authed: set[str] = set()

    def __enter__(self):
        if not HAS_PLAYWRIGHT:
            raise RuntimeError(
                "playwright is not installed.\n"
                "Run:  pip install playwright && playwright install chromium"
            )
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

    def _handle_auth(self):
        page   = self._page
        domain = urlparse(page.url).netloc

        if domain in self._authed:
            return

        log.info(f"\n  {'═' * 62}")
        log.info(f"  🔐  Login required  —  {domain}")
        log.info(f"      The browser window is open.")
        log.info(f"      Please sign in, then come back here and press ENTER.")
        log.info(f"  {'═' * 62}")
        input("  [Press ENTER once you're signed in and on the content page] ")

        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except PWTimeout:
            pass

        self._authed.add(domain)
        self._authed.add(urlparse(page.url).netloc)
        log.info(f"  ✅  Authenticated  —  won't ask again for {domain}\n")

    @staticmethod
    def _find_pdf_link(page) -> str | None:
        selectors = [
            'a[data-qa="download-pdf"]',
            'a[href*="/stable/pdf/"]',
            'a[href*="/doi/pdf/"]',
            '.pdf-download-link a',
            'a[href*="pdf"][href*="download"]',
            'a[href$=".pdf"]',
            'a:has-text("Download PDF")',
            'a:has-text("Download")',
            'a:has-text("PDF")',
        ]
        for sel in selectors:
            try:
                el = page.query_selector(sel)
                if el:
                    href = el.get_attribute("href")
                    if href:
                        return urljoin(page.url, href)
            except Exception:
                pass
        return None

    def download(self, item: dict, dest_dir: Path) -> bool:
        """
        Download one external URL.  Tries (in order):
          1. Auto-triggered browser download event
          2. Page is itself a PDF
          3. "Download PDF" button on the page
          4. Print page to PDF via Playwright renderer
          5. Give up — log URL for manual follow-up
        """
        url  = item["url"]
        text = item.get("text", "")
        page = self._page

        log.info(f"\n  URL : {url}")
        if text and text != url:
            log.info(f"  Text: {text[:100]}")

        # ── Google Drive shortcut ─────────────────────────────────────────────
        gd_url = _google_drive_direct_url(url)
        if gd_url:
            log.info("       (Google Drive → direct download URL)")
            url = gd_url

        if self.dry_run:
            log.info("       (dry-run — not downloaded)")
            return True

        # ── Strategy 1: auto-download event ──────────────────────────────────
        _triggered: dict = {}

        def _on_download(dl):
            _triggered["dl"] = dl

        page.on("download", _on_download)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except PWTimeout:
            log.warning("       (page load timed out — continuing)")
        except Exception as exc:
            log.warning(f"       Navigation error: {exc}")
        finally:
            time.sleep(1.5)
            page.remove_listener("download", _on_download)

        if "dl" in _triggered:
            dl    = _triggered["dl"]
            fname = _sanitize(dl.suggested_filename or _guess_filename(url, text))
            dest  = dest_dir / fname
            dest_dir.mkdir(parents=True, exist_ok=True)
            dl.save_as(str(dest))
            log.info(f"    ✓  {fname}  (auto-downloaded)")
            return True

        # ── Handle login wall ─────────────────────────────────────────────────
        if _is_login_page(page):
            self._handle_auth()

        # ── Strategy 2: page is a PDF ─────────────────────────────────────────
        final_url = page.url
        try:
            is_pdf = page.evaluate(
                """() =>
                    document.contentType === 'application/pdf' ||
                    !!document.querySelector('embed[type="application/pdf"]') ||
                    window.location.pathname.toLowerCase().endsWith('.pdf')
                """
            )
        except Exception:
            is_pdf = final_url.lower().split("?")[0].endswith(".pdf")

        if is_pdf:
            fname = _guess_filename(final_url, text, ".pdf")
            dest  = dest_dir / fname
            if dest.exists():
                log.info(f"    – (exists)  {fname}")
                return True
            ok = _download_bytes(final_url, dest, self._ctx.cookies())
            if ok:
                log.info(f"    ✓  {fname}  (PDF viewer → direct download)")
            return ok

        # ── Strategy 3: PDF download button ──────────────────────────────────
        pdf_link = self._find_pdf_link(page)
        if pdf_link:
            fname = _guess_filename(pdf_link, text, ".pdf")
            dest  = dest_dir / fname
            if dest.exists():
                log.info(f"    – (exists)  {fname}")
                return True

            _triggered2: dict = {}

            def _on_dl2(dl):
                _triggered2["dl"] = dl

            page.on("download", _on_dl2)
            try:
                for sel in [
                    'a[data-qa="download-pdf"]',
                    'a:has-text("Download PDF")',
                    f'a[href="{pdf_link}"]',
                    'a:has-text("PDF")',
                ]:
                    el = page.query_selector(sel)
                    if el:
                        el.click()
                        time.sleep(2.5)
                        break
            except Exception:
                pass
            finally:
                page.remove_listener("download", _on_dl2)

            if "dl" in _triggered2:
                dl    = _triggered2["dl"]
                fname = _sanitize(dl.suggested_filename or fname)
                dest  = dest_dir / fname
                dest_dir.mkdir(parents=True, exist_ok=True)
                dl.save_as(str(dest))
                log.info(f"    ✓  {fname}  (clicked download button)")
                return True

            ok = _download_bytes(pdf_link, dest, self._ctx.cookies())
            if ok:
                log.info(f"    ✓  {fname}  (direct link download)")
            return ok

        # ── Strategy 4: print page to PDF ────────────────────────────────────
        slug  = _guess_filename(final_url, text, "")
        fname = f"webpage_{slug}.pdf"
        dest  = dest_dir / fname
        if dest.exists():
            log.info(f"    – (exists)  {fname}")
            return True
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            page.pdf(path=str(dest), format="A4", print_background=True)
            log.info(f"    ✓  {fname}  (rendered page as PDF)")
            return True
        except Exception as exc:
            log.warning(f"    ✗  Could not render as PDF: {exc}")

        # ── Strategy 5: give up ───────────────────────────────────────────────
        fallback = dest_dir.parent / "_could_not_download.txt"
        with open(fallback, "a", encoding="utf-8") as fh:
            fh.write(f"URL :  {url}\nText:  {text}\n\n")
        log.info(f"    ✗  Saved to {fallback.name} for manual follow-up.")
        return False


# ──────────────────────────────────  MAIN  ────────────────────────────────────

def main() -> None:
    global DOWNLOAD_DIR, COOKIES

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run",       action="store_true",
                        help="Preview without downloading.")
    parser.add_argument("--dir",           metavar="PATH",
                        default=str(DOWNLOAD_DIR),
                        help="Output root directory.")
    parser.add_argument("--manifest-only", action="store_true",
                        help="Collect URLs and save manifest; skip downloading.")
    parser.add_argument("--course",        metavar="NAME",
                        help="Only process courses whose name contains NAME.")
    args = parser.parse_args()

    DOWNLOAD_DIR = Path(args.dir)

    # ── Browser login ─────────────────────────────────────────────────────────
    from canvas_auth import get_cookies
    COOKIES = get_cookies()
    if not COOKIES:
        log.error("  ✗  Could not get Canvas session. Exiting.")
        sys.exit(1)

    if not HAS_PLAYWRIGHT and not args.manifest_only:
        log.error(
            "\n[ERROR] playwright is not installed.\n"
            "  pip install playwright && playwright install chromium\n"
        )
        sys.exit(1)

    # ── Banner ────────────────────────────────────────────────────────────────
    log.info("═" * 66)
    log.info("  🌐  Canvas External URL Downloader")
    log.info("═" * 66)
    if args.dry_run:
        log.info("  DRY-RUN — nothing will be written to disk.\n")

    # ── Collect URLs ──────────────────────────────────────────────────────────
    log.info("\nFetching courses …")
    courses = _fetch_courses()
    if args.course:
        courses = [
            c for c in courses
            if args.course.lower() in (c.get("name") or "").lower()
        ]
    log.info(f"Found {len(courses)} course(s).\n")

    all_items: list[dict] = []
    for course in courses:
        if not course.get("name"):
            continue
        log.info(f"  Scanning: {course['name']}")
        items = collect_urls_for_course(course)
        log.info(f"    → {len(items)} external URL(s) found")
        all_items.extend(items)

    log.info(f"\nTotal external URLs: {len(all_items)}")

    # Always save a manifest
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    manifest = DOWNLOAD_DIR / "external_urls_manifest.json"
    with open(manifest, "w", encoding="utf-8") as fh:
        json.dump(all_items, fh, indent=2, ensure_ascii=False)
    log.info(f"Manifest saved → {manifest}")

    if args.manifest_only:
        log.info("\n--manifest-only set — stopping before download.")
        return

    if args.dry_run:
        return

    # ── Download ──────────────────────────────────────────────────────────────
    by_course: dict[tuple, list[dict]] = defaultdict(list)
    for item in all_items:
        by_course[(item["term"], item["course"])].append(item)

    counts = {"ok": 0, "fail": 0}

    with BrowserDownloader(dry_run=args.dry_run) as dl:
        for (term, course_name), items in by_course.items():
            log.info(f"\n{'─' * 66}")
            log.info(f"  {term}  /  {course_name}  ({len(items)} URL(s))")
            log.info(f"{'─' * 66}")

            dest_dir = (
                DOWNLOAD_DIR
                / _sanitize(term)
                / _sanitize(course_name)
                / "external_readings"
            )

            for item in items:
                ok = dl.download(item, dest_dir)
                counts["ok" if ok else "fail"] += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    log.info(f"\n{'═' * 66}")
    log.info("  ✅  FINISHED")
    log.info(f"  Downloaded : {counts['ok']}")
    log.info(f"  Failed     : {counts['fail']}")
    log.info(f"  Saved to   : {DOWNLOAD_DIR.resolve()}")
    log.info(f"  Manifest   : {manifest}")
    log.info(f"  Log        : external_downloader.log")
    log.info("═" * 66)


if __name__ == "__main__":
    main()