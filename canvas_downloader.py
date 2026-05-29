#!/usr/bin/env python3
"""
canvas_downloader.py
====================
Archives ALL course content — every file type, Canvas media recordings,
and embedded syllabi — from every Canvas course you have taken.
Files are organised by semester → course → file-type subfolder.

⚠  DISK SPACE WARNING
    A full four years of video recordings can easily exceed 100 GB.
    Run with --dry-run first to see what you're getting into.
    Use --skip-videos or --max-size MB to limit downloads.

QUICK START
-----------
  pip install requests tqdm playwright
  playwright install chromium
  python canvas_downloader.py --dry-run     # safe preview
  python canvas_downloader.py               # download everything

FLAGS
-----
  --dry-run           List what would be downloaded; save nothing.
  --dir  PATH         Output root directory  (default: ./canvas_downloads)
  --no-modules        Skip Module scanning  (faster; may miss some files).
  --no-media          Skip Canvas media / video recordings.
  --skip-videos       Skip all video files (.mp4, .mov, … and media objects).
  --skip-audio        Skip all audio files (.mp3, .m4a, …).
  --skip-ongoing      Skip courses whose term is labelled "Ongoing".
  --max-size  MB      Skip any single file larger than this many MB.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from pathlib import Path

import requests

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False


# ──────────────────────────────  CONFIGURATION  ───────────────────────────────

CANVAS_BASE_URL = "https://canvas.harvard.edu"
COOKIES: list[dict] = []          # filled in at startup by canvas_auth.py
DOWNLOAD_DIR    = Path("./canvas_downloads")
REQUEST_DELAY   = 0.20            # seconds between API calls — be polite
CHUNK_SIZE      = 64 * 1024       # 64 KB streaming chunks


# ───────────────────────────  FILE-TYPE MAPPING  ──────────────────────────────

EXT_CATEGORY: dict[str, str] = {
    # ── Video ──────────────────────────────────────────────────────────────────
    ".mp4":  "videos", ".mov":  "videos", ".avi":  "videos",
    ".mkv":  "videos", ".wmv":  "videos", ".m4v":  "videos",
    ".webm": "videos", ".flv":  "videos", ".mpg":  "videos",
    ".mpeg": "videos", ".3gp":  "videos", ".ogv":  "videos",
    # ── Audio ──────────────────────────────────────────────────────────────────
    ".mp3":  "audio",  ".wav":  "audio",  ".aac":  "audio",
    ".ogg":  "audio",  ".m4a":  "audio",  ".flac": "audio",
    ".wma":  "audio",  ".aiff": "audio",  ".opus": "audio",
    # ── Documents ──────────────────────────────────────────────────────────────
    ".pdf":    "readings",
    ".ppt":    "slides",       ".pptx":    "slides",    ".key":     "slides",
    ".doc":    "documents",    ".docx":    "documents", ".odt":     "documents",
    ".rtf":    "documents",    ".txt":     "documents", ".pages":   "documents",
    ".xls":    "spreadsheets", ".xlsx":    "spreadsheets",
    ".ods":    "spreadsheets", ".csv":     "spreadsheets",
    ".numbers":"spreadsheets",
    # ── Images ─────────────────────────────────────────────────────────────────
    ".jpg":  "images", ".jpeg": "images", ".png":  "images",
    ".gif":  "images", ".svg":  "images", ".bmp":  "images",
    ".tiff": "images", ".tif":  "images", ".heic": "images",
    # ── Code / Notebooks ───────────────────────────────────────────────────────
    ".py":   "code", ".r":    "code", ".ipynb": "code",
    ".m":    "code", ".java": "code", ".cpp":   "code",
    ".c":    "code", ".js":   "code", ".ts":    "code",
    ".go":   "code", ".rb":   "code", ".sh":    "code",
    ".sql":  "code", ".json": "code", ".xml":   "code",
    ".yaml": "code", ".yml":  "code", ".html":  "code",
    ".css":  "code", ".md":   "code",
    # ── Archives ───────────────────────────────────────────────────────────────
    ".zip": "archives", ".tar": "archives", ".gz":  "archives",
    ".rar": "archives", ".7z":  "archives", ".bz2": "archives",
    ".xz":  "archives",
}

_VIDEO_EXTS = {ext for ext, cat in EXT_CATEGORY.items() if cat == "videos"}
_AUDIO_EXTS = {ext for ext, cat in EXT_CATEGORY.items() if cat == "audio"}


# ───────────────────────────────  GLOBALS  ────────────────────────────────────

g_skip_videos:  bool         = False
g_skip_audio:   bool         = False
g_skip_ongoing: bool         = False
g_max_size_mb:  float | None = None


# ─────────────────────────────  LOGGING  ──────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("canvas_downloader.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ────────────────────────────  HTTP HELPERS  ──────────────────────────────────

def _request(method: str, url: str, **kwargs) -> requests.Response:
    """Session-cookie-authenticated request with automatic retry."""
    from canvas_auth import cookies_for_domain
    headers    = kwargs.pop("headers", {})
    cookie_str = cookies_for_domain(COOKIES, CANVAS_BASE_URL)
    if cookie_str:
        headers["Cookie"] = cookie_str

    for attempt in range(4):
        try:
            resp = requests.request(
                method, url, headers=headers, timeout=120, **kwargs
            )
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 15))
                log.warning(f"  ⚠  Rate limited — sleeping {wait}s …")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)

    raise RuntimeError(f"Gave up after 4 attempts: {url}")


def api_get(url: str, params: dict | None = None) -> requests.Response:
    return _request("GET", url, params=params or {})


def paginate(url: str, params: dict | None = None) -> list:
    """Follow Canvas's Link-header pagination and return all results as a flat list."""
    params      = {**(params or {}), "per_page": 100}
    results: list        = []
    next_url: str | None = url
    next_params          = params

    while next_url:
        resp = api_get(next_url, next_params)
        body = resp.json()

        if isinstance(body, list):
            results.extend(body)
        elif isinstance(body, dict):
            for v in body.values():
                if isinstance(v, list):
                    results.extend(v)
                    break

        next_url, next_params = None, {}
        for segment in resp.headers.get("Link", "").split(","):
            if 'rel="next"' in segment:
                next_url = segment.split(";")[0].strip().strip("<>")
                break

        time.sleep(REQUEST_DELAY)

    return results


