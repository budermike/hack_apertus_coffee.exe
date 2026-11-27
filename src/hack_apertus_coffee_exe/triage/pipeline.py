"""End-to-end Triage pipeline combining cosine similarity and Apertus v1.5."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import date
import json
import logging
from pathlib import Path
import re
from typing import Any, Sequence

import numpy as np

from .cache import TriageCache, sha256_text
from .config import AMPEL_GREEN, TriageConfig
from .date_filter import (
    DateRange,
    SkippedItem,
    check_date_in_range,
    extract_judgement_date,
    filter_items_in_date_range,
)
from .embeddings import (
    Embedder,
    SimilarityScore,
    TfidfEmbedder,
    build_reference_index,
    embed_chunks,
    embedder_fingerprint,
    score_chunk_vectors,
)
from .llm import ApertusTriageClient, TriageDecision
from .reference_corpus import ReferenceCase, load_reference_corpus

logger = logging.getLogger(__name__)

# Crawl record fields that are never metadata (raw HTML is large and not needed).
_DROP_FIELDS = ("content_html",)

_KEYWORD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Suva", re.compile(r"\bsuva\b", re.IGNORECASE)),
    ("UVG/LAA/LAINF", re.compile(r"\b(?:uvg|uvv|laa|olaa|lainf)\b", re.IGNORECASE)),
    ("Unfall/Accident", re.compile(r"\b(?:unfall|unfallversicherung|accident|infortunio)\b", re.IGNORECASE)),
    ("Berufskrankheit", re.compile(r"\b(?:berufskrankheit|maladie professionnelle|malattia professionale)\b", re.IGNORECASE)),
    ("ATSG/LPGA", re.compile(r"\b(?:atsg|lpga)\b", re.IGNORECASE)),
    ("Körperschädigung", re.compile(r"\b(?:körperschädigung|lésion corporelle)\b", re.IGNORECASE)),
)

_CHAMBER_RE = re.compile(r"\b(\d+[A-Z])_\d+/\d{4}\b")


@dataclass(frozen=True)
class CrawlItem:
    """Normalized representation of one scraped judgement JSON from Crawl."""

    case_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    language: str | None = None


@dataclass(frozen=True)
class TriageResult:
    """Output record produced by Triage for Summary and Present.

    Field names follow the shared schema (issues #11, #13, #33). The old PR #34
    names (``relevance_score``, ``reason``, ``top_reference_matches``) are kept
    as read-only aliases and are also written by :meth:`to_dict`.
    """

    case_id: str
    final_score: float  # blended Apertus + cosine (or heuristic without LLM), 0-100
    cosine_score: float  # top-k mean cosine similarity * 100 (issue #11)
    relevant: bool  # True when the traffic light is green
    relevance_reason: str
    cosine_max_similarity: float
    cosine_topk_similarity: float
    apertus_score: float | None
    keyword_hits: list[str]
    key_topics: list[str]
    similar_cases: list[dict[str, Any]]
    metadata: dict[str, Any]
    language: str
    embedder: str
    llm_model: str | None
    judgement_date: str | None = None
    rank: int | None = None  # 1 = highest score within the scored period
    ampel: str | None = None  # gruen / gelb / rot
    rank_score: float | None = None  # score that decided rank and ampel (config.rank_by)

    @property
    def relevance_score(self) -> float:
        return self.final_score

    @property
    def reason(self) -> str:
        return self.relevance_reason

    @property
    def top_reference_matches(self) -> list[dict[str, Any]]:
        return self.similar_cases

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["relevance_score"] = self.final_score
        data["reason"] = self.relevance_reason
        data["top_reference_matches"] = self.similar_cases
        return data


def detect_language(text: str, metadata: dict[str, Any] | None = None) -> str:
    """Return 'de', 'fr', or 'it' from metadata or lightweight token cues."""
    if metadata:
        for key in ("language", "lang", "sprache", "langue"):
            val = metadata.get(key)
            if isinstance(val, str) and val.strip():
                short = val.strip().lower()[:2]
                if short in ("de", "fr", "it"):
                    return short

    sample = text[:3000].lower()
    scores = {
        "de": sum(len(re.findall(p, sample)) for p in (r"\bund\b", r"\bder\b", r"\bdie\b", r"\bgegen\b", r"\bbeschwerde\b")),
        "fr": sum(len(re.findall(p, sample)) for p in (r"\bet\b", r"\bles\b", r"\bcontre\b", r"\brecours\b", r"\bconsidérant\b")),
        "it": sum(len(re.findall(p, sample)) for p in (r"\bcontro\b", r"\bricorso\b", r"\bconsiderando\b", r"\bdella\b", r"\bdegli\b")),
    }
    best = max(scores, key=lambda lang: scores[lang])
    return best if scores[best] > 0 else "de"


def extract_keyword_hits(text: str, metadata: dict[str, Any] | None = None) -> list[str]:
    """Identify Suva / accident-insurance legal signals in text and metadata."""
    haystack = text
    if metadata:
        haystack = f"{json.dumps(metadata, ensure_ascii=False)}\n{text}"
    hits = [label for label, pattern in _KEYWORD_PATTERNS if pattern.search(haystack)]

    case_id = str((metadata or {}).get("case_id") or (metadata or {}).get("id") or "")
    chamber_match = _CHAMBER_RE.search(case_id) or _CHAMBER_RE.search(text[:1000])
    if chamber_match and chamber_match.group(1) == "8C":
        hits.append("Chamber:8C")
    return hits


def _normalize_crawl_dict(raw: dict[str, Any], fallback_id: str) -> CrawlItem:
    """Extract metadata and judgement text from flexible Crawl JSON shapes."""
    metadata_block = raw.get("metadata")
    metadata: dict[str, Any] = dict(metadata_block) if isinstance(metadata_block, dict) else {}

    # Preserve top-level metadata fields if Crawl uses a flat dict instead of nested "metadata".
    text_keys = ("judgement_text", "judgment_text", "text", "full_text", "content", "body")
    text = ""
    for key in text_keys:
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            text = val.strip()
            break

    for key, val in raw.items():
        if key in text_keys or key in _DROP_FIELDS or key == "metadata" or key in metadata:
            continue
        metadata[key] = val
    for key in _DROP_FIELDS:
        metadata.pop(key, None)

    case_id = ""
    for id_key in ("case_id", "urteil_nr", "docket", "id", "doc_id", "reference", "title"):
        candidate = metadata.get(id_key) or raw.get(id_key)
        if isinstance(candidate, str) and candidate.strip():
            case_id = candidate.strip()
            break
    if not case_id:
        match = _CHAMBER_RE.search(text[:1000])
        case_id = match.group(0) if match else fallback_id

    lang = detect_language(text, metadata)
    return CrawlItem(case_id=case_id, text=text, metadata=metadata, language=lang)


def load_crawl_items(input_path: str | Path) -> list[CrawlItem]:
    """Load scraped judgement JSON(s) from a file (dict or list) or directory."""
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"Crawl input path not found: {path}")

    # The crawler stores data/raw/<YYYY-MM>/<date>_<case>.json next to bookkeeping
    # files such as data/raw/_index.json; walk sub-folders and skip "_" files.
    files = (
        sorted(p for p in path.rglob("*.json") if not p.name.startswith("_"))
        if path.is_dir()
        else [path]
    )
    if not files:
        raise ValueError(f"No .json files found in {path}")

    items: list[CrawlItem] = []
    for file_path in files:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            items.append(_normalize_crawl_dict(payload, fallback_id=file_path.stem))
        elif isinstance(payload, list):
            for idx, entry in enumerate(payload):
                if not isinstance(entry, dict):
                    raise ValueError(f"Expected JSON object at index {idx} in {file_path}")
                items.append(_normalize_crawl_dict(entry, fallback_id=f"{file_path.stem}_{idx}"))
        else:
            raise ValueError(f"Unsupported JSON root type in {file_path}: {type(payload).__name__}")
    return items


def _heuristic_relevance_score(
    similarity: SimilarityScore,
    keyword_hits: Sequence[str],
    embedder_name: str = "",
) -> tuple[float, str]:
    """Compute fallback score (0-100) and explanation when LLM triage is disabled."""
    sim_component = 0.65 * similarity.max_similarity + 0.35 * similarity.top_k_mean_similarity
    # Sparse TF-IDF cosine values are naturally lower (typically 0.10-0.40) than dense embeddings (0.40-0.85).
    sim_scale = 185.0 if embedder_name.startswith("tfidf") else 115.0
    sim_points = min(75.0, max(0.0, sim_component * sim_scale))

    kw_weight = 0.0
    if "Suva" in keyword_hits:
        kw_weight += 10.0
    if "UVG/LAA/LAINF" in keyword_hits:
        kw_weight += 8.0
    if "Chamber:8C" in keyword_hits:
        kw_weight += 4.0
    if any(k in keyword_hits for k in ("Unfall/Accident", "Berufskrankheit", "Körperschädigung")):
        kw_weight += 3.0
    kw_points = min(25.0, kw_weight)

    total = round(min(100.0, sim_points + kw_points), 2)
    top_match_desc = (
        f"Nächster Suva-Präzedenzfall: {similarity.top_matches[0].case_id} "
        f"(Cosine={similarity.top_matches[0].similarity:.3f})."
        if similarity.top_matches
        else "Keine Referenztreffer."
    )
    kw_desc = ", ".join(keyword_hits) if keyword_hits else "keine Suva/UVG-Schlüsselwörter"
    reason = f"[Embedding-Only-Modus] {top_match_desc} Gefundene Signale: {kw_desc}."
    return total, reason


class TriagePipeline:
    """Ranks scraped Federal Court judgements by relevance to Suva."""

    def __init__(
        self,
        reference_cases: Sequence[ReferenceCase] | None = None,
        judgements_dir: str | Path = "judgements",
        embedder: Embedder | None = None,
        llm_client: ApertusTriageClient | None = None,
        config: TriageConfig | None = None,
        date_range: DateRange | None = None,
        cache: TriageCache | None = None,
        top_k: int | None = None,
        llm_weight: float | None = None,
    ) -> None:
        cfg = config or TriageConfig()
        overrides = {k: v for k, v in (("top_k", top_k), ("llm_weight", llm_weight)) if v is not None}
        self.config = replace(cfg, **overrides) if overrides else cfg
        self.reference_cases = (
            list(reference_cases)
            if reference_cases is not None
            else load_reference_corpus(judgements_dir)
        )
        self.embedder: Embedder = embedder if embedder is not None else TfidfEmbedder()
        self.llm_client = llm_client
        # Explicit period only (no rolling default); None disables the date filter.
        self.date_range = date_range
        self.cache = cache
        self.last_skipped: list[SkippedItem] = []
        self.last_excluded_references: list[str] = []

        # Fit TF-IDF once on all summaries (vocabulary only, no labels), so the
        # vectors - and the cache - stay valid when the reference subset changes.
        if isinstance(self.embedder, TfidfEmbedder) and not self.embedder.vocab_:
            all_texts = [case.text_for_language(None) for case in self.reference_cases]
            self.embedder.fit(all_texts)
        self.fingerprint = embedder_fingerprint(self.embedder)

        self._ref_vecs_by_lang: dict[str, Any] = {}
        for lang_key in ("de", "fr", "all"):
            arg = None if lang_key == "all" else lang_key
            _, vecs = build_reference_index(self.reference_cases, self.embedder, language=arg)
            self._ref_vecs_by_lang[lang_key] = vecs

    @property
    def top_k(self) -> int:
        return self.config.top_k

    @property
    def llm_weight(self) -> float:
        return self.config.llm_weight

    # ------------------------------------------------------------ references

    def active_reference_indices(self, candidate_ids: set[str]) -> list[int]:
        """Reference summaries allowed for this run (no data leakage).

        A summary is dropped when its case is itself being scored, or when its
        decision date lies in or after the scored period. Summaries without a
        parseable date are kept (with a warning).
        """
        active: list[int] = []
        excluded: list[str] = []
        since = self.date_range.since if self.date_range else None
        for idx, case in enumerate(self.reference_cases):
            if case.case_id in candidate_ids:
                excluded.append(case.case_id)
            elif since is not None and case.decision_date is not None and case.decision_date >= since:
                excluded.append(case.case_id)
            else:
                if since is not None and case.decision_date is None:
                    logger.warning("Reference %s has no decision date; kept as reference", case.case_id)
                active.append(idx)
        self.last_excluded_references = excluded
        return active

    def _ref_vecs_for(self, language: str | None) -> Any:
        lang = (language or "").strip().lower()[:2]
        if lang in ("de", "fr"):
            return self._ref_vecs_by_lang[lang]
        return self._ref_vecs_by_lang["all"]

    # --------------------------------------------------------------- scoring

    def _chunk_vectors(self, item: CrawlItem, date_str: str | None, text_hash: str) -> np.ndarray:
        if self.cache is not None:
            cached = self.cache.get_vectors(item.case_id, date_str, text_hash, self.fingerprint)
            if cached is not None:
                self.cache.stats.embedding_hits += 1
                return cached
        vecs = embed_chunks(item.text, self.embedder)
        if self.cache is not None:
            self.cache.stats.embedding_misses += 1
            self.cache.put_vectors(item.case_id, date_str, text_hash, self.fingerprint, vecs)
        return vecs

    def _decision(
        self,
        item: CrawlItem,
        date_str: str | None,
        text_hash: str,
        similarity: SimilarityScore,
        keyword_hits: list[str],
    ) -> TriageDecision:
        assert self.llm_client is not None
        messages = self.llm_client.build_messages(
            item.case_id, item.text, item.metadata, similarity, keyword_hits
        )
        key = self.llm_client.request_key(messages)
        if self.cache is not None:
            cached = self.cache.get_decision(item.case_id, date_str, text_hash, key)
            if cached is not None:
                self.cache.stats.decision_hits += 1
                return TriageDecision.from_dict(cached)
        decision = self.llm_client.complete(messages)
        if self.cache is not None:
            self.cache.stats.decision_misses += 1
            self.cache.put_decision(item.case_id, date_str, text_hash, key, decision.to_dict())
            self.cache.save()  # Apertus calls are the expensive part; persist each one
        return decision

    def _score_one(
        self, item: CrawlItem, resolved_date: date | None, ref_idx: list[int]
    ) -> tuple[TriageResult, np.ndarray]:
        date_str = resolved_date.isoformat() if resolved_date else None
        text_hash = sha256_text(item.text)
        chunk_vecs = self._chunk_vectors(item, date_str, text_hash)

        cases = [self.reference_cases[i] for i in ref_idx]
        ref_vecs = self._ref_vecs_for(item.language)
        ref_vecs = ref_vecs[ref_idx] if len(ref_idx) else ref_vecs[:0]
        similarity = score_chunk_vectors(
            chunk_vecs, cases, ref_vecs, language=item.language, top_k=self.config.top_k
        )
        keyword_hits = extract_keyword_hits(item.text, item.metadata)
        cosine_score = round(min(100.0, max(0.0, similarity.top_k_mean_similarity * 100.0)), 2)

        if self.llm_client is not None:
            decision = self._decision(item, date_str, text_hash, similarity, keyword_hits)
            w = self.config.llm_weight
            final_score = round(w * decision.apertus_score + (1.0 - w) * cosine_score, 2)
            reason = decision.reason
            key_topics = decision.key_topics
            apertus_score: float | None = round(decision.apertus_score, 2)
            llm_model: str | None = decision.model
        else:
            final_score, reason = _heuristic_relevance_score(
                similarity, keyword_hits, embedder_name=self.embedder.name
            )
            key_topics = []
            apertus_score = None
            llm_model = None

        result = TriageResult(
            case_id=item.case_id,
            final_score=final_score,
            cosine_score=cosine_score,
            relevant=False,
            relevance_reason=reason,
            cosine_max_similarity=similarity.max_similarity,
            cosine_topk_similarity=similarity.top_k_mean_similarity,
            apertus_score=apertus_score,
            keyword_hits=keyword_hits,
            key_topics=key_topics,
            similar_cases=[asdict(m) for m in similarity.top_matches],
            metadata=item.metadata,
            language=item.language or "de",
            embedder=self.embedder.name,
            llm_model=llm_model,
            judgement_date=date_str,
        )
        return result, chunk_vecs

    def _finalize(self, results: list[TriageResult]) -> list[TriageResult]:
        """Sort, rank and colour by the configured score."""
        adjusted = [
            replace(res, rank_score=res.final_score if self.config.rank_by == "final_score" else res.cosine_score)
            for res in results
        ]
        adjusted.sort(key=lambda r: (r.rank_score, r.cosine_max_similarity, r.case_id), reverse=True)
        final: list[TriageResult] = []
        for rank, res in enumerate(adjusted, start=1):
            ampel = self.config.ampel_for(res.rank_score)
            final.append(replace(res, rank=rank, ampel=ampel, relevant=ampel == AMPEL_GREEN))
        return final

    def score_item(self, item: CrawlItem) -> TriageResult:
        """Validate the date range (hard filter), then score one judgement."""
        if self.date_range is not None:
            resolved_date = check_date_in_range(item.text, item.metadata, self.date_range)
        else:
            resolved_date = extract_judgement_date(item.text, item.metadata)
        ref_idx = self.active_reference_indices({item.case_id})
        result, _ = self._score_one(item, resolved_date, ref_idx)
        try:
            return self._finalize([result])[0]
        finally:
            if self.cache is not None:
                self.cache.save()

    def score_items(self, items: Sequence[CrawlItem]) -> list[TriageResult]:
        """Hard-filter by date, score, then rank (1 = best)."""
        if self.date_range is not None:
            accepted, skipped = filter_items_in_date_range(items, self.date_range)
            self.last_skipped = skipped
            for entry in skipped:
                logger.warning(
                    "Hard date filter skipped %s (date=%s): %s",
                    entry.case_id,
                    entry.parsed_date,
                    entry.reason,
                )
        else:
            accepted = list(items)
            self.last_skipped = []

        ref_idx = self.active_reference_indices({item.case_id for item in accepted})
        results: list[TriageResult] = []
        try:
            for item in accepted:
                resolved = extract_judgement_date(item.text, item.metadata)
                result, _ = self._score_one(item, resolved, ref_idx)
                results.append(result)
        finally:
            if self.cache is not None:
                self.cache.save()
        return self._finalize(results)
