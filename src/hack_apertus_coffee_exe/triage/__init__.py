"""Triage pipeline for ranking Federal Court judgements by relevance to Suva."""

from .cache import TriageCache
from .config import TriageConfig, load_config
from .date_filter import (
    DateRange,
    OutOfDateRangeError,
    SkippedItem,
    extract_judgement_date,
    filter_items_in_date_range,
)
from .embeddings import (
    Embedder,
    GatewayEmbedder,
    ReferenceMatch,
    SentenceTransformerEmbedder,
    TfidfEmbedder,
    chunk_text,
    cosine_similarity_matrix,
    score_against_corpus,
)
from .export import export_period, format_recall_list, write_ranking_csv, write_recall_list
from .pipeline import TriagePipeline, TriageResult, load_crawl_items
from .reference_corpus import ReferenceCase, load_reference_corpus
from .relevance import ApertusRelevanceClient
from .relevance_period import run as run_relevance

__all__ = [
    "ApertusRelevanceClient",
    "DateRange",
    "Embedder",
    "GatewayEmbedder",
    "OutOfDateRangeError",
    "ReferenceCase",
    "ReferenceMatch",
    "SentenceTransformerEmbedder",
    "SkippedItem",
    "TfidfEmbedder",
    "TriageCache",
    "TriageConfig",
    "TriagePipeline",
    "TriageResult",
    "chunk_text",
    "cosine_similarity_matrix",
    "export_period",
    "format_recall_list",
    "extract_judgement_date",
    "filter_items_in_date_range",
    "load_config",
    "load_crawl_items",
    "load_reference_corpus",
    "run_relevance",
    "score_against_corpus",
    "write_ranking_csv",
    "write_recall_list",
]
