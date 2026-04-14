"""Website source handler — crawl + HTML→Markdown."""

from __future__ import annotations

import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import ClassVar
from urllib.parse import urljoin, urlparse

import httpx

from rtfm.ingest.sources.base import DownloadResult, ProgressCB
from rtfm.models import SourceConfig, default_home

_GOOGLEBOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
_STATIC_EXTS = (".css", ".js", ".svg", ".png", ".ico", ".woff", ".woff2", ".ttf", ".eot", ".map")

# Minimum discoverable pages for auto-detection to accept a site.
_MIN_CRAWLABLE_PAGES = 5


class WebsiteHandler:
    name: ClassVar[str] = "website"

    def probe(
        self,
        config: SourceConfig,
        on_progress: ProgressCB | None = None,
    ) -> SourceConfig | None:
        base = (config.url or "").rstrip("/*")
        if not base:
            return None

        # Fetch start page and count discoverable same-prefix links.
        try:
            resp = httpx.get(
                base,
                timeout=15.0,
                follow_redirects=True,
                headers={"User-Agent": _GOOGLEBOT_UA},
            )
            if resp.status_code != 200:
                return None
        except httpx.HTTPError:
            return None

        links = _extract_links(resp.text, str(resp.url), base)
        # TypeDoc sites load navigation async via JS — the static start page
        # only links to index.html and modules.html. Accept anyway: the
        # crawler will pick up the rest from modules.html.
        from rtfm.ingest.detect import _is_typedoc

        is_typedoc = _is_typedoc(resp.text)
        sitemap_url = ""
        if len(links) < _MIN_CRAWLABLE_PAGES and not is_typedoc:
            # Fallback: many SPA-rendered doc sites (e.g. Docusaurus) expose
            # almost no static links but ship a sitemap.xml.
            sitemap_url = _discover_sitemap(base)
            if not sitemap_url:
                return None

        crawl_url = base + "/*"
        updates: dict[str, str] = {"type": "website", "url": crawl_url}
        if is_typedoc and not config.doc_system:
            updates["doc_system"] = "typedoc"
        if sitemap_url and not config.sitemap:
            updates["sitemap"] = sitemap_url
        return replace(config, **updates)

    def check_version(
        self,
        config: SourceConfig,
        on_progress: ProgressCB | None = None,
    ) -> str | None:
        if config.sitemap:
            try:
                resp = httpx.get(config.sitemap, timeout=15.0, follow_redirects=True)
                if resp.status_code == 200:
                    return hashlib.sha256(resp.text.encode()).hexdigest()[:12]
            except (httpx.HTTPError, httpx.InvalidURL):
                pass
            return None

        urls = _resolve_website_urls(config, on_progress=on_progress)
        if not urls:
            return None
        # Stash the resolved URLs so download() can reuse them.
        config._crawled_urls = urls  # type: ignore[attr-defined]
        return _url_set_hash(urls)

    def download(
        self,
        config: SourceConfig,
        work_dir: Path,
        on_progress: ProgressCB | None = None,
    ) -> DownloadResult:
        # Reuse URLs cached by check_version when available.
        urls: list[str] = getattr(config, "_crawled_urls", None) or _resolve_website_urls(
            config, on_progress=on_progress
        )
        if not urls:
            return DownloadResult([], "unknown")

        if config.sitemap:
            try:
                resp = httpx.get(config.sitemap, timeout=15.0, follow_redirects=True)
                version_key = (
                    hashlib.sha256(resp.text.encode()).hexdigest()[:12]
                    if resp.status_code == 200
                    else "unknown"
                )
            except (httpx.HTTPError, httpx.InvalidURL):
                version_key = "unknown"
        else:
            version_key = _url_set_hash(urls)

        html_native = config.doc_system in {"rustdoc", "typedoc"}
        cache = _PageCache(config.name)
        results: list[tuple[str, str, str]] = []
        hits = 0
        done = 0
        total = len(urls)

        with httpx.Client(
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": _GOOGLEBOT_UA},
        ) as client:

            def _fetch_one(url: str) -> tuple[str, str, str, str, bool] | None:
                try:
                    headers: dict[str, str] = {}
                    cached = cache.get(url)
                    cache_matches_mode = True
                    if cached and html_native and not cached[1].lstrip().startswith("<"):
                        cache_matches_mode = False
                    if cached and not html_native and cached[1].lstrip().startswith("<!"):
                        cache_matches_mode = False

                    if cached and cache_matches_mode:
                        headers["If-None-Match"] = cached[0]

                    resp = client.get(url, headers=headers)

                    if resp.status_code == 304 and cached and cache_matches_mode:
                        filepath = _url_to_filepath(url, config.url_filter)
                        ctype = "html" if html_native else "markdown"
                        return filepath, cached[1], ctype, "", True

                    if resp.status_code != 200:
                        return None

                    if html_native:
                        content = resp.text
                        content_type = "html"
                    else:
                        content = _html_to_markdown(resp.text)
                        content_type = "markdown"

                    if not content.strip():
                        return None

                    filepath = _url_to_filepath(url, config.url_filter)
                    etag = resp.headers.get("etag", "")
                    return filepath, content, content_type, etag, False
                except (httpx.HTTPError, httpx.InvalidURL):
                    return None

            with ThreadPoolExecutor(max_workers=16) as pool:
                futures = {pool.submit(_fetch_one, url): url for url in urls}
                for future in as_completed(futures):
                    done += 1
                    if on_progress is not None and (done % 20 == 0 or done == total):
                        on_progress(f"fetching {done}/{total} pages")
                    result = future.result()
                    if result is None:
                        continue
                    filepath, content, content_type, etag, is_hit = result
                    results.append((filepath, content, content_type))
                    if is_hit:
                        hits += 1
                    elif etag:
                        cache.put(futures[future], etag, content)

        cache.save()
        fetched = len(results) - hits
        if on_progress is not None:
            cache_info = f", {hits} cached" if hits else ""
            on_progress(f"fetched {fetched} pages{cache_info}")
        return DownloadResult(results, version_key)