# ───────────────────────────  CANVAS FETCHERS  ────────────────────────────────

def fetch_all_courses() -> list[dict]:
    """Return every course (current + concluded) the authenticated user belongs to."""
    common = {
        "include[]": ["term", "syllabus_body"],
        "state[]":   ["available", "completed"],
    }
    active    = paginate(f"{CANVAS_BASE_URL}/api/v1/courses",
                         {**common, "enrollment_state": "active"})
    concluded = paginate(f"{CANVAS_BASE_URL}/api/v1/courses",
                         {**common, "enrollment_state": "completed"})

    seen: set[int] = set()
    courses: list[dict] = []
    for c in active + concluded:
        if c["id"] not in seen:
            seen.add(c["id"])
            courses.append(c)
    return courses


def fetch_course_files(course_id: int) -> list[dict]:
    """Return ALL files uploaded to the course Files section."""
    try:
        return paginate(f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/files")
    except requests.HTTPError as exc:
        code = exc.response.status_code
        if code in (401, 403, 404):
            log.info("    (Files section not accessible for this course)")
            return []
        raise


def fetch_module_files(course_id: int, known_ids: set) -> list[dict]:
    """Walk Modules → Items and resolve File references not already captured."""
    extra: list[dict] = []
    try:
        modules = paginate(
            f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/modules",
            {"include[]": "items"},
        )
    except requests.HTTPError:
        return extra

    for module in modules:
        for item in module.get("items", []):
            if item.get("type") != "File":
                continue
            file_id = item.get("content_id")
            if not file_id or file_id in known_ids:
                continue
            try:
                fobj = api_get(
                    f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/files/{file_id}"
                ).json()
                known_ids.add(file_id)
                extra.append(fobj)
                time.sleep(REQUEST_DELAY)
            except requests.HTTPError:
                pass

    return extra


def fetch_media_objects(course_id: int) -> list[dict]:
    """
    Fetch Canvas media recordings.
    Normalised into the same shape as Files API objects so the rest
    of the pipeline can treat them uniformly.
    """
    try:
        raw = paginate(
            f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/media_objects"
        )
    except requests.HTTPError:
        return []

    results: list[dict] = []
    for obj in raw:
        media_id = obj.get("media_id") or str(obj.get("id", ""))
        title    = obj.get("title") or media_id or "media_recording"
        mtype    = (obj.get("media_type") or "video").lower()

        sources: list[dict] = obj.get("media_sources") or []

        if not sources:
            try:
                src_resp = api_get(
                    f"{CANVAS_BASE_URL}/api/v1/media_objects/{media_id}/media_sources"
                )
                sources = src_resp.json()
                time.sleep(REQUEST_DELAY)
            except requests.HTTPError:
                sources = []

        if not sources:
            log.info(f"    (no downloadable source found for media: {title})")
            continue

        best = max(
            sources,
            key=lambda s: int(s.get("bitrate", 0)),
            default=sources[0],
        )
        url = best.get("url")
        if not url:
            continue

        ext      = ".mp4" if "video" in mtype else ".m4a"
        filename = f"{sanitize(title)}{ext}"

        results.append({
            "id":           f"media_{media_id}",
            "display_name": filename,
            "url":          url,
            "size":         int(best.get("size") or 0),
            "content-type": best.get("content_type", f"{mtype}/mp4"),
            "_is_media":    True,
        })

    return results


# ──────────────────────────────  UTILITIES  ───────────────────────────────────

def sanitize(name: str, max_len: int = 180) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = name.strip(". ")
    return name[:max_len] or "unnamed"


def term_label(course: dict) -> str:
    term = course.get("term") or {}
    if term.get("name"):
        return sanitize(term["name"])
    date = course.get("start_at") or course.get("created_at") or ""
    if len(date) >= 7:
        month  = int(date[5:7])
        year   = date[:4]
        season = "Spring" if month <= 5 else ("Summer" if month <= 7 else "Fall")
        return f"{season} {year}"
    return "Unknown Term"


def fmt_size(n_bytes: int) -> str:
    if not n_bytes:
        return "? B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n_bytes < 1024:
            return f"{n_bytes:.1f} {unit}"
        n_bytes //= 1024
    return f"{n_bytes:.1f} PB"


def _ext(f: dict) -> str:
    name = f.get("display_name") or f.get("filename") or ""
    return Path(name).suffix.lower()


def is_video(f: dict) -> bool:
    ct = (f.get("content-type") or f.get("mime_class") or "").lower()
    return "video/" in ct or _ext(f) in _VIDEO_EXTS or bool(f.get("_is_media"))


def is_audio(f: dict) -> bool:
    ct = (f.get("content-type") or f.get("mime_class") or "").lower()
    return "audio/" in ct or _ext(f) in _AUDIO_EXTS


def should_skip(f: dict) -> tuple[bool, str]:
    if g_skip_videos and is_video(f):
        return True, "video skipped (--skip-videos)"
    if g_skip_audio and is_audio(f):
        return True, "audio skipped (--skip-audio)"
    if g_max_size_mb is not None:
        size  = f.get("size") or 0
        limit = g_max_size_mb * 1024 * 1024
        if size and size > limit:
            return True, f"too large ({fmt_size(size)} > {g_max_size_mb:.0f} MB)"
    return False, ""


def dest_subdir(f: dict) -> str:
    name = f.get("display_name") or f.get("filename") or ""
    if re.search(r"syllabus", name, re.IGNORECASE):
        return "syllabi"
    if f.get("_is_media"):
        ct = (f.get("content-type") or "").lower()
        return "audio" if "audio" in ct else "videos"
    return EXT_CATEGORY.get(_ext(f), "other")


# ──────────────────────────────  DOWNLOADER  ──────────────────────────────────

def download_file(
    url:        str,
    dest_dir:   Path,
    filename:   str,
    size_bytes: int  = 0,
    dry_run:    bool = False,
) -> bool:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest     = dest_dir / sanitize(filename)
    size_tag = f"  [{fmt_size(size_bytes)}]" if size_bytes else ""

    if dest.exists():
        log.info(f"    – (exists)   {dest.name}{size_tag}")
        return True

    if dry_run:
        log.info(f"    ~ (dry-run)  {dest}{size_tag}")
        return True

    try:
        resp  = _request("GET", url, stream=True)
        total = int(resp.headers.get("content-length", size_bytes or 0))

        with open(dest, "wb") as fh:
            if HAS_TQDM:
                with tqdm(
                    total=total if total else None,
                    unit="B", unit_scale=True, unit_divisor=1024,
                    desc=f"    ↓ {dest.name[:55]}",
                    leave=False, dynamic_ncols=True,
                ) as bar:
                    for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                        fh.write(chunk)
                        bar.update(len(chunk))
            else:
                for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                    fh.write(chunk)

        log.info(f"    ✓            {dest.name}{size_tag}")
        return True

    except Exception as exc:
        log.warning(f"    ✗ FAILED     {filename}  ({exc})")
        if dest.exists():
            dest.unlink()
        return False


def save_html_syllabus(
    html:         str,
    dest_dir:     Path,
    course_title: str,
    dry_run:      bool = False,
) -> None:
    if not html or not html.strip():
        return

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "syllabus.html"

    if dest.exists():
        log.info("    – (exists)   syllabus.html")
        return
    if dry_run:
        log.info(f"    ~ (dry-run)  {dest}")
        return

    page = (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        '  <meta charset="utf-8">\n'
        f"  <title>Syllabus – {course_title}</title>\n"
        "</head>\n<body>\n"
        f"{html}\n"
        "</body>\n</html>\n"
    )
    dest.write_text(page, encoding="utf-8")
    log.info("    ✓            syllabus.html")


# ─────────────────────────────  COURSE LOOP  ──────────────────────────────────

def process_course(
    course:       dict,
    dry_run:      bool = False,
    scan_modules: bool = True,
    scan_media:   bool = True,
) -> dict[str, int]:
    course_id   = course["id"]
    course_name = sanitize(course.get("name") or f"course_{course_id}")
    term        = term_label(course)
    course_dir  = DOWNLOAD_DIR / term / course_name

    log.info(f"\n{'─' * 70}")
    log.info(f"  📚  {term}  /  {course_name}")
    log.info(f"{'─' * 70}")

    counts = {"downloaded": 0, "skipped": 0, "failed": 0}

    # ── 1. Embedded HTML syllabus ─────────────────────────────────────────────
    save_html_syllabus(
        course.get("syllabus_body") or "",
        course_dir,
        course_name,
        dry_run=dry_run,
    )

    # ── 2. Files section ──────────────────────────────────────────────────────
    log.info("  Scanning Files …")
    files     = fetch_course_files(course_id)
    seen_ids: set = {f["id"] for f in files}

    # ── 3. Modules ────────────────────────────────────────────────────────────
    if scan_modules:
        log.info("  Scanning Modules …")
        files += fetch_module_files(course_id, seen_ids)

    # ── 4. Media recordings ───────────────────────────────────────────────────
    if scan_media:
        log.info("  Scanning Media recordings …")
        media = fetch_media_objects(course_id)
        if media:
            log.info(f"    Found {len(media)} media recording(s).")
        files += media

    log.info(f"  Total items found: {len(files)}")

    # ── 5. Classify and download ──────────────────────────────────────────────
    for f in files:
        filename = (
            f.get("display_name")
            or f.get("filename")
            or f"file_{f.get('id', 'unknown')}"
        )
        url = f.get("url") or f.get("download_url")

        if not url:
            log.info(f"    – (no URL)   {filename}")
            counts["skipped"] += 1
            continue

        skip, reason = should_skip(f)
        if skip:
            log.info(f"    – ({reason})  {filename}")
            counts["skipped"] += 1
            continue

        ok = download_file(
            url=url,
            dest_dir=course_dir / dest_subdir(f),
            filename=filename,
            size_bytes=f.get("size") or 0,
            dry_run=dry_run,
        )
        counts["downloaded" if ok else "failed"] += 1

    log.info(
        f"  → downloaded: {counts['downloaded']}  "
        f"skipped: {counts['skipped']}  "
        f"failed: {counts['failed']}"
    )
    return counts


# ──────────────────────────────────  MAIN  ────────────────────────────────────

def main() -> None:
    global g_skip_videos, g_skip_audio, g_skip_ongoing, g_max_size_mb, \
           DOWNLOAD_DIR, COOKIES

    parser = argparse.ArgumentParser(
        description="Download every Canvas course file before you lose access.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--dry-run",      action="store_true",
                        help="Preview only; nothing is saved to disk.")
    parser.add_argument("--dir",          metavar="PATH", default=str(DOWNLOAD_DIR),
                        help=f"Root output directory  (default: {DOWNLOAD_DIR})")
    parser.add_argument("--no-modules",   action="store_true",
                        help="Skip Module scanning.")
    parser.add_argument("--no-media",     action="store_true",
                        help="Skip Canvas media/video recordings.")
    parser.add_argument("--skip-videos",  action="store_true",
                        help="Skip all video files and media recordings.")
    parser.add_argument("--skip-audio",   action="store_true",
                        help="Skip all audio files.")
    parser.add_argument("--skip-ongoing", action="store_true",
                        help='Skip courses whose term is labelled "Ongoing".')
    parser.add_argument("--max-size",     metavar="MB", type=float,
                        help="Skip any file larger than this many MB.")
    args = parser.parse_args()

    DOWNLOAD_DIR   = Path(args.dir)
    g_skip_videos  = args.skip_videos
    g_skip_audio   = args.skip_audio
    g_skip_ongoing = args.skip_ongoing
    g_max_size_mb  = args.max_size

    # ── Browser login ─────────────────────────────────────────────────────────
    from canvas_auth import get_cookies
    COOKIES = get_cookies()
    if not COOKIES:
        log.error("  ✗  Could not get Canvas session. Exiting.")
        sys.exit(1)

    # ── Banner ────────────────────────────────────────────────────────────────
    log.info("═" * 70)
    log.info("  🎓  Canvas Complete Course Archiver")
    log.info("═" * 70)
    if args.dry_run:
        log.info("  DRY-RUN mode — nothing will be written to disk.\n")
    if g_skip_videos:
        log.info("  Skipping: videos")
    if g_skip_audio:
        log.info("  Skipping: audio")
    if g_skip_ongoing:
        log.info('  Skipping: "Ongoing" term courses')
    if g_max_size_mb:
        log.info(f"  Max file size: {g_max_size_mb} MB")
    if not HAS_TQDM:
        log.info("  Tip: pip install tqdm to get download progress bars.\n")

    # ── Fetch and iterate ─────────────────────────────────────────────────────
    log.info("\nFetching your course list …")
    courses = fetch_all_courses()
    log.info(f"Found {len(courses)} course(s).\n")

    # ── Filter out Ongoing courses if requested ───────────────────────────────
    if g_skip_ongoing:
        before = len(courses)
        courses = [c for c in courses if term_label(c).lower() != "ongoing"]
        skipped_count = before - len(courses)
        log.info(f"Skipping {skipped_count} Ongoing course(s). "
                 f"Processing {len(courses)} course(s).\n")

    totals = {"downloaded": 0, "skipped": 0, "failed": 0}
    errors: list[str] = []

    for course in courses:
        if not course.get("name"):
            continue
        try:
            counts = process_course(
                course,
                dry_run=args.dry_run,
                scan_modules=not args.no_modules,
                scan_media=not args.no_media,
            )
            for k in totals:
                totals[k] += counts[k]
        except Exception as exc:
            msg = f"Error processing '{course.get('name')}': {exc}"
            log.error(f"  ✗  {msg}")
            errors.append(msg)

    # ── Summary ───────────────────────────────────────────────────────────────
    log.info(f"\n{'═' * 70}")
    log.info("  ✅  FINISHED")
    log.info(f"  Downloaded : {totals['downloaded']}")
    log.info(f"  Skipped    : {totals['skipped']}")
    log.info(f"  Failed     : {totals['failed']}")
    if not args.dry_run:
        log.info(f"  Saved to   : {DOWNLOAD_DIR.resolve()}")
    if errors:
        log.info(f"\n  ⚠   {len(errors)} course(s) had errors — see canvas_downloader.log")
    log.info("  Log file   : canvas_downloader.log")
    log.info("═" * 70)


if __name__ == "__main__":
    main()