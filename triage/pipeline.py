"""End-to-end Triage pipeline combining cosine similarity and Apertus v1.5."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Sequence

from triage.embeddings import (
    Embedder,
    ReferenceMatch,
    SimilarityScore,
    TfidfEmbedder,
    build_reference_index,
    score_against_corpus,
)
from triage.llm import ApertusTriageClient, TriageDecision
from triage.reference_corpus import ReferenceCase, load_reference_corpus

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
    """Output record produced by Triage for Summary and Present."""

    case_id: str
    relevance_score: float
    relevant: bool
    reason: str
    cosine_max_similarity: float
    cosine_topk_similarity: float
    apertus_score: float | None
    keyword_hits: list[str]
    key_topics: list[str]
    top_reference_matches: list[dict[str, Any]]
    metadata: dict[str, Any]
    language: str
    embedder: str
    llm_model: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
        if key not in text_keys and key != "metadata" and key not in metadata:
            metadata[key] = val

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

    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
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
) -> tuple[float, bool, str]:
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
    relevant = total >= 45.0
    top_match_desc = (
        f"Nächster Suva-Präzedenzfall: {similarity.top_matches[0].case_id} "
        f"(Cosine={similarity.top_matches[0].similarity:.3f})."
        if similarity.top_matches
        else "Keine Referenztreffer."
    )
    kw_desc = ", ".join(keyword_hits) if keyword_hits else "keine Suva/UVG-Schlüsselwörter"
    reason = f"[Embedding-Only-Modus] {top_match_desc} Gefundene Signale: {kw_desc}."
    return total, relevant, reason


class TriagePipeline:
    """Ranks scraped Federal Court judgements by relevance to Suva."""

    def __init__(
        self,
        reference_cases: Sequence[ReferenceCase] | None = None,
        judgements_dir: str | Path = "judgements",
        embedder: Embedder | None = None,
        llm_client: ApertusTriageClient | None = None,
        top_k: int = 3,
        llm_weight: float = 0.75,
        relevance_threshold: float = 50.0,
    ) -> None:
        self.reference_cases = (
            list(reference_cases)
            if reference_cases is not None
            else load_reference_corpus(judgements_dir)
        )
        self.embedder: Embedder = embedder if embedder is not None else TfidfEmbedder()
        self.llm_client = llm_client
        self.top_k = top_k
        self.llm_weight = llm_weight
        self.relevance_threshold = relevance_threshold

        # Fit TF-IDF once on combined DE+FR vocabulary, then keep per-language reference matrices.
        if isinstance(self.embedder, TfidfEmbedder) and not self.embedder.vocab_:
            all_texts = [case.text_for_language(None) for case in self.reference_cases]
            self.embedder.fit(all_texts)

        self._ref_vecs_by_lang: dict[str, Any] = {}
        for lang_key in ("de", "fr", "all"):
            arg = None if lang_key == "all" else lang_key
            _, vecs = build_reference_index(self.reference_cases, self.embedder, language=arg)
            self._ref_vecs_by_lang[lang_key] = vecs

    def _ref_vecs_for(self, language: str | None) -> Any:
        lang = (language or "").strip().lower()[:2]
        if lang in ("de", "fr"):
            return self._ref_vecs_by_lang[lang]
        return self._ref_vecs_by_lang["all"]

    def score_item(self, item: CrawlItem) -> TriageResult:
        """Compute cosine similarity and optional Apertus v1.5 triage for one judgement."""
        similarity = score_against_corpus(
            candidate_text=item.text,
            cases=self.reference_cases,
            embedder=self.embedder,
            ref_vecs=self._ref_vecs_for(item.language),
            language=item.language,
            top_k=self.top_k,
        )
        keyword_hits = extract_keyword_hits(item.text, item.metadata)

        decision: TriageDecision | None = None
        if self.llm_client is not None:
            decision = self.llm_client.evaluate(
                case_id=item.case_id,
                judgement_text=item.text,
                metadata=item.metadata,
                similarity=similarity,
                keyword_hits=keyword_hits,
            )
            cos_scaled = min(100.0, max(0.0, similarity.max_similarity * 100.0))
            blended = self.llm_weight * decision.apertus_score + (1.0 - self.llm_weight) * cos_scaled
            relevance_score = round(blended, 2)
            relevant = decision.relevant or (relevance_score >= self.relevance_threshold)
            reason = decision.reason
            key_topics = decision.key_topics
            apertus_score: float | None = round(decision.apertus_score, 2)
            llm_model: str | None = decision.model
        else:
            relevance_score, relevant, reason = _heuristic_relevance_score(
                similarity, keyword_hits, embedder_name=self.embedder.name
            )
            key_topics = []
            apertus_score = None
            llm_model = None

        return TriageResult(
            case_id=item.case_id,
            relevance_score=relevance_score,
            relevant=relevant,
            reason=reason,
            cosine_max_similarity=similarity.max_similarity,
            cosine_topk_similarity=similarity.top_k_mean_similarity,
            apertus_score=apertus_score,
            keyword_hits=keyword_hits,
            key_topics=key_topics,
            top_reference_matches=[asdict(m) for m in similarity.top_matches],
            metadata=item.metadata,
            language=item.language or "de",
            embedder=self.embedder.name,
            llm_model=llm_model,
        )

    def score_items(self, items: Sequence[CrawlItem]) -> list[TriageResult]:
        """Score all items and return them sorted descending by relevance_score."""
        results = [self.score_item(item) for item in items]
        results.sort(key=lambda r: (r.relevance_score, r.cosine_max_similarity), reverse=True)
        return results
