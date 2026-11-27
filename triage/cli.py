"""CLI for Suva judgement Triage (gateway check, scoring, and corpus eval)."""

from __future__ import annotations

import argparse
from datetime import date
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


def _resolve_date_range(args: argparse.Namespace) -> DateRange | None:
    if args.all_dates:
        return None
    until = date.fromisoformat(args.until) if args.until else date.today()
    if args.since:
        return DateRange(since=date.fromisoformat(args.since), until=until)
    if args.max_age_days is not None:
        return DateRange.last_n_days(days=args.max_age_days, until=until)
    return DateRange.last_n_months(months=args.months, until=until)


def _cmd_score(args: argparse.Namespace) -> int:
    cfg = GatewayConfig.from_env(model=args.model, embedding_model=args.embedding_model)
    embedder = _build_embedder(args.embedder, args.embedding_model, cfg)
    llm_client = None if args.no_llm else ApertusTriageClient(config=cfg)
    date_range = _resolve_date_range(args)

    pipeline = TriagePipeline(
        judgements_dir=args.judgements_dir,
        embedder=embedder,
        llm_client=llm_client,
        top_k=args.top_k,
        date_range=date_range,
    )
    items = load_crawl_items(args.input)
    results = pipeline.score_items(items)
    payload = [res.to_dict() for res in results]

    if pipeline.last_skipped:
        print(
            f"Hard date filter excluded {len(pipeline.last_skipped)}/{len(items)} item(s) "
            f"outside [{date_range.since.isoformat()}, {date_range.until.isoformat()}]:",
            file=sys.stderr,
        )
        for skipped in pipeline.last_skipped:
            print(
                f"  - {skipped.case_id} (date={skipped.parsed_date}): {skipped.reason}",
                file=sys.stderr,
            )

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {len(payload)} triage result(s) to {out_path}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
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
    p_score.add_argument("--top-k", type=int, default=3, help="Number of nearest Suva precedents to retrieve")
    p_score.add_argument("--no-llm", action="store_true", help="Skip Apertus LLM call (embedding + keyword score only)")
    p_score.add_argument("--since", default=None, help="Inclusive start date YYYY-MM-DD (overrides --months)")
    p_score.add_argument("--until", default=None, help="Inclusive end date YYYY-MM-DD (default: today)")
    p_score.add_argument("--months", type=int, default=3, help="Lookback window in calendar months (default: 3)")
    p_score.add_argument("--max-age-days", type=int, default=None, help="Lookback window in days (overrides --months)")
    p_score.add_argument("--all-dates", action="store_true", help="Disable the hard date-range filter")
    p_score.set_defaults(func=_cmd_score)

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
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
