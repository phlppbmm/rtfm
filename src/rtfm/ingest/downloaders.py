"""Downloaders for documentation sources: GitHub sparse checkout, HTTP, local, website."""


import hashlib
import json
import shutil
from collections.abc import Callable
from pathlib import Path

import httpx

from rtfm.models import SourceConfig, default_home

# A progress callback receives a short human-readable status string. Passing
# None at any layer means "stay silent" — no print, no \r tricks. The CLI
# layer wraps a Reporter into one of these callbacks; library code never
# imports the Reporter directly.
ProgressCB = Callable[[str], None]


class DownloadResult:
    """Result of downloading a documentation source."""

    def __init__(self, files: list[tuple[str, str, str]], version_key: str):
        self.files = files          # [(relative_path, content, content_type), ...]
        self.version_key = version_key  # git SHA, content hash, or mtime


def download_source(
    config: SourceConfig,
    work_dir: Path,
    on_progress: ProgressCB | None = None,
) -> DownloadResult:
    """Download documentation files. Returns files and a version key."""
    if config.type == "github":
        return _download_github(config, work_dir)
    elif config.type == "llms_txt":
        return _download_llms_txt(config)
    elif config.type == "local":
        return _read_local(config)
    elif config.type == "website":
        return _download_website(config, on_progress=on_progress)
    else:
        raise ValueError(f"Unknown source type: {config.type}")


def check_remote_version(
    config: SourceConfig,
    on_progress: ProgressCB | None = None,
) -> str | None:
    """Check the remote version without downloading full content.

    Returns a version key, or None if the check is not supported.
    The optional *on_progress* callback receives short status strings during
    crawl-based checks; pass None to stay silent.
    """
    if config.type == "github":
        return _check_github_version(config, on_progress=on_progress)
    elif config.type == "llms_txt":
        return _check_llms_txt_version(config, on_progress=on_progress)
    elif config.type == "local":
        return _check_local_version(config)
    elif config.type == "website":
        return _check_website_version(config, on_progress=on_progress)
    return None


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

def _check_github_version(
    config: SourceConfig,
    on_progress: ProgressCB | None = None,
) -> str | None:
    """Get the latest commit SHA of the default branch via GitHub API."""
    # Try GitHub API (no auth needed for public repos)
    last_status: int | None = None
    for branch in ("main", "master"):
        url = f"https://api.github.com/repos/{config.repo}/commits/{branch}"
        try:
            resp = httpx.get(url, headers={"Accept": "application/vnd.github.v3+json"}, timeout=15.0)
            last_status = resp.status_code
            if resp.status_code == 200:
                sha: str = resp.json()["sha"]
                return sha[:12]
            if resp.status_code == 403:
                # GitHub rate limit (60/hour unauthenticated). Stop trying
                # other branches — they would all hit the same limit.
                if on_progress is not None:
                    on_progress("github API rate limited (403)")
                return None
        except (httpx.HTTPError, KeyError):
            continue
    if on_progress is not None:
        if last_status is not None:
            on_progress(f"github API HTTP {last_status}")
        else:
            on_progress("github API: network error")
    return None


def _close_git_repo(repo: "git.Repo") -> None:  # type: ignore[name-defined]
    """Aggressively close a GitPython Repo and release all OS handles.

    On Windows, GitPython's child git.exe processes and memory-mapped index
    files keep handles open on the .git directory.  If those handles are not
    released before ``TemporaryDirectory`` cleanup, ``shutil.rmtree`` fails
    with ``[WinError 32] The process cannot access the file because it is
    being used by another process``.
    """
    import gc
    import sys

    try:
        # Close the underlying git command interface (kills persistent
        # git cat-file / git daemon processes).
        if hasattr(repo, "git"):
            repo.git.clear_cache()
        # GitDB / loose-object database may hold open file descriptors.
        if repo.odb is not None:
            # GitDB.close() exists on some versions
            close = getattr(repo.odb, "close", None)
            if close is not None:
                close()
        repo.close()
    except Exception:  # noqa: BLE001
        pass

    del repo
    gc.collect()

    # On Windows, give the OS a moment to actually release handles after the
    # git processes have been killed.
    if sys.platform == "win32":
        import time
        time.sleep(0.1)


