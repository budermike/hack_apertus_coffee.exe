"""Recall, precision and F2 of the triage ranking on a labelled quarter (issue #21).

Setup from the issue: references only from Q4/2025, candidates = every judgement
of Q1/2026, ground truth = Suva list Q1/2026 (``is_reference`` in the Vector-DB).
A judgement counts as selected when ``score >= threshold``.

    python -m hack_apertus_coffee_exe.eval.eval

writes ``results/eval.md`` plus the sweep and ground-truth tables in
``results/tables/``. No model is called: the scores come from the pre-computed
vectors, or from a triage JSON passed with ``--scores LABEL=PATH``, e.g. the
TF-IDF row of the committed report:

    python -m hack_apertus_coffee_exe.triage.cli score --input data/raw --period 2026-Q1 \
        --no-llm --no-cache --no-export --output /tmp/triage_q1.json
    python -m hack_apertus_coffee_exe.eval.eval --scores "<label>=/tmp/triage_q1.json"
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
import logging
import math
from pathlib import Path
from typing import Any, Sequence

from hack_apertus_coffee_exe.crawl.vectordb import DEFAULT_DB_DIR, VectorDB
from hack_apertus_coffee_exe.triage.date_filter import DateRange

logger = logging.getLogger(__name__)

DEFAULT_REPORT_PATH = "results/eval.md"
DEFAULT_TABLES_DIR = "results/tables"
TOP_N = (30, 100)  # Suva grades the top 30 hits (recall_top_n in configs/triage.json)


@dataclass(frozen=True)
class ScoredCase:
    """One candidate judgement with its score and the Suva label."""

    case_id: str
    score: float
    relevant: bool
    decision_date: str = ""
    language: str = ""
    subject: str = ""


@dataclass(frozen=True)
class Evaluation:
    """Sweep result of one scorer; ``cases`` are ranked best first."""

    label: str
    cases: list[ScoredCase]
    sweep: list[dict[str, Any]]
    best: dict[str, Any]


def f2(precision: float, recall: float) -> float:
    """F-beta with beta = 2: recall counts four times as much as precision."""
    if precision <= 0.0 or recall <= 0.0:
        return 0.0
    return 5.0 * precision * recall / (4.0 * precision + recall)


def rank_cases(cases: Sequence[ScoredCase]) -> list[ScoredCase]:
    """Best score first; ties are broken by case number so every run gives the same order."""
    return sorted(cases, key=lambda c: (-c.score, c.case_id))


def _row(threshold: float, selected: int, tp: int, positives: int) -> dict[str, Any]:
    precision = tp / selected if selected else 0.0
    recall = tp / positives if positives else 0.0
    return {
        "threshold": threshold,
        "selected": selected,
        "tp": tp,
        "fp": selected - tp,
        "fn": positives - tp,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f2": round(f2(precision, recall), 4),
    }


def metrics_at(cases: Sequence[ScoredCase], threshold: float) -> dict[str, Any]:
    """Confusion counts, precision, recall and F2 when selecting ``score >= threshold``."""
    selected = [c for c in cases if c.score >= threshold]
    return _row(threshold, len(selected), sum(c.relevant for c in selected), sum(c.relevant for c in cases))


def sweep_thresholds(cases: Sequence[ScoredCase]) -> list[dict[str, Any]]:
    """One row per distinct score used as threshold, strictest threshold first."""
    ranked = rank_cases(cases)
    positives = sum(c.relevant for c in ranked)
    rows: list[dict[str, Any]] = []
    tp = 0
    for idx, case in enumerate(ranked):
        if not math.isfinite(case.score):
            break  # positives that were never scored cannot be selected by any threshold
        tp += case.relevant
        is_last_of_score = idx + 1 == len(ranked) or ranked[idx + 1].score != case.score
        if is_last_of_score:
            rows.append(_row(case.score, idx + 1, tp, positives))
    return rows


def best_f2(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Row with the highest F2; ties go to higher recall, then to the stricter threshold."""
    if not rows:
        raise ValueError("Empty sweep: no scored candidates")
    return max(rows, key=lambda r: (r["f2"], r["recall"], r["threshold"]))


def recall_at_n(cases: Sequence[ScoredCase], n: int) -> float:
    positives = sum(c.relevant for c in cases)
    if not positives:
        return 0.0
    return sum(c.relevant for c in rank_cases(cases)[:n]) / positives


def missed_cases(cases: Sequence[ScoredCase], threshold: float) -> list[tuple[int, ScoredCase]]:
    """``(rank, case)`` of the Suva judgements below the threshold (false negatives)."""
    return [
        (rank, case)
        for rank, case in enumerate(rank_cases(cases), start=1)
        if case.relevant and case.score < threshold
    ]


