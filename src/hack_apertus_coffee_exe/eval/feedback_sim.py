"""What do the lawyers' decisions change? Simulation with Suva's real decisions (issue #29).

    python -m hack_apertus_coffee_exe.eval.feedback_sim

Reads the score files of the labelled quarters (``results/tables/relevance_scores_<period>.json``)
and Suva's selection; calls no model. For each labelled quarter the base relevance comes from
the calibration fitted on the *other* quarter, as in operation, where the calibration is older
than the judgements on show. Then a lawyer is simulated who decides ``k`` judgements exactly
as Suva did:

- ``top``: the first ``k`` of the list (highest relevance first), the way a list is read;
- ``random``: ``k`` scored judgements at random (mean over ``SEEDS`` draws).

``triage.feedback_learning`` learns from these decisions. Measured is the list of the
judgements **not** decided yet, before and after learning: length, recall, precision, F2 at
the list threshold, and the average precision of the ranking. ``*_learned`` is learning as
it is, ``*_protected`` with the rule used in operation (a judgement that was on the list
stays on it). Judgements Apertus did not score are in neither list and not counted.

Writes ``results/tables/feedback_learning_sim.csv`` and ``results/reports/feedback_learning.md``.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from hack_apertus_coffee_exe.triage import calibration, feedback_learning
from hack_apertus_coffee_exe.triage.date_filter import DateRange
from hack_apertus_coffee_exe.triage.relevance_period import (
    DEFAULT_LABEL_PERIODS,
    DEFAULT_TABLES_DIR,
    load_selected_ids,
    scores_path,
)

KS = (5, 10, 20, 40, 80)
PRIOR_SDS = (0.1, 0.25, 0.5, 1.0)
SEEDS = 50


def scored_examples(records: Sequence[dict[str, Any]], cal: dict[str, Any], selected_ids: set[str]) -> list[feedback_learning.Example]:
    """One example per judgement Apertus scored: base relevance from ``cal``, features, Suva's decision."""
    out = []
    for record in records:
        p0 = calibration.relevance(record, cal)
        if p0 is None:
            continue
        criteria = [c.get("a_id") for c in (record["triage"].get("b_kriterien") or []) if isinstance(c, dict)]
        features = feedback_learning.feature_names(record["area"], criteria)
        out.append(feedback_learning.Example(p0, features, bool(set(record["case_ids"]) & selected_ids)))
    return out


def average_precision(p: np.ndarray, y: np.ndarray) -> float:
    if not y.any():
        return float("nan")
    order = np.argsort(-p, kind="stable")
    hits = y[order]
    precision_at_hit = np.cumsum(hits) / (np.arange(len(hits)) + 1)
    return float(precision_at_hit[hits].mean())


def measure(p: np.ndarray, y: np.ndarray, t_list: float) -> dict[str, float]:
    on_list = p > t_list
    tp, fp, fn = int((on_list & y).sum()), int((on_list & ~y).sum()), int((~on_list & y).sum())
    return {
        "list": float(on_list.sum()),
        "recall": tp / (tp + fn) if tp + fn else float("nan"),
        "precision": tp / (tp + fp) if tp + fp else float("nan"),
        "f2": calibration.f2(tp, fp, fn),
        "ap": average_precision(p, y),
    }


def one_run(examples: Sequence[feedback_learning.Example], decided: Sequence[int], prior_sd: float, t_list: float) -> dict[str, float]:
    """Decide ``decided`` as Suva did, learn, and measure the rest before and after."""
    decided_set = set(decided)
    corrections = feedback_learning.fit_corrections([examples[i] for i in decided], prior_sd)
    rest = [example for i, example in enumerate(examples) if i not in decided_set]
    y = np.array([example.relevant for example in rest], dtype=bool)
    base = np.array([example.p0 for example in rest])
    learned = np.array([feedback_learning.corrected(example.p0, example.features, corrections) for example in rest])
    # in operation a judgement that was on the list stays on it, at its end at worst
    protected = np.where((base > t_list) & (learned <= t_list), t_list * (1 + 1e-9), learned)
    before, after, safe = measure(base, y, t_list), measure(learned, y, t_list), measure(protected, y, t_list)
    out = {"n_rest": float(len(rest)), "pos_rest": float(y.sum()), "decided_pos": float(sum(examples[i].relevant for i in decided))}
    for name in before:
        out[f"{name}_base"], out[f"{name}_learned"], out[f"{name}_protected"] = before[name], after[name], safe[name]
    return out


