"""Batch run for green judgements and on-demand ``summarize_case`` (issue #17).

    # one judgement (UI button), reads the crawl output
    python -m hack_apertus_coffee_exe.summary.run one --case 8C_901/2026 --input data/raw

    # all green judgements of a triage run
    python -m hack_apertus_coffee_exe.summary.run batch --triage results/triage.json --input data/raw

    # print what is stored
    python -m hack_apertus_coffee_exe.summary.run show --case 8C_901/2026

Summaries already in the store are never regenerated (issue #26): batch and
on demand both go through ``store.get_or_generate_languages``.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sqlite3
import sys
from typing import Any, Callable, Sequence

from . import store
from .judgement import Judgement, load_judgements, same_case
from .store import GeneratedSummary, Summary

logger = logging.getLogger(__name__)

GREEN = "gruen"


def _similar_ids(triage_row: dict[str, Any]) -> list[str]:
    cases = triage_row.get("similar_cases") or triage_row.get("top_reference_matches") or []
    return [c["case_id"] for c in cases if isinstance(c, dict) and c.get("case_id")]


def to_generated(result: Any) -> dict[str, GeneratedSummary]:
    """Split a ``SummaryResult`` (DE + FR) into one store record per language."""
    issues = result.check.to_dicts()
    return {
        lang: GeneratedSummary(
            text=result.text(lang),
            model=result.model,
            prompt_version=result.prompt_version,
            coherence_ok=result.coherence_ok,
            title=result.title_de if lang == "de" else result.title_fr,
            issues=[i for i in issues if lang in i["language"].split("/")],
            facts=result.facts,
            examples=result.examples,
        )
        for lang in store.LANGUAGES
    }


def save_judgement_case(conn: sqlite3.Connection, judgement: Judgement) -> None:
    """Register the judgement (date, source URL, all case numbers) in ``cases``."""
    store.save_case(
        conn,
        case_id=judgement.case_id,
        decision_date=judgement.decision_date.isoformat() if judgement.decision_date else None,
        url=judgement.url,
        publication_date=judgement.publication_date,
        case_numbers=judgement.case_ids,
    )


def summarize_case(
    judgement: Judgement,
    conn: sqlite3.Connection,
    get_summarizer: Callable[[], Any],
    similar_case_ids: Sequence[str] = (),
) -> dict[str, Summary]:
    """Return the displayed DE/FR summary; generate and store it first if missing."""
    save_judgement_case(conn, judgement)

    def generate() -> dict[str, GeneratedSummary]:
        result = get_summarizer().summarize(judgement, similar_case_ids=similar_case_ids)
        logger.info("%s: generated (check ok=%s, attempts=%s)", judgement.case_id, result.coherence_ok, result.attempts)
        return to_generated(result)

    return store.get_or_generate_languages(conn, judgement.case_id, generate)


def load_triage(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON list of triage results")
    return data


def run_batch(
    triage_rows: Sequence[dict[str, Any]],
    judgements: Sequence[Judgement],
    conn: sqlite3.Connection,
    get_summarizer: Callable[[], Any],
    ampel: str = GREEN,
) -> list[str]:
    """Summarize every judgement whose triage ampel matches; returns the case ids done."""
    done: list[str] = []
    for row in triage_rows:
        if row.get("ampel") != ampel:
            continue
        case_id = row.get("case_id", "")
        judgement = next((j for j in judgements if same_case(j.case_id, case_id)), None)
        if judgement is None:
            logger.warning("%s: no crawl record found, skipped", case_id)
            continue
        try:
            summarize_case(judgement, conn, get_summarizer, _similar_ids(row))
            done.append(case_id)
        except Exception:  # one failing case must not stop the batch
            logger.exception("%s: summary failed", case_id)
    return done


def summarizer_factory(
    model: str | None = None, judgements_dir: str = "judgements", n_examples: int = 2
) -> Callable[[], Any]:
    """Lazily build one ``Summarizer`` (no gateway access until the first summary)."""
    cache: dict[str, Any] = {}

    def factory() -> Any:
        if "s" not in cache:
            from ..triage.llm import GatewayConfig

            from .examples import ExampleSelector
            from .generate import Summarizer
            from .llm import SummaryLLM

            llm = SummaryLLM(config=GatewayConfig.from_env(model=model))
            cache["s"] = Summarizer(llm, ExampleSelector(judgements_dir=judgements_dir), n_examples=n_examples)
        return cache["s"]

    return factory


def _summarizer_factory(args: argparse.Namespace) -> Callable[[], Any]:
    return summarizer_factory(args.model, args.judgements_dir, args.examples)


def _print(stored: dict[str, Summary | None]) -> None:
    for lang, s in stored.items():
        if s is None:
            print(f"[{lang}] —")
            continue
        flag = "ok" if s.coherence_ok else ("unchecked" if s.coherence_ok is None else "CHECK")
        print(f"=== {s.title}  [{lang}, v{s.version}, {s.status}, {s.model}, {flag}]")
        print(s.text)
        for issue in s.issues:
            print(f"  ! {issue['severity']}: {issue['detail']}")
        print()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="summary", description="Suva summaries DE/FR with Apertus")
    parser.add_argument("--db", default=None, help=f"SQLite file (default: ${store.DB_PATH_ENV} or {store.DEFAULT_DB_PATH})")
    parser.add_argument("--model", default=None, help="Gateway model ID (default: LLM_NAME / APERTUS_MODEL)")
    parser.add_argument("--judgements-dir", default="judgements", help="Suva example summaries")
    parser.add_argument("--examples", type=int, default=2, help="Few-shot examples per language")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_one = sub.add_parser("one", help="Summarize one judgement")
    p_one.add_argument("--case", required=True)
    p_one.add_argument("--input", default="data/raw", help="Crawl file or directory")

    p_batch = sub.add_parser("batch", help="Summarize all judgements with the given ampel")
    p_batch.add_argument("--triage", required=True, help="Triage results JSON (hack_apertus_coffee_exe.triage.cli score --output)")
    p_batch.add_argument("--input", default="data/raw")
    p_batch.add_argument("--ampel", default=GREEN)

    p_show = sub.add_parser("show", help="Print the stored summaries of a case")
    p_show.add_argument("--case", required=True)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    conn = store.connect(args.db)
    factory = _summarizer_factory(args)

    if args.cmd == "show":
        _print({lang: store.get_display(conn, args.case, lang) for lang in store.LANGUAGES})
        return 0

    judgements = load_judgements(args.input)
    if args.cmd == "one":
        judgement = next((j for j in judgements if same_case(j.case_id, args.case)), None)
        if judgement is None:
            print(f"{args.case} not found in {args.input}", file=sys.stderr)
            return 1
        try:
            _print(summarize_case(judgement, conn, factory))
        except ValueError as exc:  # e.g. missing API key
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0

    done = run_batch(load_triage(args.triage), judgements, conn, factory, ampel=args.ampel)
    print(f"{len(done)} judgement(s) summarized or already stored in {args.db or store.db_path_from_env()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