def evaluate(label: str, cases: Sequence[ScoredCase]) -> Evaluation:
    sweep = sweep_thresholds(cases)
    return Evaluation(label=label, cases=rank_cases(cases), sweep=sweep, best=best_f2(sweep))


# ------------------------------------------------------------------ score sources


class _ReadOnlyEmbedder:
    """Metadata-only embedder so eval reads pre-computed vectors without loading PyTorch."""

    def __init__(self, name: str, dim: int = 0) -> None:
        self.name = name
        self.dim = dim

    def embed(self, texts: Sequence[str]):  # pragma: no cover - read-only guard
        raise RuntimeError("Read-only embedder cannot compute new embeddings")


def _open_readonly_vectordb(db_dir: str | Path) -> VectorDB:
    meta_path = Path(db_dir) / "embedder_meta.json"
    if meta_path.exists():
        saved = json.loads(meta_path.read_text(encoding="utf-8"))
        if saved.get("kind") != "tfidf" and saved.get("name"):
            return VectorDB(
                db_dir=db_dir,
                embedder=_ReadOnlyEmbedder(name=saved["name"], dim=int(saved.get("dim") or 0)),
            )
    return VectorDB(db_dir=db_dir)


def score_vectordb(
    db: VectorDB,
    period: DateRange,
    ref_period: DateRange | None,
    *,
    hybrid: bool = True,
) -> list[ScoredCase]:
    """Stage-1 scores of every judgement in ``period``; ``ref_period`` None = all earlier references."""
    rows = db.score_candidates(
        date_from=period.since.isoformat(),
        date_to=period.until.isoformat(),
        ref_date_from=ref_period.since.isoformat() if ref_period else None,
        ref_date_to=ref_period.until.isoformat() if ref_period else None,
        hybrid=hybrid,
    )
    if not rows:
        raise ValueError("No candidates in the period or no Suva references in the reference period")
    return [
        ScoredCase(
            case_id=r["case_id"],
            score=float(r["score"]),
            relevant=bool(r["is_reference"]),
            decision_date=r["decision_date"],
            language=r["language"] or "",
            subject=r["subject"] or "",
        )
        for r in rows
    ]


def _labelled_doc_key(row: dict[str, Any]) -> str:
    return str(row.get("doc_key") or f"{row.get('decision_date') or ''}_{row['case_id']}")


def load_triage_scores(path: str | Path, labelled: Sequence[dict[str, Any]]) -> list[ScoredCase]:
    """Label a triage JSON (``triage.cli score --output``) with the Vector-DB ground truth.

    ``labelled`` are the Vector-DB rows of the period. Entries are matched and
    deduplicated by canonical document key so joined cases are counted once;
    unscored Suva judgements from ``labelled`` are added with score ``-inf``.
    """
    row_by_key: dict[str, dict[str, Any]] = {}
    by_exact: dict[tuple[str, str], str] = {}
    by_case_id: dict[str, list[str]] = {}
    for row in labelled:
        dk = _labelled_doc_key(row)
        row_by_key[dk] = row
        row_date = str(row.get("decision_date") or "")
        for cid in set(row.get("case_ids") or ()) | {row["case_id"]}:
            if row_date:
                by_exact[(cid, row_date)] = dk
            by_case_id.setdefault(cid, []).append(dk)

    raw_entries = json.loads(Path(path).read_text(encoding="utf-8"))
    best_by_key: dict[str, ScoredCase] = {}
    ignored = 0
    for entry in raw_entries:
        cid = entry["case_id"]
        jdate = str(entry.get("judgement_date") or "")
        doc_key: str | None = None
        if jdate and (cid, jdate) in by_exact:
            doc_key = by_exact[(cid, jdate)]
        elif cid in by_case_id:
            candidates = by_case_id[cid]
            doc_key = next((k for k in candidates if k not in best_by_key), candidates[0])
        if doc_key is None:
            ignored += 1
            continue

        row = row_by_key[doc_key]
        score = entry.get("rank_score")
        if score is None:
            score = entry["final_score"]
        score_f = float(score)
        meta = entry.get("metadata") or {}
        candidate = ScoredCase(
            case_id=cid,
            score=score_f,
            relevant=bool(row["is_reference"]),
            decision_date=jdate or str(row.get("decision_date") or ""),
            language=entry.get("language") or str(row.get("language") or ""),
            subject=meta.get("subject") or str(row.get("subject") or ""),
        )
        prev = best_by_key.get(doc_key)
        if prev is None or score_f > prev.score:
            best_by_key[doc_key] = candidate

    if raw_entries and not best_by_key:
        raise ValueError("No entries in triage scores match the evaluated period")
    if ignored:
        # Ignored entries are not counted as false positives, so precision may look better than it is.
        logger.warning(
            "%s: %d of %d scored judgements are not in the Vector-DB for this period and were ignored",
            path, ignored, len(raw_entries),
        )

    cases = list(best_by_key.values())
    for dk, row in row_by_key.items():
        if row["is_reference"] and dk not in best_by_key:
            cases.append(
                ScoredCase(
                    case_id=row["case_id"],
                    score=-math.inf,
                    relevant=True,
                    decision_date=str(row.get("decision_date") or ""),
                    language=str(row.get("language") or ""),
                    subject=str(row.get("subject") or ""),
                )
            )
    return cases