def simulate(
    records: dict[str, list[dict[str, Any]]],
    selected_ids: set[str],
    list_factor: float,
    target_recall: float | None = None,
) -> list[dict[str, Any]]:
    """Every (test, train) pair of quarters; the list threshold follows ``target_recall`` when set (#75), else ``list_factor``."""
    rows: list[dict[str, Any]] = []
    names = list(records)
    for test in names:
        for train in names:
            if train == test:
                continue
            cal = calibration.calibrate(records[train], selected_ids, target_recall)
            t_list = calibration.list_threshold(cal, list_factor)
            examples = scored_examples(records[test], cal, selected_ids)
            by_rank = sorted(range(len(examples)), key=lambda i: -examples[i].p0)
            for k in KS:
                if k >= len(examples):
                    continue
                for prior_sd in PRIOR_SDS:
                    top = one_run(examples, by_rank[:k], prior_sd, t_list)
                    rows.append({"quarter": test, "calibrated_on": train, "protocol": "top", "k": k, "prior_sd": prior_sd, "draws": 1, **top})
                    draws = [
                        one_run(examples, np.random.default_rng(seed).choice(len(examples), size=k, replace=False).tolist(), prior_sd, t_list)
                        for seed in range(SEEDS)
                    ]
                    mean = {key: float(np.nanmean([d[key] for d in draws])) for key in draws[0]}
                    rows.append({"quarter": test, "calibrated_on": train, "protocol": "random", "k": k, "prior_sd": prior_sd, "draws": SEEDS, **mean})
    return rows


