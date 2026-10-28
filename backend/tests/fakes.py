"""Test doubles."""

import hashlib

import numpy as np

from investigator.embedding import DIMENSIONS


class FakeEmbedder:
    """Deterministic unit vectors derived from a hash of the text: equal text, equal vector.

    No model download, no meaning: for testing storage and plumbing, not search quality.
    """

    name = "fake-embedder"
    sha256 = "0" * 64

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        self.calls.append(list(texts))
        return np.vstack([self._vector(t) for t in texts]) if texts else np.zeros((0, DIMENSIONS))

    def embed_query(self, text: str) -> np.ndarray:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> np.ndarray:
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
        vector = np.random.default_rng(seed).standard_normal(DIMENSIONS).astype(np.float32)
        return vector / np.linalg.norm(vector)
