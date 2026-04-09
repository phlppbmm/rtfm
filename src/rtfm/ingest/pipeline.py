"""Ingestion pipeline: orchestrates download, parsing, extraction, and storage."""


import asyncio
import multiprocessing
import os
import stat
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

from rich.console import Console

from rtfm.ingest.detect import detect_doc_system, pre_detect_from_config
from rtfm.ingest.downloaders import check_remote_version, download_source
from rtfm.ingest.extractors import get_extractor
from rtfm.ingest.parsers import get_parser
from rtfm.models import AppConfig, KnowledgeUnit, SourceConfig
from rtfm.reporter import Reporter, make_reporter
from rtfm.storage import Storage

console = Console()


def _with_doc_system(config: SourceConfig, doc_system: str) -> SourceConfig:
    """Return a copy of config with doc_system set (SourceConfig is a frozen-ish dataclass)."""
    from dataclasses import replace
    return replace(config, doc_system=doc_system)


def _rmtree_safe(path: str | Path) -> None:
    """Remove a directory tree, handling Windows read-only files from git.

    Git creates read-only files inside .git/objects/.  On Windows,
    ``shutil.rmtree`` fails on those unless we flip the permissions first.
    We also tolerate residual WinError 32 (file locked) by retrying once
    after a short delay.
    """
    import shutil
    import time

    def _on_rm_error(func, fpath, exc_info):  # noqa: ANN001
        """onerror handler: clear read-only flag and retry."""
        try:
            os.chmod(fpath, stat.S_IWRITE)
            func(fpath)
        except OSError:
            pass

    for attempt in range(3):
        try:
            shutil.rmtree(path, onerror=_on_rm_error)
            return
        except OSError:
            if attempt < 2:
                time.sleep(0.2 * (attempt + 1))
    # Final best-effort: ignore if it still fails
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def _download_and_parse(
    source_config: SourceConfig,
    on_progress: Callable[[str], None] | None = None,
) -> tuple[list[KnowledgeUnit], str, str]:
    """Download, parse, and extract units for a single source.

    Returns (units, version_key, doc_system). No storage side effects.
    """
    # Pre-detect doc system from config/URL so the downloader can keep raw HTML
    pre_detected = pre_detect_from_config(source_config)
    if pre_detected and not source_config.doc_system:
        source_config = _with_doc_system(source_config, pre_detected)

    work_dir = tempfile.mkdtemp()
    try:
        result = download_source(source_config, Path(work_dir), on_progress=on_progress)
        if not result.files:
            return [], result.version_key, ""

        if on_progress is not None:
            on_progress(f"parsing {len(result.files)} files")

        # Detect doc system: explicit config > pre-detected > content-based auto-detect
        doc_system = source_config.doc_system or detect_doc_system(
            result.files, source_config.type
        )

        parser = get_parser(doc_system)
        extractor = get_extractor(doc_system)

        all_units: list[KnowledgeUnit] = []
        for rel_path, content, _content_type in result.files:
            sections = parser.parse(content, source_file=rel_path)
            units = extractor.extract(
                sections,
                framework=source_config.name,
                language=source_config.language,
                source_file=rel_path,
            )
            all_units.extend(units)

        # Deduplicate
        seen_ids: set[str] = set()
        unique: list[KnowledgeUnit] = []
        for unit in all_units:
            if unit.id not in seen_ids:
                seen_ids.add(unit.id)
                unique.append(unit)

        return unique, result.version_key, doc_system
    finally:
        _rmtree_safe(work_dir)


def _ingest_one(
    source_config: SourceConfig,
    storage: Storage,
    reporter: Reporter,
    task_id: str,
    rebuild: bool = True,
) -> int:
    """Ingest one framework end-to-end via the reporter. Returns unit count."""
    framework = source_config.name

    def _on_progress(msg: str) -> None:
        reporter.update(task_id, detail=msg)

    if rebuild:
        reporter.update(task_id, status="parsing", detail="clearing existing data")
        storage.clear_framework(framework)

    reporter.update(task_id, status="downloading")
    try:
        units, version_key, doc_system = _download_and_parse(source_config, on_progress=_on_progress)
    except Exception as e:  # noqa: BLE001 — surface to user
        reporter.finish(task_id, status="error", detail=str(e)[:80])
        return 0

    if not units:
        reporter.finish(task_id, status="done", detail="0 units")
        return 0

    reporter.update(task_id, status="embedding", detail=f"0/{len(units)}")

    def _embed_progress(done: int, total: int) -> None:
        reporter.update(task_id, detail=f"{done}/{total}")

    storage.insert_units(units, on_progress=_embed_progress)
    storage.set_version(framework, version_key, doc_system=doc_system)

    reporter.finish(task_id, status="done", detail=f"{len(units)} units")
    return len(units)


