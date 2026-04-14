"""GitHub source handler — sparse checkout of repo docs."""

from __future__ import annotations

import gc
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

import httpx

from rtfm.ingest.sources.base import DownloadResult, ProgressCB
from rtfm.models import SourceConfig


class GithubHandler:
    name: ClassVar[str] = "github"

    def probe(
        self,
        config: SourceConfig,
        on_progress: ProgressCB | None = None,
    ) -> SourceConfig | None:
        url = config.url or ""
        if "github.com/" not in url:
            return None
        # Extract owner/repo and optional docs_path from URL like:
        #   https://github.com/owner/repo
        #   https://github.com/owner/repo/tree/main/docs/en/docs
        parts = url.split("github.com/", 1)[1].strip("/").split("/")
        if len(parts) < 2:
            return None
        owner_repo = f"{parts[0]}/{parts[1]}"

        # Extract docs_path from /tree/<branch>/<path> if present
        docs_path = config.docs_path
        if not docs_path and len(parts) > 3 and parts[2] == "tree":
            # parts = [owner, repo, "tree", branch, ...path segments...]
            docs_path = "/".join(parts[4:])
        if not docs_path:
            docs_path = "docs"

        return replace(
            config,
            type="github",
            repo=owner_repo,
            docs_path=docs_path,
        )

    def check_version(
        self,
        config: SourceConfig,
        on_progress: ProgressCB | None = None,
    ) -> str | None:
        last_status: int | None = None
        for branch in ("main", "master"):
            api_url = f"https://api.github.com/repos/{config.repo}/commits/{branch}"
            try:
                resp = httpx.get(
                    api_url,
                    headers={"Accept": "application/vnd.github.v3+json"},
                    timeout=15.0,
                )
                last_status = resp.status_code
                if resp.status_code == 200:
                    sha: str = resp.json()["sha"]
                    return sha[:12]
                if resp.status_code == 403:
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

    def download(
        self,
        config: SourceConfig,
        work_dir: Path,
        on_progress: ProgressCB | None = None,
    ) -> DownloadResult:
        import fnmatch

        import git

        repo_url = f"https://github.com/{config.repo}.git"
        clone_dir = work_dir / config.name

        if clone_dir.exists():
            shutil.rmtree(clone_dir)

        repo = git.Repo.init(clone_dir)
        try:
            repo.git.remote("add", "origin", repo_url)
            repo.git.config("core.sparseCheckout", "true")

            sparse_file = clone_dir / ".git" / "info" / "sparse-checkout"
            sparse_file.parent.mkdir(parents=True, exist_ok=True)
            sparse_file.write_text(config.docs_path + "/\n")

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

            docs_root = clone_dir / config.docs_path
            if not docs_root.exists():
                return DownloadResult([], version_key)

            glob_pattern = config.glob or "**/*.md"
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


def _close_git_repo(repo: "git.Repo") -> None:  # type: ignore[name-defined]
    """Aggressively close a GitPython Repo and release all OS handles."""
    try:
        if hasattr(repo, "git"):
            repo.git.clear_cache()
        if repo.odb is not None:
            close = getattr(repo.odb, "close", None)
            if close is not None:
                close()
        repo.close()
    except Exception:  # noqa: BLE001
        pass

    del repo
    gc.collect()

    if sys.platform == "win32":
        import time

        time.sleep(0.1)