# ---------------------------------------------------------------------------
# Helpers (moved from downloaders.py)
# ---------------------------------------------------------------------------


_HREF_RE = re.compile(
    r"""href\s*=\s*(?:"([^"#]*)"|'([^'#]*)'|([^\s>#]+))""",
    re.IGNORECASE,
)


def _extract_links(html: str, page_url: str, prefix: str) -> set[str]:
    """Extract same-prefix, same-host links from HTML."""
    parsed = urlparse(prefix)
    prefix_path = parsed.path.rstrip("/")
    found: set[str] = set()
    for dq, sq, uq in _HREF_RE.findall(html):
        href = dq or sq or uq
        if not href or len(href) > 500:
            continue
        if href.startswith(("javascript:", "mailto:", "data:", "\\")):
            continue
        full = urljoin(page_url, href).split("#")[0].split("?")[0].rstrip("/")
        if len(full) > 500:
            continue
        full_parsed = urlparse(full)
        if full_parsed.netloc != parsed.netloc:
            continue
        if not full_parsed.path.startswith(prefix_path):
            continue
        if any(full_parsed.path.endswith(ext) for ext in _STATIC_EXTS):
            continue
        found.add(full)
    return found


def _discover_sitemap(base: str) -> str:
    """Return a sitemap URL at the site root if one exists, else empty string."""
    parsed = urlparse(base)
    root = f"{parsed.scheme}://{parsed.netloc}"
    for candidate in (f"{root}/sitemap.xml", f"{base.rstrip('/')}/sitemap.xml"):
        try:
            r = httpx.head(
                candidate,
                timeout=10.0,
                follow_redirects=True,
                headers={"User-Agent": _GOOGLEBOT_UA},
            )
            if r.status_code == 200:
                return candidate
        except httpx.HTTPError:
            continue
    return ""


def _parse_sitemap(sitemap_url: str, url_filter: str) -> list[str]:
    from xml.etree import ElementTree

    resp = httpx.get(sitemap_url, timeout=15.0, follow_redirects=True)
    resp.raise_for_status()
    root = ElementTree.fromstring(resp.text)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls: list[str] = []
    for url_el in root.findall("s:url", ns):
        loc = url_el.find("s:loc", ns)
        if loc is not None and loc.text and (not url_filter or url_filter in loc.text):
            urls.append(loc.text)
    return sorted(urls)


def _html_to_markdown(html: str) -> str:
    import html2text

    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.body_width = 0
    md = h.handle(html)

    lines = md.split("\n")
    start = 0
    for i, line in enumerate(lines):
        if line.startswith("# ") and "Loading" not in line:
            start = i
            break

    end = len(lines)
    for i in range(len(lines) - 1, start, -1):
        line = lines[i].strip().lower()
        if any(kw in line for kw in ["was this helpful", "updated ", "next steps", "cookie"]):
            end = i
            break

    return "\n".join(lines[start:end]).strip()


