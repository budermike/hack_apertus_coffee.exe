"""Embedding backends and cosine-similarity scoring against Suva precedents."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import math
import re
from typing import Protocol, Sequence

import numpy as np

from triage.reference_corpus import ReferenceCase

_TOKEN_RE = re.compile(r"[0-9a-zA-ZäöüÄÖÜàâéèêëîïôùûüçÀÂÉÈÊËÎÏÔÙÛÜÇ]+")


class Embedder(Protocol):
    """Protocol for text embedding backends returning L2-normalized vectors."""

    name: str

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """Embed a sequence of strings into an (N, D) float32 normalized matrix."""
        ...


@dataclass(frozen=True)
class ReferenceMatch:
    """Similarity match between a candidate judgement and a Suva reference case."""

    case_id: str
    title: str
    similarity: float
    summary_snippet: str
    bger_url: str


@dataclass(frozen=True)
class SimilarityScore:
    """Aggregated cosine-similarity scores and top reference precedents."""

    max_similarity: float
    top_k_mean_similarity: float
    top_matches: list[ReferenceMatch]


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


def cosine_similarity_matrix(query_vecs: np.ndarray, ref_vecs: np.ndarray) -> np.ndarray:
    """Compute (N_query, N_ref) cosine similarity matrix."""
    if query_vecs.size == 0 or ref_vecs.size == 0:
        return np.zeros((len(query_vecs), len(ref_vecs)), dtype=np.float32)
    q = l2_normalize(query_vecs)
    r = l2_normalize(ref_vecs)
    sims = q @ r.T
    return np.clip(sims, -1.0, 1.0)


def chunk_text(
    text: str,
    max_words: int = 350,
    overlap_words: int = 50,
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


class TfidfEmbedder:
    """Zero-dependency multilingual word + char-ngram TF-IDF embedder.

    Fits vocabulary and IDF weights on the reference corpus (plus optional domain
    documents) and produces L2-normalized vectors suitable for cosine similarity
    in air-gapped environments without external model weights.
    """

    def __init__(self, use_char_ngrams: bool = True, char_ngram_range: tuple[int, int] = (4, 5)) -> None:
        self.name = "tfidf-word-char45" if use_char_ngrams else "tfidf-word"
        self.use_char_ngrams = use_char_ngrams
        self.char_ngram_range = char_ngram_range
        self.vocab_: dict[str, int] = {}
        self.idf_: np.ndarray = np.array([], dtype=np.float32)

    def _featurize(self, text: str) -> Counter[str]:
        tokens = [t.lower() for t in _TOKEN_RE.findall(text)]
        counts: Counter[str] = Counter(f"w:{t}" for t in tokens)
        if self.use_char_ngrams:
            min_n, max_n = self.char_ngram_range
            for tok in tokens:
                if len(tok) < min_n:
                    continue
                padded = f"<{tok}>"
                for n in range(min_n, max_n + 1):
                    for i in range(len(padded) - n + 1):
                        counts[f"c:{padded[i : i + n]}"] += 1
        return counts

    def fit(self, documents: Sequence[str]) -> "TfidfEmbedder":
        """Fit vocabulary and smooth IDF weights on reference documents."""
        doc_freq: Counter[str] = Counter()
        n_docs = len(documents)
        for doc in documents:
            feats = self._featurize(doc)
            for term in feats:
                doc_freq[term] += 1

        # Keep terms appearing in at least 1 doc; deterministic ordering.
        terms = sorted(doc_freq.keys())
        self.vocab_ = {term: idx for idx, term in enumerate(terms)}
        idf = np.zeros(len(terms), dtype=np.float32)
        for term, idx in self.vocab_.items():
            df = doc_freq[term]
            idf[idx] = math.log((1.0 + n_docs) / (1.0 + df)) + 1.0
        self.idf_ = idf
        return self

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """Transform texts into L2-normalized TF-IDF vectors."""
        if not self.vocab_:
            raise RuntimeError("TfidfEmbedder must be fitted before calling embed().")
        mat = np.zeros((len(texts), len(self.vocab_)), dtype=np.float32)
        for row_idx, text in enumerate(texts):
            feats = self._featurize(text)
            for term, count in feats.items():
                col_idx = self.vocab_.get(term)
                if col_idx is not None:
                    tf = 1.0 + math.log(count)
                    mat[row_idx, col_idx] = tf * self.idf_[col_idx]
        return l2_normalize(mat)


class GatewayEmbedder:
    """OpenAI-compatible embeddings client for the Keera gateway or local vLLM/TEI."""

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        batch_size: int = 32,
        timeout: int = 60,
        max_retries: int = 3,
    ) -> None:
        from openai import OpenAI

        self.name = f"gateway:{model}"
        self.model = model
        self.batch_size = batch_size
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
        )

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = [t if t.strip() else " " for t in texts[start : start + self.batch_size]]
            resp = self._client.embeddings.create(model=self.model, input=batch)
            ordered = sorted(resp.data, key=lambda item: item.index)
            vectors.extend(item.embedding for item in ordered)
        return l2_normalize(np.asarray(vectors, dtype=np.float32))


class SentenceTransformerEmbedder:
    """Optional local open-weights SentenceTransformer embedder (e.g. BAAI/bge-m3)."""

    def __init__(self, model_name: str = "BAAI/bge-m3", device: str | None = None) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers is not installed. Install it (after team approval) "
                "or use --embedder tfidf / --embedder gateway."
            ) from exc

        self.name = f"sentence-transformers:{model_name}"
        self._model = SentenceTransformer(model_name, device=device)

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        vecs = self._model.encode(
            list(texts),
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vecs, dtype=np.float32)


def build_reference_index(
    cases: Sequence[ReferenceCase],
    embedder: Embedder,
    language: str | None = None,
) -> tuple[list[str], np.ndarray]:
    """Compute normalized reference embeddings for all Suva summary cases."""
    ref_texts = [case.text_for_language(language) for case in cases]
    if isinstance(embedder, TfidfEmbedder) and not embedder.vocab_:
        embedder.fit(ref_texts)
    ref_vecs = embedder.embed(ref_texts)
    return ref_texts, ref_vecs


def embedder_fingerprint(embedder: Embedder, max_words: int = 350, overlap_words: int = 50) -> str:
    """Identify everything that determines a judgement's chunk vectors.

    Used as the cache key next to the text hash: if the embedder, its fitted
    vocabulary or the chunking changes, cached vectors are recomputed.
    """
    parts = [embedder.name, f"chunk={max_words}/{overlap_words}"]
    if isinstance(embedder, TfidfEmbedder):
        digest = hashlib.sha256()
        digest.update("\n".join(sorted(embedder.vocab_)).encode("utf-8"))
        digest.update(np.asarray(embedder.idf_, dtype=np.float32).tobytes())
        parts.append(f"vocab={digest.hexdigest()[:16]}")
    return "|".join(parts)


def embed_chunks(
    text: str,
    embedder: Embedder,
    max_words: int = 350,
    overlap_words: int = 50,
) -> np.ndarray:
    """Chunk a judgement and embed the chunks; returns an (n_chunks, D) matrix."""
    chunks = chunk_text(text, max_words=max_words, overlap_words=overlap_words)
    if not chunks:
        return np.zeros((0, 0), dtype=np.float32)
    return np.asarray(embedder.embed(chunks), dtype=np.float32)


def max_chunk_similarity(a_chunks: np.ndarray, b_chunks: np.ndarray) -> float:
    """Judgement-to-judgement similarity: best-matching pair of chunks."""
    if a_chunks.size == 0 or b_chunks.size == 0 or a_chunks.shape[1] != b_chunks.shape[1]:
        return 0.0
    return float(cosine_similarity_matrix(a_chunks, b_chunks).max())


def score_chunk_vectors(
    chunk_vecs: np.ndarray,
    cases: Sequence[ReferenceCase],
    ref_vecs: np.ndarray,
    language: str | None = None,
    top_k: int = 3,
) -> SimilarityScore:
    """Score pre-computed chunk vectors against the given reference cases.

    ``cases`` and ``ref_vecs`` must be aligned row by row, so a caller can pass a
    subset of the corpus (e.g. without summaries from the scored period).
    """
    if chunk_vecs.size == 0 or len(cases) == 0:
        return SimilarityScore(max_similarity=0.0, top_k_mean_similarity=0.0, top_matches=[])

    sim_matrix = cosine_similarity_matrix(chunk_vecs, ref_vecs)
    # Per reference case, take the strongest matching chunk in the candidate judgement.
    per_case_sims = sim_matrix.max(axis=0)

    k = max(1, min(top_k, len(cases)))
    top_indices = np.argsort(-per_case_sims, kind="stable")[:k]
    top_scores = [float(per_case_sims[i]) for i in top_indices]

    matches: list[ReferenceMatch] = []
    for idx in top_indices:
        case = cases[int(idx)]
        ref_text = case.text_for_language(language)
        snippet = ref_text[:400].strip() + ("..." if len(ref_text) > 400 else "")
        matches.append(
            ReferenceMatch(
                case_id=case.case_id,
                title=case.title,
                similarity=round(float(per_case_sims[int(idx)]), 4),
                summary_snippet=snippet,
                bger_url=case.bger_url,
            )
        )

    return SimilarityScore(
        max_similarity=round(top_scores[0], 4),
        top_k_mean_similarity=round(float(sum(top_scores) / len(top_scores)), 4),
        top_matches=matches,
    )


def score_against_corpus(
    candidate_text: str,
    cases: Sequence[ReferenceCase],
    embedder: Embedder,
    ref_vecs: np.ndarray,
    language: str | None = None,
    top_k: int = 3,
    max_words: int = 350,
    overlap_words: int = 50,
) -> SimilarityScore:
    """Score a candidate judgement by chunked cosine similarity against Suva precedents."""
    if len(cases) == 0:
        return SimilarityScore(max_similarity=0.0, top_k_mean_similarity=0.0, top_matches=[])
    chunk_vecs = embed_chunks(candidate_text, embedder, max_words=max_words, overlap_words=overlap_words)
    return score_chunk_vectors(chunk_vecs, cases, ref_vecs, language=language, top_k=top_k)
