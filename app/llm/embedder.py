"""Gemini embeddings — best-effort, batched, task_type aware.

No key / any failure → embed() returns None and callers fall back to keyword
recall. Semantic is an optional layer on top of a working spine, never required.
"""

import logging
import numpy as np

from app.config.settings import settings

_MODEL = "gemini-embedding-001"
_DIM = 768  # gemini-embedding-001 native 3072; reduced to 768 (smaller BLOB, cosine-normalized)


class Embedder:
    def __init__(self, api_key: str = None, model: str = _MODEL):
        self.model = model
        self._client = None
        key = api_key or settings.GEMINI_API_KEY
        if key:
            try:
                from google import genai
                self._client = genai.Client(api_key=key)
            except Exception as e:
                logging.warning(f"Embedder init failed, semantic off: {e}")

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def embed(self, texts, task_type: str = "RETRIEVAL_DOCUMENT"):
        """list[str] → list[np.ndarray], or None on disabled/failure.

        task_type: RETRIEVAL_DOCUMENT for stored facts/memories,
        RETRIEVAL_QUERY for the incoming message. Gemini uses it to align
        query and document spaces, improving recall for free.
        """
        texts = [t for t in (texts or []) if t]
        if not self._client or not texts:
            return None
        try:
            from google.genai import types
            resp = self._client.models.embed_content(
                model=self.model,
                contents=texts,
                config=types.EmbedContentConfig(
                    task_type=task_type, output_dimensionality=_DIM),
            )
            return [np.asarray(e.values, dtype=np.float32) for e in resp.embeddings]
        except Exception as e:
            # Rate-limit / network / anything → degrade to keyword, never crash a turn.
            logging.warning(f"embed failed ({len(texts)} texts): {e}")
            return None

    def embed_one(self, text: str, task_type: str = "RETRIEVAL_QUERY"):
        got = self.embed([text], task_type)
        return got[0] if got else None
