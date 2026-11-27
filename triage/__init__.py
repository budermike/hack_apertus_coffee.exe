"""Triage pipeline for ranking Federal Court judgements by relevance to Suva."""

from triage.date_filter import (
    DateRange,
    OutOfDateRangeError,
    SkippedItem,
    extract_judgement_date,
    filter_items_in_date_range,
)
from triage.embeddings import (
    Embedder,
    GatewayEmbedder,
    ReferenceMatch,
    SentenceTransformerEmbedder,
    TfidfEmbedder,
    chunk_text,
    cosine_similarity_matrix,
    score_against_corpus,
)
from triage.pipeline import TriagePipeline, TriageResult, load_crawl_items
from triage.reference_corpus import ReferenceCase, load_reference_corpus

__all__ = [
    "DateRange",
    "Embedder",
    "GatewayEmbedder",
    "OutOfDateRangeError",
    "ReferenceCase",
    "ReferenceMatch",
    "SentenceTransformerEmbedder",
    "SkippedItem",
    "TfidfEmbedder",
    "TriagePipeline",
    "TriageResult",
    "chunk_text",
    "cosine_similarity_matrix",
    "extract_judgement_date",
    "filter_items_in_date_range",
    "load_crawl_items",
    "load_reference_corpus",
    "score_against_corpus",
]
