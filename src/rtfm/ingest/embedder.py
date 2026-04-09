"""Standalone embedding module for multiprocess workers."""

from __future__ import annotations

import os
from typing import ClassVar

# Suppress ONNX noise in worker processes
os.environ.setdefault("ORT_LOGGING_LEVEL", "3")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


class Embedder:
    """Wraps ChromaDB's default ONNX embedding model for standalone use."""

    _instance: ClassVar[Embedder | None] = None

    def __init__(self) -> None:
        from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import (
            ONNXMiniLM_L6_V2,
        )

        self._ef = ONNXMiniLM_L6_V2()

    def encode(self, documents: list[str]) -> list[list[float]]:
        """Encode documents into embedding vectors."""
        return self._ef(documents)

    @classmethod
    def get_instance(cls) -> Embedder:
        if cls._instance is None:
            cls._instance = Embedder()
        return cls._instance


def worker_init() -> None:
    """ProcessPoolExecutor initializer: pre-load the model in each worker.

    Workers ignore SIGINT — the main process handles shutdown and kills
    workers explicitly. Without this, Ctrl+C sends SIGINT to the entire
    process group and workers dump tracebacks before our handler runs.
    """
    import signal

    signal.signal(signal.SIGINT, signal.SIG_IGN)
    Embedder.get_instance()


def embed_batch(documents: list[str]) -> list[list[float]]:
    """Top-level function for ProcessPoolExecutor.submit().

    Must be a module-level function (picklable).
    """
    return Embedder.get_instance().encode(documents)
