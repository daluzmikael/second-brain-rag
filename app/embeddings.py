"""Embedding backends.

Three of them, chosen by EMBEDDING_BACKEND:

  local   a static model (model2vec) running on this machine. Encodes a query in
          ~0.1ms against ~800ms for an OpenAI round trip, embeds the whole vault
          in a hundredth of a second, costs nothing and works offline. This is
          the default: at this vault's size the hosted model's extra nuance is
          swamped by the keyword and title signals in search.py anyway.
  openai  the hosted model, for when embedding quality matters more than latency.
  fake    deterministic hashed bag-of-words. No network, no key, no model file.
          Lets the whole pipeline be exercised in tests.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import time

from app.config import (
    EMBED_BATCH_SIZE,
    EMBEDDING_BACKEND,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    FAKE_EMBEDDINGS,
    LOCAL_EMBEDDING_MODEL,
    OPENAI_API_KEY,
    require_api_key,
)

_client = None
_local_model = None
_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def active_backend() -> str:
    if FAKE_EMBEDDINGS:
        return "fake"
    return "local" if EMBEDDING_BACKEND == "local" else "openai"


def embedding_identity() -> str:
    """Identifies the vectors in an index, so a backend swap forces a rebuild."""
    backend = active_backend()
    if backend == "fake":
        return "fake:hashed"
    if backend == "local":
        return f"local:{LOCAL_EMBEDDING_MODEL}"
    return f"openai:{EMBEDDING_MODEL}"


# --- local ------------------------------------------------------------------
def _get_local_model():
    global _local_model
    if _local_model is None:
        # The hub client prints a download progress bar even for a fully cached
        # model, which corrupts CLI output.
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        try:
            from model2vec import StaticModel
        except ImportError as error:  # pragma: no cover
            raise RuntimeError(
                "EMBEDDING_BACKEND=local needs model2vec.\n"
                "  Install it:  .venv/bin/pip install model2vec\n"
                "  Or set EMBEDDING_BACKEND=openai in .env."
            ) from error
        _local_model = StaticModel.from_pretrained(LOCAL_EMBEDDING_MODEL)
    return _local_model


def local_dimensions() -> int:
    return int(_get_local_model().dim)


def warm_up() -> None:
    """Load the local model ahead of the first query, if one is in use."""
    if active_backend() == "local":
        _get_local_model().encode(["warm"])


# --- fake -------------------------------------------------------------------
def _fake_embedding(text: str) -> list[float]:
    """Hashing-trick bag of words. Deterministic, offline, lexically meaningful."""
    vector = [0.0] * EMBEDDING_DIMENSIONS
    words = _WORD_RE.findall(text.lower())
    if not words:
        return vector
    for word in words:
        digest = hashlib.blake2b(word.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm:
        vector = [value / norm for value in vector]
    return vector


# --- openai -----------------------------------------------------------------
def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI

        require_api_key()
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def _embed_batch_remote(texts: list[str], attempts: int = 5) -> list[list[float]]:
    client = _get_client()
    delay = 1.0
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
            return [item.embedding for item in response.data]
        except Exception as error:  # rate limits, transient network failures
            last_error = error
            name = type(error).__name__
            if name in {"AuthenticationError", "PermissionDeniedError", "BadRequestError"}:
                raise
            if attempt == attempts - 1:
                break
            time.sleep(delay)
            delay = min(delay * 2, 16.0)
    raise RuntimeError(f"Embedding request failed after {attempts} attempts: {last_error}")


# --- entry points -----------------------------------------------------------
def embed_texts(texts: list[str], progress=None) -> list[list[float]]:
    """Embed a list of texts. `progress(done, total)` is optional."""
    if not texts:
        return []

    backend = active_backend()

    if backend == "local":
        vectors = _get_local_model().encode(texts)
        if progress:
            progress(len(texts), len(texts))
        return [row.tolist() for row in vectors]

    if backend == "fake":
        vectors = [_fake_embedding(text) for text in texts]
        if progress:
            progress(len(texts), len(texts))
        return vectors

    out: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start : start + EMBED_BATCH_SIZE]
        out.extend(_embed_batch_remote(batch))
        if progress:
            progress(min(start + len(batch), len(texts)), len(texts))
    return out


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]
