"""Automatic run for green judgements and on-demand ``summarize_case`` (issue #17).

Only judgements triage marks green (definitely relevant) are summarized
automatically. Yellow (uncertain) and red (not relevant) judgements get a
summary only when a lawyer clicks "summarise" in the UI, which calls
``summarize_case`` (CLI: ``one``).

This module only generates. Storing the results (summaries DB) is done by the
caller; ``SummaryResult.to_dict()`` / ``--output`` give everything to store.

    # one judgement on request (UI "summarise" button), any traffic light
    python -m hack_apertus_coffee_exe.summary.run one --case 8C_901/2026 --input data/raw

    # automatic: all green judgements of a triage run, written to a JSON file
    python -m hack_apertus_coffee_exe.summary.run batch --triage results/tables/triage.json \
        --input data/raw --output results/tables/summaries.json
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
from typing import Any, Sequence

from .generate import Summarizer, SummaryResult
from .judgement import Judgement, load_judgements, same_case

logger = logging.getLogger(__name__)

GREEN = "gruen"


def similar_case_ids(triage_row: dict[str, Any]) -> list[str]:
    """Case ids of the Suva summaries triage found most similar (few-shot examples)."""
    cases = triage_row.get("similar_cases") or triage_row.get("top_reference_matches") or []
    return [c["case_id"] for c in cases if isinstance(c, dict) and c.get("case_id")]


def summarize_case(
    judgement: Judgement,
    summarizer: Summarizer,
    similar_case_ids: Sequence[str] = (),
) -> SummaryResult:
    """Generate the DE and FR summary of one judgement (UI "summarise" button, any traffic light)."""
    result = summarizer.summarize(judgement, similar_case_ids=similar_case_ids)
    logger.info("%s: generated (check ok=%s, attempts=%s)", judgement.case_id, result.coherence_ok, result.attempts)
    return result


def run_batch(
    triage_rows: Sequence[dict[str, Any]],
    judgements: Sequence[Judgement],
    summarizer: Summarizer,
    skip_case_ids: Sequence[str] = (),
) -> list[SummaryResult]:
    """Summarize every green (definitely relevant) judgement of a triage run.

    Yellow and red judgements are never summarized here; they need an explicit
    request from the UI (:func:`summarize_case`). ``skip_case_ids`` lets the
    caller leave out judgements that already have a summary.
    """
    results: list[SummaryResult] = []
    for row in triage_rows:
        if row.get("ampel") != GREEN:
            continue
        case_id = row.get("case_id", "")
        if any(same_case(case_id, s) for s in skip_case_ids):
            continue
        judgement = next((j for j in judgements if same_case(j.case_id, case_id)), None)
        if judgement is None:
            logger.warning("%s: no crawl record found, skipped", case_id)
            continue
        try:
            results.append(summarize_case(judgement, summarizer, similar_case_ids(row)))
        except Exception:  # one failing case must not stop the batch
            logger.exception("%s: summary failed", case_id)
    return results


def load_triage(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON list of triage results")
    return data


def _build_summarizer(args: argparse.Namespace) -> Summarizer:
    from ..triage.llm import GatewayConfig

    from .examples import ExampleSelector
    from .llm import SummaryLLM

    llm = SummaryLLM(config=GatewayConfig.from_env(model=args.model))
    return Summarizer(llm, ExampleSelector(judgements_dir=args.judgements_dir), n_examples=args.examples)


def _print(result: SummaryResult) -> None:
    flag = "ok" if result.coherence_ok else "CHECK"
    for lang, title in (("de", result.title_de), ("fr", result.title_fr)):
        print(f"=== {title}  [{lang}, {result.model}, {flag}]")
        print(result.text(lang))
        print()
    for issue in result.check.issues:
        print(f"  ! {issue.language} {issue.severity}: {issue.detail}")


def _write(results: list[SummaryResult], path: str | None) -> None:
    if not path:
        return
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(results)} summary result(s) to {out}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="summary", description="Suva summaries DE/FR with Apertus")
    parser.add_argument("--model", default=None, help="Gateway model ID (default: LLM_NAME / APERTUS_MODEL)")
    parser.add_argument("--judgements-dir", default="judgements", help="Suva example summaries")
    parser.add_argument("--examples", type=int, default=2, help="Few-shot examples per language")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_one = sub.add_parser("one", help="Summarize one judgement on request (any traffic light)")
    p_one.add_argument("--case", required=True)
    p_one.add_argument("--input", default="data/raw", help="Crawl file or directory")
    p_one.add_argument("--output", default=None, help="Also write the result as JSON")

    p_batch = sub.add_parser("batch", help="Automatically summarize all green judgements")
    p_batch.add_argument("--triage", required=True,
                         help="Triage results JSON (hack_apertus_coffee_exe.triage.cli score --output)")
    p_batch.add_argument("--input", default="data/raw")
    p_batch.add_argument("--output", default=None, help="JSON file for all results")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    judgements = load_judgements(args.input)

    try:
        summarizer = _build_summarizer(args)
    except ValueError as exc:  # e.g. missing API key
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.cmd == "one":
        judgement = next((j for j in judgements if same_case(j.case_id, args.case)), None)
        if judgement is None:
            print(f"{args.case} not found in {args.input}", file=sys.stderr)
            return 1
        result = summarize_case(judgement, summarizer)
        _print(result)
        _write([result], args.output)
        return 0

    results = run_batch(load_triage(args.triage), judgements, summarizer)
    for result in results:
        _print(result)
    _write(results, args.output)
    print(f"{len(results)} green judgement(s) summarized", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