def update_all(config: AppConfig, *, as_json: bool = False) -> None:
    """Check for updates and re-ingest outdated sources via the reporter."""
    storage = Storage(config.data_dir, config.embedding_model)
    versions = storage.get_all_versions()

    frameworks = list(config.sources.keys())
    if not frameworks:
        console.print("[yellow]No sources configured.[/yellow]")
        storage.close()
        return

    # Phase 1: parallel update checks
    check_reporter = make_reporter(
        console,
        title=f"Checking {len(frameworks)} sources",
        as_json=as_json,
    )

    outdated: list[str] = []

    def _check_one(name: str) -> tuple[str, str]:
        src = config.sources[name]
        local_ver = versions.get(name, (None, None))[0]
        check_reporter.update(name, status="checking")

        def _on_progress(msg: str) -> None:
            check_reporter.update(name, detail=msg)

        try:
            remote_ver = check_remote_version(src, on_progress=_on_progress)
        except Exception as e:  # noqa: BLE001
            check_reporter.finish(name, status="error", detail=str(e)[:60])
            return name, "error"

        if remote_ver is None:
            if local_ver is None:
                check_reporter.finish(name, status="outdated", detail="not yet ingested")
                return name, "outdated"
            # Keep whatever detail the crawler set last (e.g. "rate limited",
            # "network error: ..."). Don't generic-ify it.
            check_reporter.finish(name, status="skipped")
            return name, "skipped"

        if local_ver != remote_ver:
            check_reporter.finish(name, status="outdated", detail=f"new: {remote_ver[:12]}")
            return name, "outdated"

        check_reporter.finish(name, status="ok", detail=local_ver[:12])
        return name, "ok"

    with check_reporter:
        for name in frameworks:
            check_reporter.add(name, name, status="pending")
        with ThreadPoolExecutor(max_workers=8) as pool:
            for name, state in pool.map(_check_one, frameworks):
                if state == "outdated":
                    outdated.append(name)

    if not outdated:
        if not as_json:
            console.print("\n[green]Everything is up to date.[/green]")
        storage.close()
        return

    if not as_json:
        console.print(f"\n[bold]Updating {len(outdated)} source(s): {', '.join(outdated)}[/bold]")

    # Phase 2: parallel download+parse, sequential embed (Chroma is single-writer)
    _ingest_many(config, storage, outdated, rebuild=True, as_json=as_json)
    storage.close()


def _resolve_workers(config: AppConfig) -> int:
    """Resolve the number of embedding worker processes."""
    w = config.ingest_workers
    if w <= 0:
        return max(1, (os.cpu_count() or 2) // 2)
    return w


def _ingest_many(
    config: AppConfig,
    storage: Storage,
    framework_names: list[str],
    *,
    rebuild: bool = True,
    as_json: bool = False,
) -> None:
    """Ingest a set of frameworks with async I/O and multiprocess embedding.

    Each source runs independently through download → parse → embed → store.
    Embedding runs in worker processes; storage writes are serialized.
    """
    asyncio.run(_ingest_many_async(
        config, storage, framework_names, rebuild=rebuild, as_json=as_json,
    ))


async def _ingest_many_async(
    config: AppConfig,
    storage: Storage,
    framework_names: list[str],
    *,
    rebuild: bool,
    as_json: bool,
) -> None:
    import signal

    sources = {name: config.sources[name] for name in framework_names}
    store_lock = asyncio.Lock()

    reporter = make_reporter(
        console,
        title=f"Ingesting {len(sources)} sources",
        as_json=as_json,
    )

    n_workers = _resolve_workers(config)
    embed_executor = ProcessPoolExecutor(
        max_workers=n_workers,
        mp_context=multiprocessing.get_context("spawn"),
        initializer=_embed_worker_init,
    )
    embed_semaphore = asyncio.Semaphore(n_workers)

    # Ensure worker processes are cleaned up on interrupt.
    # asyncio.to_thread() threads are not interruptible, so we kill
    # workers and hard-exit on signal instead of trying to await cleanup.
    loop = asyncio.get_running_loop()
    results: dict[str, int] = {}

    def _shutdown_on_signal() -> None:
        embed_executor.shutdown(wait=False, cancel_futures=True)
        for pid in _get_executor_pids(embed_executor):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        total = sum(results.values())
        reporter.log("Interrupted — cleaning up workers...")
        if total:
            reporter.log(f"Total: {total} units ingested across {len(results)} sources")
        # Hard exit — to_thread() threads can't be cancelled
        os._exit(130)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown_on_signal)

    try:
        with reporter:
            for name in sources:
                reporter.add(name, name, status="pending")

            try:
                async with asyncio.TaskGroup() as tg:
                    for name in sources:
                        tg.create_task(_ingest_one_async(
                            name=name,
                            src=sources[name],
                            config=config,
                            storage=storage,
                            reporter=reporter,
                            embed_executor=embed_executor,
                            embed_semaphore=embed_semaphore,
                            store_lock=store_lock,
                            rebuild=rebuild,
                            results=results,
                        ))
            except* (KeyboardInterrupt, asyncio.CancelledError):
                pass

            total_units = sum(results.values())
            if total_units:
                reporter.log(f"Total: {total_units} units ingested across {len(results)} sources")
    finally:
        embed_executor.shutdown(wait=False, cancel_futures=True)
        for pid in _get_executor_pids(embed_executor):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


