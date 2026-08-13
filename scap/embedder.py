"""SCAP v2 — Optional embedding generator for latent space evolution.

Uses sentence-transformers all-MiniLM-L6-v2 (384-dim) when available.
Gracefully degrades to None when sentence-transformers is not installed,
allowing SCAP to retain its zero-LLM-dependency core.

Install with:  pip install scap-engine-v2[evolution]
"""
from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


class Embedder:
    """Lazy-loading embedding generator.

    The model is only loaded on first use, not at import time.
    If sentence-transformers is not installed, all embed() calls
    return None and the store falls back to FTS5/LIKE search.
    """

    MODEL_NAME = "all-MiniLM-L6-v2"
    DIMENSION = 384

    def __init__(self) -> None:
        self._model = None
        self._available: Optional[bool] = None

    @property
    def is_available(self) -> bool:
        """Check if sentence-transformers is installed."""
        if self._available is None:
            try:
                import sentence_transformers  # noqa: F401
                self._available = True
            except ImportError:
                self._available = False
                logger.info(
                    "sentence-transformers not installed; "
                    "vector search disabled. Install with: "
                    "pip install scap-engine-v2[evolution]"
                )
        return self._available

    def embed(self, text: str) -> Optional[List[float]]:
        """Generate embedding for a single text.

        Returns:
            List[float] of length DIMENSION, or None if unavailable.
        """
        if not self.is_available:
            return None
        if not text or not text.strip():
            return None
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            logger.info(f"Loading embedding model: {self.MODEL_NAME}")
            self._model = SentenceTransformer(self.MODEL_NAME)
        embedding = self._model.encode(text, normalize_embeddings=True)
        return embedding.tolist()

    def embed_batch(self, texts: List[str]) -> Optional[List[List[float]]]:
        """Generate embeddings for multiple texts.

        Returns:
            List of List[float], or None if unavailable.
        """
        if not self.is_available:
            return None
        if not texts:
            return []
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            logger.info(f"Loading embedding model: {self.MODEL_NAME}")
            self._model = SentenceTransformer(self.MODEL_NAME)
        # Filter out empty strings
        valid_texts = [t if t and t.strip() else " " for t in texts]
        embeddings = self._model.encode(
            valid_texts, normalize_embeddings=True, show_progress_bar=False
        )
        return [e.tolist() for e in embeddings]
