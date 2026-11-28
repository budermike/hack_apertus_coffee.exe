"""Hold-out evaluation of the two-pass relevance scoring (issue #62, docs/ISSUE_RECALL.md).

    python -m hack_apertus_coffee_exe.eval.relevance_eval

Reads the score files written by ``triage.cli relevance``
(``results/tables/relevance_scores_<period>.json``) and Suva's selection, calls
no model and needs no cache. For every pair of labelled quarters the calibration
is fitted on one quarter and measured on the other, so the threshold is never
chosen on the judgements it is measured on. Writes ``results/relevance_eval.md``
and ``results/tables/relevance_eval_thresholds.csv``.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from hack_apertus_coffee_exe.triage import calibration
from hack_apertus_coffee_exe.triage.date_filter import DateRange
from hack_apertus_coffee_exe.triage.relevance_period import (
    DEFAULT_LABEL_PERIODS,
    DEFAULT_TABLES_DIR,
    load_selected_ids,
    scores_path,
)

FACTORS = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3)
TARGETS = (0.8, 0.85, 0.9, 0.95, 1.0)  # recall targets (#75): share of known Suva judgements above the threshold
N_BOOT = 2000


def confusion(selected: np.ndarray, y: np.ndarray) -> tuple[int, int, int]:
    return int((selected & y).sum()), int((selected & ~y).sum()), int((~selected & y).sum())


def metrics(selected: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    tp, fp, fn = confusion(selected, y)
    return {
        "selected": tp + fp, "tp": tp, "fp": fp, "fn": fn,
        "recall": tp / max(tp + fn, 1), "precision": tp / max(tp + fp, 1), "f2": calibration.f2(tp, fp, fn),
    }


def hits_at(p: np.ndarray, y: np.ndarray, k: int = 30) -> float:
    """Suva judgements among the ``k`` highest relevance values; ties at rank ``k`` count proportionally."""
    if len(p) <= k:
        return float(y.sum())
    cut = np.sort(p)[::-1][k - 1]
    above, tied = p > cut, p == cut
    return float(y[above].sum() + y[tied].sum() * (k - above.sum()) / tied.sum())


def holdout(records: dict[str, list[dict[str, Any]]], selected_ids: set[str]) -> list[dict[str, Any]]:
    """One fold per ordered pair (calibrated on A, measured on B): relevance values and labels of B."""
    folds = []
    for test, test_records in records.items():
        for train, train_records in records.items():
            if train == test:
                continue
            cal = calibration.calibrate(train_records, selected_ids)
            p = np.array([calibration.relevance(r, cal) or 0.0 for r in test_records])
            y = np.array([bool(set(r["case_ids"]) & selected_ids) for r in test_records])
            scored = [r for r in train_records if calibration.relevance(r, cal) is not None]
            train_p = [calibration.relevance(r, cal) for r in scored]
            train_y = [int(bool(set(r["case_ids"]) & selected_ids)) for r in scored]
            folds.append({"train": train, "test": test, "cal": cal, "p": p, "y": y, "train_p": train_p, "train_y": train_y,
                          "direction": "vorwärts" if train < test else "rückwärts"})
    return folds


def pooled(folds: Sequence[dict[str, Any]], factor: float, index: dict[int, np.ndarray] | None = None) -> dict[str, Any]:
    tp = fp = fn = 0
    for k, fold in enumerate(folds):
        selected, y = fold["p"] > fold["cal"]["t"] * factor, fold["y"]
        if index is not None:
            selected, y = selected[index[k]], y[index[k]]
        a, b, c = confusion(selected, y)
        tp, fp, fn = tp + a, fp + b, fn + c
    return {"selected": tp + fp, "tp": tp, "fn": fn, "recall": tp / max(tp + fn, 1), "f2": calibration.f2(tp, fp, fn)}


def threshold_table(folds: Sequence[dict[str, Any]], seed: int = 1) -> list[dict[str, Any]]:
    """Recall and F2 for lower thresholds, with 95 % bootstrap intervals and the paired difference to factor 1."""
    rng = np.random.default_rng(seed)
    draws = [{k: rng.integers(0, len(f["y"]), len(f["y"])) for k, f in enumerate(folds)} for _ in range(N_BOOT)]
    base = [pooled(folds, 1.0, d) for d in draws]
    rows = []
    for factor in FACTORS:
        point, boot = pooled(folds, factor), [pooled(folds, factor, d) for d in draws]
        row: dict[str, Any] = {"factor": factor, **{k: point[k] for k in ("selected", "tp", "fn")},
                               "recall": round(point["recall"], 4), "f2": round(point["f2"], 4)}
        for key in ("recall", "f2"):
            values = np.array([b[key] for b in boot])
            delta = values - np.array([b[key] for b in base])
            row[f"{key}_lo"], row[f"{key}_hi"] = (round(float(v), 3) for v in np.percentile(values, [2.5, 97.5]))
            row[f"d_{key}_lo"], row[f"d_{key}_hi"] = (round(float(v), 3) for v in np.percentile(delta, [2.5, 97.5]))
        rows.append(row)
    return rows


def target_rows(folds: Sequence[dict[str, Any]], targets: Sequence[float] = TARGETS) -> list[dict[str, Any]]:
    """Recall target (#75) in hold-out: threshold set on the training quarter, list measured on the other."""
    rows = []
    for target in targets:
        tp = fp = fn = 0
        per_fold = []
        for fold in folds:
            t = calibration.recall_threshold(fold["train_p"], fold["train_y"], target)
            a, b, c = confusion(fold["p"] > t, fold["y"])
            tp, fp, fn = tp + a, fp + b, fn + c
            per_fold.append(f"{fold['test']}: {a} von {a + c} ({a + b} auf der Liste)")
        rows.append({"target": target, "selected": tp + fp, "tp": tp, "fn": fn,
                     "recall": round(tp / max(tp + fn, 1), 4), "f2": round(calibration.f2(tp, fp, fn), 4),
                     "per_fold": "; ".join(per_fold)})
    return rows


def _pct(value: float) -> str:
    return f"{value * 100:.1f} %"


def render(
    folds: Sequence[dict[str, Any]],
    table: Sequence[dict[str, Any]],
    list_factor: float,
    model: str,
    targets: Sequence[dict[str, Any]] = (),
    target_recall: float | None = None,
) -> str:
    lines = [
        "# Evaluation Relevanz-Bewertung: zwei Apertus-Durchgänge mit Kalibrierung",
        "",
        "Erzeugt mit `python -m hack_apertus_coffee_exe.eval.relevance_eval`. Nicht von Hand ändern.",
        "",
        f"Modell: `{model}`. Kandidaten: Urteile der III. und IV. öffentlich-rechtlichen Abteilung (8C, 9C, 8F, 9F); "
        "von Apertus bewertet werden Unfall-, Invaliden- und Militärversicherung.",
        "",
        "## Hold-out: Kalibrierung und Schwelle stammen nie aus dem gemessenen Quartal",
        "",
        "| Kalibriert auf | Gemessen auf | Richtung | Schwelle | Auf der Liste | Gefunden | Recall | Precision | F2 | Suva-Urteile unter den ersten 30 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for fold in folds:
        m = metrics(fold["p"] > fold["cal"]["t"], fold["y"])
        lines.append(
            f"| {fold['train']} | {fold['test']} | {fold['direction']} | {fold['cal']['t']:.3f} | {m['selected']} | "
            f"{m['tp']} von {m['tp'] + m['fn']} | {_pct(m['recall'])} | {_pct(m['precision'])} | {m['f2']:.3f} | "
            f"{hits_at(fold['p'], fold['y']):.1f} |"
        )
    lines += [
        "",
        "## Schwelle senken: mehr Recall gegen längere Liste",
        "",
        "Alle Hold-out-Messungen zusammen. Faktor 1.0 ist die F2-Schwelle der Kalibrierung; kleinere Faktoren senken sie. "
        f"In Klammern das 95-%-Intervall (Bootstrap über Urteile, {N_BOOT} Ziehungen). Δ ist die gepaarte Differenz zu Faktor 1.0.",
        "",
        "| Faktor | Auf der Liste | Gefunden | Recall | F2 | Δ Recall | Δ F2 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in table:
        mark = " **(eingestellt)**" if abs(row["factor"] - list_factor) < 1e-9 else ""
        lines.append(
            f"| {row['factor']:.1f}{mark} | {row['selected']} | {row['tp']} von {row['tp'] + row['fn']} | "
            f"{_pct(row['recall'])} ({_pct(row['recall_lo'])} bis {_pct(row['recall_hi'])}) | "
            f"{row['f2']:.3f} ({row['f2_lo']:.3f} bis {row['f2_hi']:.3f}) | "
            f"{row['d_recall_lo'] * 100:+.1f} bis {row['d_recall_hi'] * 100:+.1f} Punkte | {row['d_f2_lo']:+.3f} bis {row['d_f2_hi']:+.3f} |"
        )
    if targets:
        lines += [
            "",
            "## Recall-Ziel (#75): Anteil der bekannten Suva-Urteile über der Schwelle",
            "",
            "Die Schwelle wird auf dem einen Quartal so gesetzt, dass dort mindestens dieser Anteil der Suva-Urteile "
            "auf der Liste steht, und auf dem anderen Quartal gemessen. "
            "Ist ein Recall-Ziel eingestellt, ersetzt es den Faktor oben.",
            "",
            "| Ziel | Auf der Liste | Gefunden | Recall | F2 | Je Quartal |",
            "|---|---|---|---|---|---|",
        ]
        for row in targets:
            mark = " **(eingestellt)**" if target_recall is not None and abs(row["target"] - target_recall) < 1e-9 else ""
            lines.append(
                f"| {row['target'] * 100:.0f} %{mark} | {row['selected']} | {row['tp']} von {row['tp'] + row['fn']} | "
                f"{_pct(row['recall'])} | {row['f2']:.3f} | {row['per_fold']} |"
            )
    lines += [
        "",
        "## Was diese Zahlen nicht zeigen",
        "",
        "- Die Kriterien im Prompt wurden mit Blick auf beide gelabelten Quartale formuliert. Die Werte sind deshalb eher zu günstig.",
        "- Zwei Quartale mit zusammen rund 60 Suva-Urteilen: Ein bis zwei Urteile Unterschied sind Zufall.",
        "- Gemessen wird die Liste, nicht die Qualität der Begründung oder der Zusammenfassungen.",
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hold-out evaluation of the two-pass relevance scoring")
    parser.add_argument("--periods", nargs="*", default=list(DEFAULT_LABEL_PERIODS), help="Labelled periods")
    parser.add_argument("--judgements-dir", default="judgements")
    parser.add_argument("--tables-dir", default=DEFAULT_TABLES_DIR)
    parser.add_argument("--list-factor", type=float, default=None, help="Factor to mark in the table (default: config)")
    parser.add_argument("--report", default="results/relevance_eval.md")
    args = parser.parse_args(argv)

    from hack_apertus_coffee_exe.triage.config import load_config

    config = load_config()
    list_factor = args.list_factor if args.list_factor is not None else config.list_factor
    selected_ids = load_selected_ids(args.judgements_dir)
    records = {}
    for name in args.periods:
        path = scores_path(args.tables_dir, DateRange.from_period(name))
        if not path.exists():
            raise SystemExit(f"{path} is missing: run `python -m hack_apertus_coffee_exe.triage.cli relevance --period {name}` first")
        records[name] = json.loads(path.read_text(encoding="utf-8"))
    folds = holdout(records, selected_ids)
    table = threshold_table(folds)
    model = next((r["model"] for rs in records.values() for r in rs if r.get("triage")), "?")
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    targets = target_rows(folds)
    Path(args.report).write_text(
        render(folds, table, list_factor, model, targets, config.target_recall), encoding="utf-8"
    )
    csv_path = Path(args.tables_dir) / "relevance_eval_thresholds.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(table[0].keys()))
        writer.writeheader()
        writer.writerows(table)
    for fold in folds:
        m = metrics(fold["p"] > fold["cal"]["t"], fold["y"])
        print(f"{fold['train']} -> {fold['test']}: list {m['selected']}, recall {m['recall']:.3f}, precision {m['precision']:.3f}, F2 {m['f2']:.3f}")
    print(f"report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
