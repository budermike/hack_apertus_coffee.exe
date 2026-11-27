"""Shared multilingual open-weights embedding module (issue #5).

Uses ``intfloat/multilingual-e5-small`` (MIT licence, 384-dim, DE/FR/IT) locally
via ``sentence-transformers`` (MPS/CUDA/CPU) after confirming the Keera gateway
does not expose ``/v1/embeddings``. Configurable via ``EMBEDDING_MODEL`` and
``EMBEDDING_DEVICE`` environment variables for air-gapped / sovereign deployment.
"""

from __future__ import annotations

import os
import re
from typing import Sequence

import numpy as np

DEFAULT_EMBEDDING_MODEL = "intfloat/multilingual-e5-small"
DEFAULT_CHUNK_WORDS = 350
DEFAULT_OVERLAP_WORDS = 50

_SUBSTANTIVE_START_RE = re.compile(
    r"^(?:Gegenstand|Objet|Oggetto|betreffend|Sachverhalt|Faits|Fatti|Erwägungen|Considérant|Considerando)\b",
    re.MULTILINE,
)
_BOILERPLATE_FOOTER_RE = re.compile(
    r"\n(?:Die Gerichtskosten von|Les frais judiciaires|Le spese giudiziarie|Dieses Urteil wird|Le présent arrêt est communiqué|La presente sentenza è comunicata)\b.*",
    re.DOTALL,
)


def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    """Row-wise L2 normalization, leaving zero rows as zeros."""
    arr = np.asarray(matrix, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.size == 0:
        return arr
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    safe_norms = np.where(norms > 1e-12, norms, 1.0)
    return arr / safe_norms


def strip_boilerplate(text: str) -> str:
    """Drop repetitive BGer judge/party header and cost/notification footer."""
    if not text:
        return ""
    cleaned = text
    m = _SUBSTANTIVE_START_RE.search(cleaned[:4000])
    if m and m.start() > 0:
        cleaned = cleaned[m.start() :]
    cleaned = _BOILERPLATE_FOOTER_RE.sub("", cleaned)
    return cleaned.strip() or text.strip()


def chunk_text(
    text: str,
    max_words: int = DEFAULT_CHUNK_WORDS,
    overlap_words: int = DEFAULT_OVERLAP_WORDS,
) -> list[str]:
    """Split a long court judgement into overlapping word chunks."""
    words = text.split()
    if not words:
        return []
    if len(words) <= max_words:
        return [" ".join(words)]

    step = max(1, max_words - overlap_words)
    chunks: list[str] = []
    for start in range(0, len(words), step):
        end = start + max_words
        chunks.append(" ".join(words[start:end]))
        if end >= len(words):
            break
    return chunks


def chunk_judgement(
    text: str,
    *,
    division: str | None = None,
    subject: str | None = None,
    max_words: int = DEFAULT_CHUNK_WORDS,
    overlap_words: int = DEFAULT_OVERLAP_WORDS,
) -> list[str]:
    """Strip procedural boilerplate and attach division/subject context to every chunk."""
    body = strip_boilerplate(text)
    if not body:
        return []
    header_parts = [p.strip() for p in (division, subject) if p and p.strip()]
    prefix = f"[{' | '.join(header_parts)}] " if header_parts else ""
    budget = max(32, max_words - len(prefix.split()))
    raw_chunks = chunk_text(body, max_words=budget, overlap_words=min(overlap_words, budget // 2))
    return [f"{prefix}{c}" for c in raw_chunks]


def _select_device(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env_dev = os.environ.get("EMBEDDING_DEVICE")
    if env_dev:
        return env_dev
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _uses_e5_prefixes(model_name: str) -> bool:
    lower = model_name.lower()
    return "e5-" in lower or "/e5" in lower


class MultilingualEmbedder:
    """Local open-weights multilingual embedder compatible with VectorDB and Triage."""

    def __init__(
        self,
        model_name: str | None = None,
        device: str | None = None,
        batch_size: int | None = None,
    ) -> None:
        from sentence_transformers import SentenceTransformer

        resolved_model = model_name or os.environ.get("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
        resolved_device = _select_device(device)
        self.model_name = resolved_model
        self.device = resolved_device
        self.name = f"sentence-transformers:{resolved_model}"
        self.uses_e5_prefix = _uses_e5_prefixes(resolved_model)
        self.batch_size = batch_size or (128 if resolved_device in ("mps", "cuda") else 32)
        try:
            self._model = SentenceTransformer(resolved_model, device=resolved_device, local_files_only=True)
        except Exception:
            self._model = SentenceTransformer(resolved_model, device=resolved_device)
        dim_fn = getattr(self._model, "get_embedding_dimension", None) or self._model.get_sentence_embedding_dimension
        self.dim = int(dim_fn())

    def _prepare(self, texts: Sequence[str], *, is_query: bool) -> list[str]:
        if not self.uses_e5_prefix:
            return [t if t.strip() else " " for t in texts]
        prefix = "query: " if is_query else "passage: "
        out: list[str] = []
        for t in texts:
            clean = t.strip() or " "
            if clean.startswith(("query: ", "passage: ")):
                out.append(clean)
            else:
                out.append(prefix + clean)
        return out

    def embed(self, texts: Sequence[str], *, is_query: bool = False) -> np.ndarray:
        """Encode ``texts`` into an (N, dim) L2-normalized float32 matrix."""
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        prepared = self._prepare(texts, is_query=is_query)
        vecs = self._model.encode(
            prepared,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return l2_normalize(np.asarray(vecs, dtype=np.float32))

    def embed_queries(self, queries: Sequence[str]) -> np.ndarray:
        """Encode short search queries (adds 'query: ' prefix for E5 models)."""
        return self.embed(queries, is_query=True)


_DEFAULT_INSTANCE: MultilingualEmbedder | None = None


def get_default_embedder(model_name: str | None = None) -> MultilingualEmbedder:
    """Return process-wide cached MultilingualEmbedder instance."""
    global _DEFAULT_INSTANCE
    wanted = model_name or os.environ.get("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
    if _DEFAULT_INSTANCE is None or _DEFAULT_INSTANCE.model_name != wanted:
        _DEFAULT_INSTANCE = MultilingualEmbedder(model_name=wanted)
    return _DEFAULT_INSTANCE


def embed(texts: Sequence[str], *, is_query: bool = False) -> np.ndarray:
    """Embed a sequence of texts into L2-normalized float32 vectors (issue #5)."""
    return get_default_embedder().embed(texts, is_query=is_query)