def _url_to_filepath(url: str, url_filter: str) -> str:
    path = url.split("//", 1)[-1]
    path = path.split("/", 1)[-1] if "/" in path else path

    if url_filter:
        idx = path.find(url_filter.strip("/"))
        if idx >= 0:
            path = path[idx + len(url_filter.strip("/")):]

    path = path.strip("/")
    if not path:
        path = "index"
    if not path.endswith((".md", ".html", ".rst")):
        path = path + ".md"
    return path


def _crawl_links(
    start_url: str,
    max_pages: int = 2000,
    on_progress: ProgressCB | None = None,
) -> list[str]:
    """Recursively crawl a start URL and discover all links under the same path prefix."""
    resp = None
    for _attempt, delay in enumerate((0, 2, 4, 8)):
        if delay:
            if on_progress is not None:
                on_progress(f"rate-limited, retrying in {delay}s")
            time.sleep(delay)
        try:
            resp = httpx.get(
                start_url,
                timeout=15.0,
                follow_redirects=True,
                headers={"User-Agent": _GOOGLEBOT_UA},
            )
        except httpx.HTTPError:
            resp = None
            continue
        if resp.status_code == 200:
            break
        if resp.status_code not in (429, 502, 503, 504):
            break

    if resp is None or resp.status_code != 200:
        if on_progress is not None:
            if resp is None:
                on_progress("network error: could not reach source")
            else:
                reason = {
                    429: "rate limited",
                    502: "bad gateway",
                    503: "service unavailable",
                    504: "gateway timeout",
                }.get(resp.status_code, f"HTTP {resp.status_code}")
                on_progress(f"{reason} ({start_url})")
        return []

    base_url = str(resp.url).rstrip("/") + "/"
    prefix = base_url.rstrip("/")

    base_root = base_url.rstrip("/")
    discovered: set[str] = {base_root}
    initial_links = _extract_links(resp.text, base_url, prefix)
    for link in sorted(initial_links):
        if len(discovered) >= max_pages:
            break
        discovered.add(link)

    visited: set[str] = {base_root}
    queue: list[str] = sorted(discovered - visited)

    with httpx.Client(
        timeout=15.0,
        follow_redirects=True,
        headers={"User-Agent": _GOOGLEBOT_UA},
    ) as client:

        def _fetch(url: str) -> tuple[str, set[str]]:
            try:
                r = client.get(url)
                if r.status_code == 200:
                    return url, _extract_links(r.text, str(r.url), prefix)
            except (httpx.HTTPError, httpx.InvalidURL):
                pass
            return url, set()

        while queue and len(discovered) < max_pages:
            batch = [u for u in queue[:64] if u not in visited]
            queue = queue[64:]
            if not batch:
                continue

            results: list[tuple[str, set[str]]] = []
            with ThreadPoolExecutor(max_workers=16) as pool:
                futures = {pool.submit(_fetch, url): url for url in batch}
                for future in as_completed(futures):
                    results.append(future.result())

            for url, links in sorted(results, key=lambda r: r[0]):
                visited.add(url)
                for link in sorted(links):
                    if len(discovered) >= max_pages:
                        break
                    if link not in discovered:
                        discovered.add(link)
                        queue.append(link)

            if on_progress is not None:
                on_progress(f"crawling {len(visited)}/{len(discovered)} pages")

    if on_progress is not None:
        on_progress(f"crawled {len(discovered)} pages")
    return sorted(discovered)


def _resolve_website_urls(
    config: SourceConfig,
    on_progress: ProgressCB | None = None,
) -> list[str]:
    if config.sitemap:
        return _parse_sitemap(config.sitemap, config.url_filter)
    if config.urls:
        return sorted(config.urls)
    if config.url and config.url.endswith("/*"):
        return _crawl_links(config.url[:-2], on_progress=on_progress)
    if config.url:
        return [config.url]
    return []


def _url_set_hash(urls: list[str]) -> str:
    sorted_urls = sorted(set(urls))
    payload = "\n".join(sorted_urls)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


class _PageCache:
    def __init__(self, source_name: str) -> None:
        self._path = Path(default_home()) / "cache" / f"{source_name}.json"
        self._data: dict[str, dict[str, str]] = {}
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get(self, url: str) -> tuple[str, str] | None:
        entry = self._data.get(url)
        if entry and "etag" in entry and "markdown" in entry:
            return entry["etag"], entry["markdown"]
        return None

    def put(self, url: str, etag: str, markdown: str) -> None:
        self._data[url] = {"etag": etag, "markdown": markdown}

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
