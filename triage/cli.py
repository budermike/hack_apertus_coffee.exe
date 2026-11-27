"""CLI for Suva judgement Triage (gateway check, scoring, and corpus eval)."""

from __future__ import annotations

import argparse
from datetime import date
import logging
import json
from pathlib import Path
import sys
from typing import Sequence

import numpy as np

from triage.date_filter import DateRange
from triage.embeddings import (
    Embedder,
    GatewayEmbedder,
    SentenceTransformerEmbedder,
    TfidfEmbedder,
    build_reference_index,
    cosine_similarity_matrix,
)
from triage.llm import (
    ApertusTriageClient,
    GatewayConfig,
    discover_gateway_models,
    probe_gateway_embeddings,
)
from triage.cache import TriageCache
from triage.config import RATINGS, load_config
from triage.export import export_period
from triage.feedback import FeedbackStore
from triage.pipeline import TriagePipeline, load_crawl_items
from triage.reference_corpus import load_reference_corpus


def _build_embedder(
    kind: str,
    embedding_model: str | None,
    config: GatewayConfig,
) -> Embedder:
    if kind == "tfidf":
        return TfidfEmbedder()
    if kind == "gateway":
        model = embedding_model or config.embedding_model
        if not model:
            raise ValueError(
                "For --embedder gateway, specify --embedding-model <id> or set EMBEDDING_MODEL."
            )
        if not config.api_key:
            raise ValueError(
                "Missing API key: set LLM_API_KEY or APERTUS_API_KEY in .env or environment."
            )
        return GatewayEmbedder(model=model, base_url=config.base_url, api_key=config.api_key)
    if kind == "sentence-transformers":
        return SentenceTransformerEmbedder(model_name=embedding_model or "BAAI/bge-m3")
    raise ValueError(f"Unknown embedder kind: {kind}")


def _cmd_check_gateway(args: argparse.Namespace) -> int:
    cfg = GatewayConfig.from_env(model=args.model)
    models = discover_gateway_models(cfg)
    print(f"Gateway base URL: {cfg.base_url}")
    print(f"Available models ({len(models)}):")
    for m in models:
        print(f"  - {m}")

    if args.probe_embedding:
        result = probe_gateway_embeddings(args.probe_embedding, config=cfg)
        print("Embedding probe:", json.dumps(result, ensure_ascii=False))
    else:
        emb_candidates = [m for m in models if "embed" in m.lower() or "bge" in m.lower() or "e5" in m.lower()]
        if emb_candidates:
            print("Possible embedding models detected, probing first match:", emb_candidates[0])
            try:
                result = probe_gateway_embeddings(emb_candidates[0], config=cfg)
                print("Embedding probe:", json.dumps(result, ensure_ascii=False))
            except Exception as exc:
                print(f"Embedding probe failed for {emb_candidates[0]}: {exc}")
        else:
            print("No obvious embedding model ID found in /models; pass --probe-embedding <id> to test one.")
    return 0


def _resolve_date_range(args: argparse.Namespace) -> DateRange:
    """Explicit period only: ``--period 2026-Q2`` or ``--since`` + ``--until``."""
    if args.period:
        if args.since or args.until:
            raise SystemExit("Use either --period or --since/--until, not both.")
        return DateRange.from_period(args.period)
    if args.since and args.until:
        return DateRange(since=date.fromisoformat(args.since), until=date.fromisoformat(args.until))
    raise SystemExit("A scoring period is required: --period 2026-Q2 (or 2026-06), or --since and --until.")