def _download_github(config: SourceConfig, work_dir: Path) -> DownloadResult:
    """Sparse checkout of a GitHub repo's docs directory."""
    import git

    repo_url = f"https://github.com/{config.repo}.git"
    clone_dir = work_dir / config.name

    if clone_dir.exists():
        shutil.rmtree(clone_dir)

    # Initialize a repo with sparse checkout
    repo = git.Repo.init(clone_dir)
    try:
        repo.git.remote("add", "origin", repo_url)
        repo.git.config("core.sparseCheckout", "true")

        # Write sparse-checkout pattern
        sparse_file = clone_dir / ".git" / "info" / "sparse-checkout"
        sparse_file.parent.mkdir(parents=True, exist_ok=True)
        sparse_file.write_text(config.docs_path + "/\n")

        # Fetch only the default branch (shallow)
        repo.git.fetch("origin", "--depth=1")
        commit_sha = None
        for branch in ("main", "master"):
            try:
                repo.git.checkout(f"origin/{branch}")
                commit_sha = repo.head.commit.hexsha[:12]
                break
            except git.GitCommandError:
                continue

        version_key = commit_sha or "unknown"

        # Collect markdown files
        docs_root = clone_dir / config.docs_path
        if not docs_root.exists():
            return DownloadResult([], version_key)

        import fnmatch

        glob_pattern = config.glob or "**/*.md"
        # Extract the file-level pattern (e.g. "*.md" from "**/*.md")
        file_pattern = glob_pattern.split("/")[-1] if "/" in glob_pattern else glob_pattern

        results: list[tuple[str, str, str]] = []
        for md_file in docs_root.rglob("*"):
            if not md_file.is_file():
                continue
            rel = str(md_file.relative_to(clone_dir)).replace("\\", "/")
            if not fnmatch.fnmatch(md_file.name, file_pattern):
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
                ctype = "rst" if md_file.suffix == ".rst" else "markdown"
                results.append((rel, content, ctype))
            except (UnicodeDecodeError, OSError):
                continue
    finally:
        _close_git_repo(repo)

    return DownloadResult(results, version_key)


# ---------------------------------------------------------------------------
# llms.txt / llms-full.txt
# ---------------------------------------------------------------------------

def _version_key_from_http_response(resp: httpx.Response) -> str | None:
    """Extract a version key from HTTP headers, if possible."""
    etag = resp.headers.get("etag")
    if etag:
        return f"etag:{etag}"
    last_mod = resp.headers.get("last-modified")
    if last_mod:
        return f"mod:{last_mod}"
    return None


def _check_llms_txt_version(
    config: SourceConfig,
    on_progress: ProgressCB | None = None,
) -> str | None:
    """HEAD request to get a version key cheaply."""
    try:
        resp = httpx.head(config.url, follow_redirects=True, timeout=15.0)
        if resp.status_code == 200:
            return _version_key_from_http_response(resp)
        if on_progress is not None:
            on_progress(f"HTTP {resp.status_code}")
    except httpx.HTTPError as e:
        if on_progress is not None:
            on_progress(f"network error: {type(e).__name__}")
    return None


def _download_llms_txt(config: SourceConfig) -> DownloadResult:
    """Download an llms.txt or llms-full.txt file via HTTP."""
    response = httpx.get(config.url, follow_redirects=True, timeout=60.0)
    response.raise_for_status()
    content = response.text
    filename = config.url.rsplit("/", 1)[-1]

    # Use same key derivation as the HEAD check for consistency
    version_key = _version_key_from_http_response(response)
    if not version_key:
        # Fallback: content hash
        version_key = hashlib.sha256(content.encode()).hexdigest()[:12]

    return DownloadResult([(filename, content, "markdown")], version_key)


# ---------------------------------------------------------------------------
# Local files
# ---------------------------------------------------------------------------

def _check_local_version(config: SourceConfig) -> str | None:
    """Hash based on file count + total size + newest mtime."""
    root = Path(config.path)
    if not root.exists():
        return None

    total_size = 0
    newest_mtime = 0.0
    count = 0
    for f in root.rglob("*.md"):
        if f.is_file():
            stat = f.stat()
            total_size += stat.st_size
            newest_mtime = max(newest_mtime, stat.st_mtime)
            count += 1

    key = f"{count}:{total_size}:{int(newest_mtime)}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _read_local(config: SourceConfig) -> DownloadResult:
    """Read markdown files from a local directory."""
    import fnmatch

    root = Path(config.path)
    if not root.exists():
        raise FileNotFoundError(f"Local source path does not exist: {config.path}")

    results: list[tuple[str, str, str]] = []
    total_size = 0
    newest_mtime = 0.0
    count = 0

    for md_file in root.rglob("*"):
        if not md_file.is_file():
            continue
        if not fnmatch.fnmatch(md_file.name, "*.md"):
            continue
        try:
            stat = md_file.stat()
            total_size += stat.st_size
            newest_mtime = max(newest_mtime, stat.st_mtime)
            count += 1
            rel = str(md_file.relative_to(root)).replace("\\", "/")
            content = md_file.read_text(encoding="utf-8")
            ctype = "rst" if md_file.suffix == ".rst" else "markdown"
            results.append((rel, content, ctype))
        except (UnicodeDecodeError, OSError):
            continue

    key = f"{count}:{total_size}:{int(newest_mtime)}"
    version_key = hashlib.sha256(key.encode()).hexdigest()[:12]
    return DownloadResult(results, version_key)