# ------------------------------------------------------------------------ outputs


def write_sweep_csv(rows: Sequence[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_ground_truth_csv(evaluation: Evaluation, path: Path) -> None:
    """Every Suva judgement with its rank and whether the best threshold finds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    threshold = evaluation.best["threshold"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rank", "case_id", "decision_date", "language", "score", "found", "subject"])
        for rank, case in enumerate(evaluation.cases, start=1):
            if case.relevant:
                scored = math.isfinite(case.score)
                writer.writerow(
                    [
                        rank if scored else "",
                        case.case_id,
                        case.decision_date,
                        case.language,
                        f"{case.score:.2f}" if scored else "",
                        case.score >= threshold,
                        case.subject,
                    ]
                )


def _pct(value: float) -> str:
    return f"{value * 100:.1f} %"


def _operating_points(evaluation: Evaluation) -> list[tuple[str, dict[str, Any]]]:
    """Best F2 plus the strictest thresholds that reach 90 % and 100 % recall."""
    points = [("Bestes F2", evaluation.best)]
    for target, name in ((0.9, "Recall ≥ 90 %"), (1.0, "Recall = 100 %")):
        row = next((r for r in evaluation.sweep if r["recall"] >= target), None)
        if row is not None:
            points.append((name, row))
    return points


def render_report(
    main: Evaluation,
    others: Sequence[Evaluation],
    *,
    period: DateRange,
    ref_period: DateRange,
    n_references: int,
    embedder: str,
) -> str:
    cases, best = main.cases, main.best
    positives = sum(c.relevant for c in cases)
    threshold = best["threshold"]
    span = f"{period.since.isoformat()} bis {period.until.isoformat()}"
    ref_span = f"{ref_period.since.isoformat()} bis {ref_period.until.isoformat()}"

    lines = [
        f"# Evaluation Triage: Recall, Precision, F2 ({span})",
        "",
        "Erzeugt mit `python -m hack_apertus_coffee_exe.eval.eval` (Issue #21). Nicht von Hand ändern.",
        "",
        "## Ergebnis",
        "",
        f"- **Schwelle (bestes F2): Score ≥ {threshold:.2f}**",
        f"- **Recall: {_pct(best['recall'])}** ({best['tp']} von {positives} Suva-Urteilen gefunden)",
        f"- Precision: {_pct(best['precision'])} ({best['tp']} von {best['selected']} ausgewählten Urteilen)",
        f"- **F2: {best['f2']:.3f}**",
        "- "
        + ", ".join(f"Recall@{n}: {_pct(recall_at_n(cases, n))}" for n in TOP_N)
        + " (Anteil der Suva-Urteile unter den ersten n der Rangliste)",
        "",
        "## Aufbau",
        "",
        f"- Kandidaten: alle {len(cases)} Urteile mit Entscheiddatum {span}.",
        f"- Ground Truth: {positives} davon stehen auf der Suva-Liste (`is_reference` im Vector-DB), "
        f"das sind {_pct(positives / len(cases))}.",
        f"- Referenzbasis: nur die {n_references} Suva-Urteile mit Entscheiddatum {ref_span}.",
        f"- Score: {main.label}. Embedder: `{embedder}`.",
        "- Ein Urteil gilt als ausgewählt, wenn Score ≥ Schwelle. Der Sweep prüft jeden vorkommenden Score als Schwelle.",
        "",
        "## Arbeitspunkte",
        "",
        "| Arbeitspunkt | Schwelle | Ausgewählt | Gefunden | Recall | Precision | F2 |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, row in _operating_points(main):
        lines.append(
            f"| {name} | {row['threshold']:.2f} | {row['selected']} | {row['tp']} | "
            f"{_pct(row['recall'])} | {_pct(row['precision'])} | {row['f2']:.3f} |"
        )

    lines += [
        "",
        "## Vergleich der Scores",
        "",
        "Jede Zeile mit ihrer eigenen besten F2-Schwelle. Die Schwellen sind zwischen den Zeilen nicht vergleichbar.",
        "",
        "| Score | Schwelle | Ausgewählt | Recall | Precision | F2 | "
        + " | ".join(f"Recall@{n}" for n in TOP_N)
        + " |",
        "|---|---|---|---|---|---|" + "---|" * len(TOP_N),
    ]
    for ev in (main, *others):
        row = ev.best
        lines.append(
            f"| {ev.label} | {row['threshold']:.2f} | {row['selected']} | {_pct(row['recall'])} | "
            f"{_pct(row['precision'])} | {row['f2']:.3f} | "
            + " | ".join(_pct(recall_at_n(ev.cases, n)) for n in TOP_N)
            + " |"
        )

    missed = missed_cases(cases, threshold)
    lines += ["", f"## Verpasste Suva-Urteile bei Schwelle {threshold:.2f} ({len(missed)})", ""]
    if missed:
        lines += ["| Rang | Geschäftsnummer | Entscheiddatum | Sprache | Score | Sachgebiet |", "|---|---|---|---|---|---|"]
        for rank, case in missed:
            scored = math.isfinite(case.score)
            lines.append(
                f"| {rank if scored else '–'} | {case.case_id} | {case.decision_date} | {case.language} | "
                f"{f'{case.score:.2f}' if scored else 'nicht bewertet'} | {case.subject} |"
            )
    else:
        lines.append("Keine.")

    lines += [
        "",
        "## Was diese Zahlen nicht zeigen",
        "",
        "- Die Schwelle ist auf denselben Urteilen gewählt, auf denen sie gemessen wird. "
        "Auf einem neuen Quartal sind die Werte eher tiefer.",
        "- Bewertet wird nur die Rangliste des genannten Scores. Ein Score ohne Apertus-Aufruf "
        "sagt nichts über die Apertus-Bewertung aus; diese lässt sich mit `--scores NAME=triage.json` messen.",
        "- Einträge der Suva-Liste ohne Bundesgerichts-Geschäftsnummer (z. B. kantonale Entscheide) "
        "sind nicht in der Ground Truth, weil sie nicht von bger.ch stammen.",
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recall, precision, F2 and threshold sweep (issue #21)")
    parser.add_argument("--period", default="2026-Q1", help="candidate period with Suva labels")
    parser.add_argument("--ref-period", default="2025-Q4", help="period of the Suva references")
    parser.add_argument("--db-dir", default=DEFAULT_DB_DIR)
    parser.add_argument(
        "--scores",
        action="append",
        default=[],
        metavar="LABEL=PATH",
        help="also evaluate a triage JSON (triage.cli score --output); repeatable",
    )
    parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    parser.add_argument("--tables-dir", default=DEFAULT_TABLES_DIR)
    args = parser.parse_args(argv)

    period = DateRange.from_period(args.period)
    ref_period = DateRange.from_period(args.ref_period)
    if ref_period.until >= period.since:
        raise SystemExit("--ref-period must end before --period starts (no leakage)")

    db = _open_readonly_vectordb(args.db_dir)
    ref_label = args.ref_period.upper()
    main_eval = evaluate(
        f"Vector-DB Stufe 1, hybrid (Cosine + Fachgebiets-Bonus), Referenzen {ref_label}, ohne Apertus",
        score_vectordb(db, period, ref_period),
    )
    others = [
        evaluate(
            f"Vector-DB Stufe 1, nur Cosine, Referenzen {ref_label}",
            score_vectordb(db, period, ref_period, hybrid=False),
        ),
        evaluate(
            f"Vector-DB Stufe 1, hybrid, alle Referenzen vor {period.since.isoformat()}",
            score_vectordb(db, period, None),
        ),
    ]
    if args.scores:
        labelled = db.query(date_from=period.since.isoformat(), date_to=period.until.isoformat())
        for spec in args.scores:
            label, _, path = spec.partition("=")
            if not path:
                raise SystemExit(f"--scores expects LABEL=PATH, got {spec!r}")
            others.append(evaluate(label, load_triage_scores(path, labelled)))

    n_references = len(
        db.query(date_from=ref_period.since.isoformat(), date_to=ref_period.until.isoformat(), is_reference=True)
    )
    report = render_report(
        main_eval,
        others,
        period=period,
        ref_period=ref_period,
        n_references=n_references,
        embedder=db.embedder.name,
    )
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    tables = Path(args.tables_dir)
    write_sweep_csv(main_eval.sweep, tables / f"eval_{period.label()}_sweep.csv")
    write_ground_truth_csv(main_eval, tables / f"eval_{period.label()}_ground_truth.csv")

    best = main_eval.best
    print(
        f"{args.period}: threshold >= {best['threshold']:.2f} | recall {best['recall']:.3f} | "
        f"precision {best['precision']:.3f} | F2 {best['f2']:.3f} | "
        f"{best['fn']} missed of {best['tp'] + best['fn']} | report: {report_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