def _cmd_score(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.top_k is not None:
        from dataclasses import replace

        config = replace(config, top_k=args.top_k)
    cfg = GatewayConfig.from_env(model=args.model, embedding_model=args.embedding_model)
    embedder = _build_embedder(args.embedder, args.embedding_model, cfg)
    llm_client = None if args.no_llm else ApertusTriageClient(config=cfg)
    date_range = _resolve_date_range(args)
    cache = None if args.no_cache else TriageCache(args.cache_dir or config.cache_dir)
    feedback = None if args.no_feedback else FeedbackStore(args.feedback_file or config.feedback.store_path)

    pipeline = TriagePipeline(
        judgements_dir=args.judgements_dir,
        embedder=embedder,
        llm_client=llm_client,
        config=config,
        date_range=date_range,
        cache=cache,
        feedback=feedback,
    )
    items = load_crawl_items(args.input)
    results = pipeline.score_items(items)
    payload = [res.to_dict() for res in results]

    print(
        f"Period {date_range.since.isoformat()}..{date_range.until.isoformat()}: "
        f"{len(results)} scored, {len(pipeline.last_skipped)} outside period, "
        f"{len(pipeline.last_excluded_references)} reference summaries excluded (leakage).",
        file=sys.stderr,
    )
    if args.verbose:
        for skipped in pipeline.last_skipped:
            print(f"  - skipped {skipped.case_id} (date={skipped.parsed_date}): {skipped.reason}", file=sys.stderr)
    if cache is not None:
        print(f"Cache: {cache.stats.summary()} ({cache.root})", file=sys.stderr)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {len(payload)} triage result(s) to {out_path}", file=sys.stderr)
    elif args.no_export:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    if not args.no_export:
        ranking, recall = export_period(
            results,
            args.export_dir or config.export_dir,
            date_range.label(),
            config.recall_top_n,
        )
        print(f"Wrote {ranking} and {recall} (top {config.recall_top_n})", file=sys.stderr)
    return 0


def _cmd_feedback(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    store = FeedbackStore(args.feedback_file or config.feedback.store_path)
    if args.action == "set":
        fb = store.set(args.case_id, args.rating, args.comment or "")
        print(f"{fb.case_id}: {fb.rating} saved to {store.path}. Re-run `score` to re-evaluate.")
    elif args.action == "clear":
        removed = store.clear(args.case_id)
        print(f"{args.case_id}: {'removed' if removed else 'had no feedback'}")
    else:
        ratings = store.all()
        if not ratings:
            print("No feedback recorded.")
        for fb in ratings.values():
            comment = f" - {fb.comment}" if fb.comment else ""
            print(f"{fb.case_id}\t{fb.rating}\t{fb.updated_at}{comment}")
    return 0


def _cmd_eval_corpus(args: argparse.Namespace) -> int:
    cfg = GatewayConfig.from_env(embedding_model=args.embedding_model)
    embedder = _build_embedder(args.embedder, args.embedding_model, cfg)
    cases = load_reference_corpus(args.judgements_dir)
    _, vecs = build_reference_index(cases, embedder, language=None)
    sim_mat = cosine_similarity_matrix(vecs, vecs)
    np.fill_diagonal(sim_mat, -1.0)

    best_indices = np.argmax(sim_mat, axis=1)
    best_sims = sim_mat[np.arange(len(cases)), best_indices]

    print(f"Reference corpus: {len(cases)} cases from {args.judgements_dir}")
    print(f"Embedder: {embedder.name}")
    print(
        f"Leave-one-out nearest-neighbor cosine similarity: "
        f"min={best_sims.min():.3f}, median={np.median(best_sims):.3f}, "
        f"mean={best_sims.mean():.3f}, max={best_sims.max():.3f}"
    )
    print("Sample nearest-neighbor pairs:")
    for idx in np.argsort(-best_sims)[:5]:
        nn_idx = int(best_indices[idx])
        print(f"  {cases[idx].case_id} <-> {cases[nn_idx].case_id}: {best_sims[idx]:.3f}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Suva Federal Court judgement triage CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check-gateway", help="List Keera gateway models and probe embeddings")
    p_check.add_argument("--model", default=None, help="Override LLM model ID")
    p_check.add_argument("--probe-embedding", default=None, help="Explicit model ID to test /embeddings")
    p_check.set_defaults(func=_cmd_check_gateway)

    p_score = sub.add_parser("score", help="Score Crawl JSON judgement(s) for Suva relevance")
    p_score.add_argument("--input", required=True, help="Path to Crawl JSON file or folder of .json files")
    p_score.add_argument("--output", default=None, help="Output JSON path (prints to stdout if omitted)")
    p_score.add_argument("--judgements-dir", default="judgements", help="Directory with Export_*.html summaries")
    p_score.add_argument(
        "--embedder",
        choices=("tfidf", "gateway", "sentence-transformers"),
        default="tfidf",
        help="Embedding backend for cosine similarity",
    )
    p_score.add_argument("--embedding-model", default=None, help="Embedding model ID (for gateway or ST)")
    p_score.add_argument("--model", default=None, help="Apertus model ID override")
    p_score.add_argument("--top-k", type=int, default=None, help="Override top_k from the config")
    p_score.add_argument("--no-llm", action="store_true", help="Skip Apertus LLM call (embedding + keyword score only)")
    p_score.add_argument("--period", default=None, help="Scoring period on decision_date: 2026-Q2, 2026-06 or 2026")
    p_score.add_argument("--since", default=None, help="Inclusive start date YYYY-MM-DD (with --until)")
    p_score.add_argument("--until", default=None, help="Inclusive end date YYYY-MM-DD (with --since)")
    p_score.add_argument("--config", default=None, help="Triage config JSON (default: configs/triage.json)")
    p_score.add_argument("--cache-dir", default=None, help="Override cache_dir from the config")
    p_score.add_argument("--no-cache", action="store_true", help="Recompute embeddings and Apertus calls")
    p_score.add_argument("--feedback-file", default=None, help="Override feedback store path")
    p_score.add_argument("--no-feedback", action="store_true", help="Ignore lawyer feedback")
    p_score.add_argument("--export-dir", default=None, help="Override export_dir from the config")
    p_score.add_argument("--no-export", action="store_true", help="Do not write ranking CSV / recall list")
    p_score.add_argument("-v", "--verbose", action="store_true", help="List judgements skipped by the date filter")
    p_score.set_defaults(func=_cmd_score)

    p_fb = sub.add_parser("feedback", help="Record lawyer feedback (-- - 0 + ++) for a judgement")
    p_fb.add_argument("--config", default=None, help="Triage config JSON (default: configs/triage.json)")
    p_fb.add_argument("--feedback-file", default=None, help="Override feedback store path")
    fb_sub = p_fb.add_subparsers(dest="action", required=True)
    p_fb_set = fb_sub.add_parser("set", help="Set a rating; write negative ones as --rating=-- or --rating=-")
    p_fb_set.add_argument("case_id", help="Geschaeftsnummer, e.g. 8C_123/2026")
    p_fb_set.add_argument("--rating", required=True, choices=RATINGS)
    p_fb_set.add_argument("--comment", default=None)
    p_fb_clear = fb_sub.add_parser("clear", help="Remove the rating of a judgement")
    p_fb_clear.add_argument("case_id")
    fb_sub.add_parser("list", help="List all ratings")
    p_fb.set_defaults(func=_cmd_feedback)

    p_eval = sub.add_parser("eval-corpus", help="Evaluate leave-one-out cosine similarity on reference corpus")
    p_eval.add_argument("--judgements-dir", default="judgements", help="Directory with Export_*.html summaries")
    p_eval.add_argument(
        "--embedder",
        choices=("tfidf", "gateway", "sentence-transformers"),
        default="tfidf",
    )
    p_eval.add_argument("--embedding-model", default=None)
    p_eval.set_defaults(func=_cmd_eval_corpus)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