# ---------------------------------------------------------------------------
# Website (sitemap + HTML → Markdown)
# ---------------------------------------------------------------------------

_GOOGLEBOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


def _parse_sitemap(sitemap_url: str, url_filter: str) -> list[str]:
    """Fetch sitemap.xml and return URLs matching the filter."""
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
    """Convert HTML to Markdown, stripping navigation and boilerplate."""
    import html2text

    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.body_width = 0
    md = h.handle(html)

    # Strip everything before the first real heading (nav, sidebar, etc.)
    lines = md.split("\n")
    start = 0
    for i, line in enumerate(lines):
        if line.startswith("# ") and "Loading" not in line:
            start = i
            break

    # Strip trailing boilerplate (footer, "Was this helpful?", etc.)
    end = len(lines)
    for i in range(len(lines) - 1, start, -1):
        line = lines[i].strip().lower()
        if any(kw in line for kw in ["was this helpful", "updated ", "next steps", "cookie"]):
            end = i
            break

    return "\n".join(lines[start:end]).strip()


def _url_to_filepath(url: str, url_filter: str) -> str:
    """Convert a URL to a relative file path for storage."""
    # Extract the path after the filter prefix
    path = url.split("//", 1)[-1]  # remove scheme
    path = path.split("/", 1)[-1] if "/" in path else path  # remove host

    if url_filter:
        idx = path.find(url_filter.strip("/"))
        if idx >= 0:
            path = path[idx + len(url_filter.strip("/")):]

    path = path.strip("/")
    if not path:
        path = "index"
    # Don't append .md if the path already has a recognized extension
    if not path.endswith((".md", ".html", ".rst")):
        path = path + ".md"
    return path


def _crawl_links(
    start_url: str,
    max_pages: int = 2000,
    on_progress: ProgressCB | None = None,
) -> list[str]:
    """Recursively crawl a start URL and discover all links under the same path prefix.

    Uses concurrent BFS to follow links across pages (e.g. TypeDoc sites where
    the index links to modules, which link to classes, which link to methods).
    """
    import re
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from urllib.parse import urljoin, urlparse

    # Initial GET with backoff: GitHub Pages and similar CDNs return 429 if
    # we crawl too eagerly. A short retry rescues most transient throttles
    # without escalating into the user-visible error path.
    import time

    resp = None
    for attempt, delay in enumerate((0, 2, 4, 8)):
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
        # The previous fallback returned [start_url], which then poisoned the
        # version key calculation downstream — every transient 429/503 looked
        # like a "real" 1-URL site and produced a different hash. Return [] so
        # the caller can treat it as "could not determine".
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
    parsed = urlparse(base_url)
    prefix = parsed.path.rstrip("/")
    _static_exts = (".css", ".js", ".svg", ".png", ".ico", ".woff", ".woff2", ".ttf", ".eot", ".map")

    def _extract_links(html: str, page_url: str) -> set[str]:
        found: set[str] = set()
        for href in re.findall(r'href="([^"#]+)"', html):
            if href.startswith(("javascript:", "mailto:", "data:")):
                continue
            full = urljoin(page_url, href).split("#")[0].split("?")[0].rstrip("/")
            full_parsed = urlparse(full)
            if full_parsed.netloc != parsed.netloc:
                continue
            if not full_parsed.path.startswith(prefix):
                continue
            if any(full_parsed.path.endswith(ext) for ext in _static_exts):
                continue
            found.add(full)
        return found

    # The crawl must be deterministic so the version key derived from the
    # final URL set is stable across runs. Three sources of non-determinism
    # to avoid:
    #   1. iterating a set (hash randomization)
    #   2. consuming `as_completed` futures in network-latency order
    #   3. applying `discovered |= new_links` without strict cap enforcement,
    #      so two runs may keep different links once max_pages is reached
    base_root = base_url.rstrip("/")
    discovered: set[str] = {base_root}
    initial_links = _extract_links(resp.text, base_url)
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
                    return url, _extract_links(r.text, str(r.url))
            except httpx.HTTPError:
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

            # Apply results in stable URL order so two runs that complete
            # the same fetches end up with the same `discovered` set, even
            # when the cap fires partway through.
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
    """Get the list of URLs to scrape — from sitemap, explicit list, or crawl.

    Crawl mode is triggered by a trailing /* in the url:
        url: https://tokio.rs/tokio/tutorial/*
    """
    if config.sitemap:
        return _parse_sitemap(config.sitemap, config.url_filter)
    if config.urls:
        return sorted(config.urls)
    if config.url and config.url.endswith("/*"):
        return _crawl_links(config.url[:-2], on_progress=on_progress)  # strip /*
    if config.url:
        return [config.url]
    return []