def _get_executor_pids(executor: ProcessPoolExecutor) -> list[int]:
    """Extract PIDs from a ProcessPoolExecutor's internal process map."""
    pids: list[int] = []
    # Access internal _processes dict (set of Process objects)
    processes = getattr(executor, "_processes", None)
    if processes:
        for p in processes.values():
            if p.pid and p.is_alive():
                pids.append(p.pid)
    return pids


def _embed_worker_init() -> None:
    """ProcessPoolExecutor initializer — pre-load the ONNX model."""
    from rtfm.ingest.embedder import worker_init
    worker_init()


async def _ingest_one_async(
    *,
    name: str,
    src: SourceConfig,
    config: AppConfig,
    storage: Storage,
    reporter: Reporter,
    embed_executor: ProcessPoolExecutor,
    embed_semaphore: asyncio.Semaphore,
    store_lock: asyncio.Lock,
    rebuild: bool,
    results: dict[str, int],
) -> None:
    """Per-source pipeline: download → parse → embed → store."""
    from rtfm.ingest.embedder import embed_batch

    # Stage 1: Download + parse (in thread, no concurrency limit)
    def _on_progress(msg: str) -> None:
        reporter.update(name, detail=msg)

    reporter.update(name, status="downloading")
    try:
        units, version_key, doc_system = await asyncio.to_thread(
            _download_and_parse, src, _on_progress,
        )
    except Exception as e:  # noqa: BLE001
        reporter.finish(name, status="error", detail=str(e)[:80])
        return

    if not units and version_key in ("unknown", ""):
        reporter.finish(name, status="error")
        return

    if not units:
        reporter.finish(name, status="done", detail="0 units")
        return

    reporter.update(name, status="parsing", detail=f"{len(units)} units ready")

    # Stage 2: Embed in worker processes (semaphore limits concurrent sources)
    async with embed_semaphore:
        reporter.update(name, status="embedding", detail=f"0/{len(units)}")

        documents = [
            " > ".join(u.heading_hierarchy) + "\n\n" + u.content
            for u in units
        ]

        loop = asyncio.get_running_loop()
        all_embeddings: list[list[float]] = []
        batch_size = 100

        for i in range(0, len(documents), batch_size):
            batch_docs = documents[i : i + batch_size]
            batch_embs = await loop.run_in_executor(
                embed_executor, embed_batch, batch_docs,
            )
            all_embeddings.extend(batch_embs)
            reporter.update(name, detail=f"{len(all_embeddings)}/{len(units)}")

    # Stage 3: Store (serialized — single-writer, in thread so the event
    # loop stays free for embedding dispatches)
    reporter.update(name, status="storing", detail=f"0/{len(units)}")

    def _store_progress(done: int, total: int) -> None:
        reporter.update(name, detail=f"{done}/{total}")

    def _do_store() -> None:
        if rebuild:
            storage.clear_framework(name)
        storage.insert_units(units, embeddings=all_embeddings, on_progress=_store_progress)
        storage.set_version(name, version_key, doc_system=doc_system)

    async with store_lock:
        await asyncio.to_thread(_do_store)

    # Health check
    min_score = config.min_health_score
    if min_score > 0:
        from rtfm.health import compute_health
        h = compute_health(storage.conn, name)
        if h.score < min_score:
            async with store_lock:
                storage.clear_framework(name)
            reasons = ", ".join(h.signals[:3])
            reporter.finish(
                name,
                status="rejected",
                detail=f"health {h.score}/{min_score} ({reasons})",
            )
            return

    reporter.finish(name, status="done", detail=f"{len(units)} units")
    results[name] = len(units)


def ingest_all(
    config: AppConfig,
    framework: str | None = None,
    rebuild: bool = False,
    *,
    as_json: bool = False,
) -> None:
    """Run the full ingestion pipeline.

    *framework* — restrict to a single source.
    *rebuild*   — clear all existing data first (without *framework*) or just
                  the named framework.
    """
    storage = Storage(config.data_dir, config.embedding_model)

    if rebuild and framework is None:
        storage.clear_all()

    sources = config.sources
    if framework:
        if framework not in sources:
            console.print(f"[red]Unknown framework: {framework}[/red]")
            console.print(f"Available: {', '.join(sources.keys())}")
            storage.close()
            return
        names = [framework]
    else:
        names = list(sources.keys())

    if not names:
        console.print("[yellow]No sources configured.[/yellow]")
        storage.close()
        return

    _ingest_many(config, storage, names, rebuild=rebuild, as_json=as_json)
    storage.close()