def summarise(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mean change over the quarters per protocol, prior and number of decisions."""
    out = []
    for protocol in ("top", "random"):
        for prior_sd in PRIOR_SDS:
            for k in KS:
                group = [r for r in rows if r["protocol"] == protocol and r["prior_sd"] == prior_sd and r["k"] == k]
                if not group:
                    continue
                entry: dict[str, Any] = {"protocol": protocol, "prior_sd": prior_sd, "k": k}
                for variant in ("learned", "protected"):
                    for name in ("list", "recall", "precision", "f2", "ap"):
                        changes = [r[f"{name}_{variant}"] - r[f"{name}_base"] for r in group]
                        entry[f"{variant}_d_{name}"] = float(np.nanmean(changes)) if not np.all(np.isnan(changes)) else float("nan")
                out.append(entry)
    return out


def _pp(value: float) -> str:
    """A change of a share in percentage points, e.g. ``-7.5``."""
    return "–" if np.isnan(value) else f"{100 * value:+.1f}"


def render(rows: Sequence[dict[str, Any]], list_factor: float, target_recall: float | None = None) -> str:
    """The German report: mean changes per number of decisions, and the range over all runs."""
    summary = summarise(rows)
    quarters = sorted({r["quarter"] for r in rows})

    def span(variant: str, name: str) -> tuple[float, float]:
        changes = [r[f"{name}_{variant}"] - r[f"{name}_base"] for r in rows]
        changes = [c for c in changes if not np.isnan(c)]
        return min(changes), max(changes)

    rule = (
        f"Recall-Ziel {target_recall:.0%} der bekannten Suva-Urteile des Kalibrierquartals".replace("%", " %")
        if target_recall is not None
        else f"`list_factor` {list_factor}"
    )
    lines = [
        "# Simulation: Lernen aus Relevanz-Entscheiden",
        "",
        "Erzeugt mit `python -m hack_apertus_coffee_exe.eval.feedback_sim`. Nicht von Hand ändern.",
        "",
        "Frage: Was ändert sich an der Liste, wenn Juristinnen und Juristen `k` Urteile entscheiden "
        "und das System daraus lernt (`triage/feedback_learning.py`)?",
        "",
        f"Aufbau: Quartale {' und '.join(quarters)}, in denen Suvas Auswahl bekannt ist. Die Relevanz eines "
        "Quartals stammt aus der Kalibrierung auf dem jeweils anderen Quartal, wie im Betrieb. Die simulierte "
        "Juristin entscheidet `k` Urteile genau so, wie Suva es getan hat: entweder die ersten `k` der Liste "
        f"(«von oben») oder `k` zufällige (Mittel über {SEEDS} Ziehungen). Gemessen wird die Liste der "
        f"**noch nicht entschiedenen** Urteile an der Listenschwelle ({rule}). "
        "«Ungeschützt» ist das Lernen ohne weitere Regel, «geschützt» mit der Regel im Betrieb: Ein Urteil, das "
        "auf der Liste war, bleibt auf der Liste. Es wird kein Modell aufgerufen.",
        "",
        "## Mittlere Änderung gegenüber der Liste ohne Lernen",
        "",
        "Liste = Anzahl Urteile, Recall und Precision in Prozentpunkten, AP = Average Precision der Rangfolge. "
        f"`prior_sd` {feedback_learning.DEFAULT_PRIOR_SD} ist die Einstellung im Betrieb.",
        "",
        "| Reihenfolge | prior_sd | k | Liste ungeschützt | Recall ungeschützt | Precision ungeschützt | Liste geschützt | Recall geschützt | Precision geschützt | AP geschützt |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for entry in summary:
        mark = " **(Betrieb)**" if abs(entry["prior_sd"] - feedback_learning.DEFAULT_PRIOR_SD) < 1e-9 else ""
        lines.append(
            f"| {'von oben' if entry['protocol'] == 'top' else 'zufällig'} | {entry['prior_sd']}{mark} | {entry['k']} "
            f"| {entry['learned_d_list']:+.1f} | {_pp(entry['learned_d_recall'])} | {_pp(entry['learned_d_precision'])} "
            f"| {entry['protected_d_list']:+.1f} | {_pp(entry['protected_d_recall'])} | {_pp(entry['protected_d_precision'])} "
            f"| {entry['protected_d_ap']:+.3f} |"
        )
    open_recall, safe_recall = span("learned", "recall"), span("protected", "recall")
    open_list, safe_list = span("learned", "list"), span("protected", "list")
    safe_ap = span("protected", "ap")
    top_runs = [r for r in rows if r["protocol"] == "top"]
    share_no = 1 - sum(r["decided_pos"] for r in top_runs) / sum(r["k"] for r in top_runs)
    safe_recall_text = (
        "der Recall bleibt in jedem Lauf gleich"
        if abs(safe_recall[0]) < 1e-9 and abs(safe_recall[1]) < 1e-9
        else f"der Recall um {_pp(safe_recall[0])} bis {_pp(safe_recall[1])} Prozentpunkte"
    )
    lines += [
        "",
        "## Was die Zahlen sagen",
        "",
        f"Über alle {len(rows)} Läufe (einzelne Quartale, nicht gemittelt):",
        "",
        f"- **Ungeschützt** ändert sich die Liste um {open_list[0]:+.0f} bis {open_list[1]:+.0f} Urteile und der Recall um "
        f"{_pp(open_recall[0])} bis {_pp(open_recall[1])} Prozentpunkte. Von den Entscheiden «von oben» lauten "
        f"{100 * share_no:.0f} % «nicht relevant»; das Lernen senkt dann ganze Rechtsgebiete und Kriterien, und Urteile nahe der "
        "Schwelle fallen von der Liste, auch solche, die Suva gewählt hat.",
        f"- **Geschützt** ändert sich die Liste um {safe_list[0]:+.0f} bis {safe_list[1]:+.0f} Urteile, {safe_recall_text}: "
        "Es geht kein von Suva gewähltes Urteil verloren.",
        f"- Die Rangfolge (AP, geschützt) ändert sich um {safe_ap[0]:+.3f} bis {safe_ap[1]:+.3f}. Die Werte streuen in beide "
        "Richtungen um null: Ein Gewinn in der Rangfolge ist mit diesen Daten **nicht nachgewiesen**.",
        "",
        "Folgerung für den Betrieb: Gelernt wird mit Listenschutz. Entscheide verändern die Bewertung ähnlicher Urteile "
        "(gleiches Rechtsgebiet, gleiche Kriterien) und damit die Reihenfolge, können Urteile auf die Liste bringen, "
        "aber keines von der Liste nehmen. Grenzen: zwei Quartale mit je rund 30 von Suva gewählten Urteilen sind wenig; "
        "Rechtsgebiet und Kriterien sind grobe Merkmale. Ein feineres Merkmal (Ähnlichkeit der Urteilstexte) ist der nächste Schritt.",
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simulate learning from relevance decisions with Suva's real decisions")
    parser.add_argument("--periods", nargs="*", default=list(DEFAULT_LABEL_PERIODS), help="Labelled periods")
    parser.add_argument("--judgements-dir", default="judgements")
    parser.add_argument("--tables-dir", default=DEFAULT_TABLES_DIR)
    parser.add_argument("--list-factor", type=float, default=None, help="Old rule instead of the recall target of configs/triage.json")
    parser.add_argument("--out", default=None, help="Default: <tables-dir>/feedback_learning_sim.csv")
    parser.add_argument("--report", default="results/reports/feedback_learning.md")
    args = parser.parse_args(argv)

    from hack_apertus_coffee_exe.triage.config import load_config

    config = load_config()
    list_factor = args.list_factor if args.list_factor is not None else config.list_factor
    target_recall = None if args.list_factor is not None else config.target_recall  # explicit factor: the old rule
    selected_ids = load_selected_ids(args.judgements_dir)
    records = {}
    for name in args.periods:
        path = scores_path(args.tables_dir, DateRange.from_period(name))
        if not path.exists():
            raise SystemExit(f"{path} is missing: run `python -m hack_apertus_coffee_exe.triage.cli relevance --period {name}` first")
        records[name] = json.loads(path.read_text(encoding="utf-8"))
    rows = simulate(records, selected_ids, list_factor, target_recall)
    out_path = Path(args.out) if args.out else Path(args.tables_dir) / "feedback_learning_sim.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: (round(value, 4) if isinstance(value, float) else value) for key, value in row.items()})
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render(rows, list_factor, target_recall), encoding="utf-8")
    print(f"{len(rows)} rows -> {out_path}, report -> {report_path}")
    print("mean change against the base list, over the quarters (unprotected | list protected):")
    print("protocol  prior_sd   k |  list  recall  precision      AP |  list  recall  precision      AP")
    for entry in summarise(rows):
        cells = " | ".join(
            f"{entry[f'{v}_d_list']:>+5.1f}  {entry[f'{v}_d_recall']:>+6.3f}  {entry[f'{v}_d_precision']:>+9.3f}  {entry[f'{v}_d_ap']:>+6.3f}"
            for v in ("learned", "protected")
        )
        print(f"{entry['protocol']:<8}  {entry['prior_sd']:>7}  {entry['k']:>3} | {cells}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