def _url_set_hash(urls: list[str]) -> str:
    """Stable hash over the sorted URL set.

    We deliberately do NOT include per-URL ETags here. Two reasons:
    - Many CDNs (notably GitHub Pages) rotate ETags between cache nodes for
      the same content. A version key built from those ETags drifts even
      when nothing has actually changed, so the source looks permanently
      "outdated" no matter how often the user runs `rtfm update`.
    - 2000 sequential HEAD requests dominate the wall-clock time of every
      status check.

    The trade-off: silent content edits (same URL set, different body) are
    not detected automatically. Users who want to force a refresh run
    `rtfm ingest -f <name> --rebuild`.
    """
    sorted_urls = sorted(set(urls))
    payload = "\n".join(sorted_urls)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def _website_version_key(
    config: SourceConfig,
    on_progress: ProgressCB | None = None,
) -> str | None:
    """Stable version key for a website source.

    - Sitemap mode: hash the sitemap XML
    - URL list / crawl mode: HEAD each URL, collect ETags, hash the set
    """
    if config.sitemap:
        try:
            resp = httpx.get(config.sitemap, timeout=15.0, follow_redirects=True)
            if resp.status_code == 200:
                return hashlib.sha256(resp.text.encode()).hexdigest()[:12]
        except httpx.HTTPError:
            pass
        return None

    urls = _resolve_website_urls(config, on_progress=on_progress)
    if urls:
        return _url_set_hash(urls)
    return None


def _check_website_version(
    config: SourceConfig,
    on_progress: ProgressCB | None = None,
) -> str | None:
    """Cheap version check for website sources."""
    return _website_version_key(config, on_progress=on_progress)


class _PageCache:
    """Per-page ETag + Markdown cache for website sources.

    Stored as JSON at ~/.rtfm/cache/<source_name>.json:
    { "url": { "etag": "...", "markdown": "..." }, ... }
    """

    def __init__(self, source_name: str) -> None:
        self._path = Path(default_home()) / "cache" / f"{source_name}.json"
        self._data: dict[str, dict[str, str]] = {}
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get(self, url: str) -> tuple[str, str] | None:
        """Return (etag, markdown) if cached, else None."""
        entry = self._data.get(url)
        if entry and "etag" in entry and "markdown" in entry:
            return entry["etag"], entry["markdown"]
        return None

    def put(self, url: str, etag: str, markdown: str) -> None:
        self._data[url] = {"etag": etag, "markdown": markdown}

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")


def _download_website(
    config: SourceConfig,
    on_progress: ProgressCB | None = None,
) -> DownloadResult:
    """Download docs from a website via sitemap or URL list + HTML→Markdown.

    Uses per-page ETag caching and concurrent downloads for speed.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Resolve URL list once and derive the version key from it. Previously
    # we called _website_version_key (which crawls) AND _resolve_website_urls
    # (which crawls again) — exactly the same crawl twice in a row.
    urls = _resolve_website_urls(config, on_progress=on_progress)
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
        except httpx.HTTPError:
            version_key = "unknown"
    else:
        version_key = _url_set_hash(urls)

    # HTML-native doc systems skip the HTML→MD conversion and pass raw HTML
    _HTML_NATIVE_SYSTEMS = {"rustdoc", "typedoc"}
    html_native = config.doc_system in _HTML_NATIVE_SYSTEMS

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
            """Returns (filepath, content, content_type, etag, is_cache_hit) or None."""
            try:
                headers: dict[str, str] = {}
                cached = cache.get(url)
                # Don't use cached content if the mode changed (e.g. markdown→html
                # after switching to a html-native parser). Detect by checking if
                # the cached content starts with '<' (HTML) vs '#' or text (markdown).
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
            except httpx.HTTPError:
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
