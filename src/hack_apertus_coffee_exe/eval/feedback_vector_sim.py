"""Prototype: does a vector shift from the lawyers' decisions help? Offline experiment, no model call.

    python -m hack_apertus_coffee_exe.eval.feedback_vector_sim

Same protocol as ``eval/feedback_sim.py`` (Suva's real decisions replayed as lawyer clicks on
the two labelled quarters; base relevance from the calibration of the *other* quarter;
measured on the judgements not decided yet). It compares the learning used in operation
(``triage.feedback_learning``: one correction per area and criterion) with the same learning
plus one content feature from the e5 vectors already in the vector DB:

- ``proto`` (Rocchio): cos(judgement, mean of the "relevant" decisions) minus
  cos(judgement, mean of the "nicht relevant" decisions);
- ``knn``: mean cos to the ``KNN`` most similar "relevant" decisions minus the same for
  "nicht relevant".

The stored vectors are not changed: the "shift" is computed from them. A document vector is
the L2-normalised mean of the judgement's chunk vectors. For the decided judgements the feature
is computed leave-one-out, so no judgement sees its own label. The feature is standardised over
the quarter (no labels used) and gets the same normal prior as the other corrections, so a few
decisions cannot swing it; its weight is learned, so a useless feature ends near 0.

Writes ``results/tables/feedback_vector_sim.csv`` and ``results/reports/feedback_vector_sim.md``.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from hack_apertus_coffee_exe.eval.feedback_sim import measure
from hack_apertus_coffee_exe.triage import calibration, feedback_learning
from hack_apertus_coffee_exe.triage.date_filter import DateRange
from hack_apertus_coffee_exe.triage.relevance_period import (
    DEFAULT_LABEL_PERIODS,
    DEFAULT_TABLES_DIR,
    load_selected_ids,
    scores_path,
)

KS = (5, 10, 20, 40, 80)
PRIOR_SDS = (0.25, 1.0)
SEEDS = 30
KNN = 5
VARIANTS = ("current", "current+proto", "current+knn")
DEFAULT_VECTORS_DIR = "data/processed/vectordb/vectors"


def doc_vector(vectors_dir: Path, record: dict[str, Any]) -> np.ndarray | None:
    """L2-normalised mean of the stored chunk vectors of one judgement; ``None`` if it has none."""
    day = str(record.get("decision_date") or "")
    path = vectors_dir / day[:7] / f"{day}_{record['case_id'].replace('/', '_')}.npz"
    if not path.is_file():
        return None
    with np.load(path) as data:
        chunks = np.asarray(data["dense"], dtype=float)
    if chunks.size == 0:
        return None
    mean = chunks.mean(axis=0)
    norm = float(np.linalg.norm(mean))
    return mean / norm if norm else None


def quarter_data(records: Sequence[dict[str, Any]], cal: dict[str, Any], selected_ids: set[str], vectors_dir: Path):
    """Examples (base relevance, area/criteria, Suva's decision) and their document vectors, aligned."""
    examples, vectors = [], []
    for record in records:
        p0 = calibration.relevance(record, cal)
        vec = doc_vector(vectors_dir, record) if p0 is not None else None
        if p0 is None or vec is None:
            continue
        criteria = [c.get("a_id") for c in (record["triage"].get("b_kriterien") or []) if isinstance(c, dict)]
        examples.append(feedback_learning.Example(p0, feedback_learning.feature_names(record["area"], criteria),
                                                  bool(set(record["case_ids"]) & selected_ids)))
        vectors.append(vec)
    return examples, np.vstack(vectors)


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n else v


def shift_feature(E: np.ndarray, decided: Sequence[int], labels: Sequence[bool], kind: str) -> np.ndarray:
    """Content feature for every judgement from the decided ones (leave-one-out for the decided)."""
    centroid = _unit(E.mean(axis=0))  # stands in for a class nobody decided yet
    decided = list(decided)
    pos = [i for i, rel in zip(decided, labels) if rel]
    neg = [i for i, rel in zip(decided, labels) if not rel]
    sims = E @ E[decided].T if decided else np.zeros((len(E), 0))  # (n, |decided|)
    col = {i: j for j, i in enumerate(decided)}
    out = np.zeros(len(E))
    for i in range(len(E)):
        own = col.get(i)
        p_idx = [col[j] for j in pos if col[j] != own]
        n_idx = [col[j] for j in neg if col[j] != own]
        if kind == "proto":
            r_pos = _unit(E[[decided[j] for j in p_idx]].sum(axis=0)) if p_idx else centroid
            r_neg = _unit(E[[decided[j] for j in n_idx]].sum(axis=0)) if n_idx else centroid
            out[i] = float(E[i] @ r_pos - E[i] @ r_neg)
        else:  # knn
            row = sims[i]
            top = lambda idx: float(np.sort(row[idx])[-KNN:].mean()) if idx else float(E[i] @ centroid)  # noqa: E731
            out[i] = top(p_idx) - top(n_idx)
    sd = float(out.std())
    return (out - out.mean()) / sd if sd > 1e-12 else np.zeros(len(E))


def fit_with_dense(examples, decided, dense: np.ndarray | None, prior_sd: float, steps: int = 50):
    """MAP logistic regression with offset logit p0: binary area/criteria columns plus optional dense columns."""
    names = sorted({name for i in decided for name in examples[i].features})
    index = {name: j for j, name in enumerate(names)}
    n_dense = 0 if dense is None else dense.shape[1]

    def design(rows: Sequence[int]) -> np.ndarray:
        phi = np.zeros((len(rows), len(names) + n_dense))
        for r, i in enumerate(rows):
            for name in examples[i].features:
                if name in index:
                    phi[r, index[name]] = 1.0
            if n_dense:
                phi[r, len(names):] = dense[i]
        return phi

    logit = lambda p: math.log(min(max(p, 1e-4), 1 - 1e-4) / (1 - min(max(p, 1e-4), 1 - 1e-4)))  # noqa: E731
    phi = design(decided)
    offset = np.array([logit(examples[i].p0) for i in decided])
    y = np.array([float(examples[i].relevant) for i in decided])
    theta = np.zeros(phi.shape[1])
    if phi.shape[1]:
        precision = 1.0 / (prior_sd * prior_sd)
        for _ in range(steps):
            p = 1.0 / (1.0 + np.exp(-(offset + phi @ theta)))
            step = np.linalg.solve((phi.T * (p * (1 - p))) @ phi + precision * np.eye(len(theta)),
                                   phi.T @ (p - y) + precision * theta)
            theta -= step
            if float(np.abs(step).max()) < 1e-10:
                break

    def predict(rows: Sequence[int]) -> np.ndarray:
        off = np.array([logit(examples[i].p0) for i in rows])
        return 1.0 / (1.0 + np.exp(-(off + design(rows) @ theta))) if len(theta) else np.array([examples[i].p0 for i in rows])

    weight = float(theta[-1]) if n_dense else float("nan")
    return predict, weight


def one_run(examples, E, decided, prior_sd, t_list):
    decided_set = set(decided)
    rest = [i for i in range(len(examples)) if i not in decided_set]
    y = np.array([examples[i].relevant for i in rest], dtype=bool)
    base = np.array([examples[i].p0 for i in rest])
    labels = [examples[i].relevant for i in decided]
    out: dict[str, float] = {"n_rest": float(len(rest)), "pos_rest": float(y.sum()), "decided_pos": float(sum(labels))}
    for name, value in measure(base, y, t_list).items():
        out[f"{name}_base"] = value
    for variant in VARIANTS:
        dense = None
        if variant != "current":
            dense = shift_feature(E, decided, labels, variant.split("+")[1])[:, None]
        predict, weight = fit_with_dense(examples, decided, dense, prior_sd)
        learned = predict(rest)
        protected = np.where((base > t_list) & (learned <= t_list), t_list * (1 + 1e-9), learned)
        for name, value in measure(protected, y, t_list).items():
            out[f"{name}_{variant}"] = value
        out[f"weight_{variant}"] = weight
    return out


def simulate(records, selected_ids, target_recall, list_factor, vectors_dir: Path):
    rows: list[dict[str, Any]] = []
    names = list(records)
    for test in names:
        for train in names:
            if train == test:
                continue
            cal = calibration.calibrate(records[train], selected_ids, target_recall)
            t_list = calibration.list_threshold(cal, list_factor)
            examples, E = quarter_data(records[test], cal, selected_ids, vectors_dir)
            by_rank = sorted(range(len(examples)), key=lambda i: -examples[i].p0)
            for k in KS:
                if k >= len(examples):
                    continue
                for prior_sd in PRIOR_SDS:
                    top = one_run(examples, E, by_rank[:k], prior_sd, t_list)
                    rows.append({"quarter": test, "protocol": "top", "k": k, "prior_sd": prior_sd, "draw": 0, **top})
                    for seed in range(SEEDS):
                        pick = np.random.default_rng(seed).choice(len(examples), size=k, replace=False).tolist()
                        rows.append({"quarter": test, "protocol": "random", "k": k, "prior_sd": prior_sd, "draw": seed,
                                     **one_run(examples, E, pick, prior_sd, t_list)})
    return rows


def auc(scores: Sequence[float], labels: Sequence[bool]) -> float:
    """Probability that a random relevant judgement scores higher than a random other one (ties count half)."""
    s, y = np.asarray(scores, dtype=float), np.asarray(labels, dtype=bool)
    if not y.any() or y.all():
        return float("nan")
    pos, neg = s[y], s[~y]
    return float((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean())


def upper_bound(records, selected_ids, target_recall, vectors_dir: Path) -> list[dict[str, Any]]:
    """Best case for the content feature: *all* Suva labels instead of a few clicks.

    Per quarter: how well Apertus' relevance and the content features separate Suva's choice
    (AUC), and the ranking (AP) when the prototype of the other quarter's full selection is
    added to Apertus' relevance with a weight fitted on that other quarter.
    """
    from hack_apertus_coffee_exe.eval.feedback_sim import average_precision

    out = []
    names = list(records)
    for test in names:
        for train in names:
            if train == test:
                continue
            cal = calibration.calibrate(records[train], selected_ids, target_recall)
            ex, E = quarter_data(records[test], cal, selected_ids, vectors_dir)
            ex_t, E_t = quarter_data(records[train], cal, selected_ids, vectors_dir)
            y, y_t = np.array([e.relevant for e in ex]), np.array([e.relevant for e in ex_t])
            p0 = np.array([e.p0 for e in ex])
            r_pos, r_neg = _unit(E_t[y_t].mean(axis=0)), _unit(E_t[~y_t].mean(axis=0))
            proto, proto_t = E @ r_pos - E @ r_neg, E_t @ r_pos - E_t @ r_neg
            z = lambda v: (v - v.mean()) / (v.std() or 1.0)  # noqa: E731
            _, weight = fit_with_dense(ex_t, list(range(len(ex_t))), z(proto_t)[:, None], 1.0)
            clipped = np.clip(p0, 1e-4, 1 - 1e-4)
            combined = 1 / (1 + np.exp(-(np.log(clipped / (1 - clipped)) + weight * z(proto))))
            everyone = list(range(len(ex)))
            out.append({
                "quarter": test, "n": len(ex), "pos": int(y.sum()),
                "auc_apertus": auc(p0, y), "auc_proto_other": auc(proto, y),
                "auc_proto_loo": auc(shift_feature(E, everyone, list(y), "proto"), y),
                "auc_knn_loo": auc(shift_feature(E, everyone, list(y), "knn"), y),
                "ap_apertus": average_precision(p0, y), "ap_apertus_proto": average_precision(combined, y), "weight": weight,
            })
    return out


def summarise(rows):
    """Per protocol, prior and k: mean change against the base list, and the share of runs where AP improved."""
    out = []
    for protocol in ("top", "random"):
        for prior_sd in PRIOR_SDS:
            for k in KS:
                group = [r for r in rows if r["protocol"] == protocol and r["prior_sd"] == prior_sd and r["k"] == k]
                if not group:
                    continue
                entry: dict[str, Any] = {"protocol": protocol, "prior_sd": prior_sd, "k": k, "runs": len(group)}
                for variant in VARIANTS:
                    for name in ("list", "recall", "ap"):
                        d = np.array([r[f"{name}_{variant}"] - r[f"{name}_base"] for r in group], dtype=float)
                        entry[f"{variant}|d_{name}"] = float(np.nanmean(d))
                    if variant != "current":
                        gain = np.array([r[f"ap_{variant}"] - r["ap_current"] for r in group], dtype=float)
                        entry[f"{variant}|ap_vs_current"] = float(np.nanmean(gain))
                        entry[f"{variant}|ap_vs_current_sd"] = float(np.nanstd(gain))
                        entry[f"{variant}|better_share"] = float(np.nanmean(gain > 1e-9))
                        entry[f"{variant}|weight"] = float(np.nanmean([r[f"weight_{variant}"] for r in group]))
                out.append(entry)
    return out


def render(summary, rows, target_recall, bound=()) -> str:
    def f(v, pct=False, sign=True):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "–"
        v = 100 * v if pct else v
        return f"{v:+.1f}" if pct and sign else f"{v:+.3f}" if sign else f"{v:.0%}"

    lines = [
        "# Prototyp: Vektor-Verschiebung aus den Relevanz-Entscheiden",
        "",
        "Erzeugt mit `python -m hack_apertus_coffee_exe.eval.feedback_vector_sim`. Nicht von Hand ändern. Kein Modellaufruf.",
        "",
        "Gleiches Protokoll wie `results/reports/feedback_learning.md`: Suvas echte Entscheide werden als Klicks "
        "eingespielt (die ersten `k` der Liste oder `k` zufällige), die Basis-Relevanz kommt aus der Kalibrierung "
        "auf dem anderen Quartal, gemessen wird an den noch nicht entschiedenen Urteilen, mit Listenschutz wie im Betrieb "
        f"(Listenschwelle: Recall-Ziel {target_recall:.0%}).".replace("%", " %"),
        "",
        "- **current**: Lernen wie im Betrieb (eine Korrektur je Rechtsgebiet und Kriterium).",
        "- **+proto**: dazu ein Inhaltsmerkmal: Ähnlichkeit zum Mittel der «relevant»-Entscheide minus zum Mittel der "
        "«nicht relevant»-Entscheide (e5-Vektoren aus der Vector-DB, Rocchio).",
        f"- **+knn**: dazu ein Inhaltsmerkmal: mittlere Ähnlichkeit zu den {KNN} ähnlichsten «relevant»-Entscheiden minus "
        "dasselbe für «nicht relevant».",
        "",
        "AP = Average Precision der Rangfolge der noch offenen Urteile (höher = relevante Urteile weiter oben). "
        "«vs current» ist der gepaarte Unterschied zu **current** im selben Lauf; «besser» der Anteil der Läufe, in denen "
        "die Variante eine höhere AP hat. Recall und Liste in Prozentpunkten bzw. Urteilen gegenüber der Liste ohne Lernen.",
        "",
    ]
    for prior_sd in PRIOR_SDS:
        lines += [f"## Prior-SD {prior_sd}" + (" (wie im Betrieb)" if prior_sd == feedback_learning.DEFAULT_PRIOR_SD else ""), ""]
        lines += ["| Protokoll | k | Läufe | ΔAP current | ΔAP +proto | +proto vs current (SD) | besser | ΔAP +knn | +knn vs current (SD) | besser | Δ Recall current / +proto / +knn | Gewicht proto / knn |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for e in summary:
            if e["prior_sd"] != prior_sd:
                continue
            lines.append(
                f"| {e['protocol']} | {e['k']} | {e['runs']} | {f(e['current|d_ap'])} | {f(e['current+proto|d_ap'])} | "
                f"{f(e['current+proto|ap_vs_current'])} ({e['current+proto|ap_vs_current_sd']:.3f}) | {f(e['current+proto|better_share'], sign=False)} | "
                f"{f(e['current+knn|d_ap'])} | {f(e['current+knn|ap_vs_current'])} ({e['current+knn|ap_vs_current_sd']:.3f}) | "
                f"{f(e['current+knn|better_share'], sign=False)} | "
                f"{f(e['current|d_recall'], True)} / {f(e['current+proto|d_recall'], True)} / {f(e['current+knn|d_recall'], True)} | "
                f"{e['current+proto|weight']:+.2f} / {e['current+knn|weight']:+.2f} |"
            )
        lines.append("")
    if bound:
        lines += [
            "## Obergrenze: alle Suva-Labels statt weniger Klicks",
            "",
            "Wie gut trennt das Inhaltsmerkmal Suvas Auswahl, wenn *alle* Labels bekannt sind? AUC 0.5 = Zufall, 1.0 = perfekt. "
            "«proto (anderes Quartal)» nutzt Suvas ganze Auswahl des anderen Quartals; «LOO» die Labels des eigenen Quartals "
            "ohne das jeweilige Urteil. AP «Apertus + proto»: Prototyp des anderen Quartals mit dort gelerntem Gewicht zur "
            "Apertus-Relevanz addiert.",
            "",
            "| Quartal | Urteile (Suva) | AUC Apertus | AUC proto (anderes Quartal) | AUC proto LOO | AUC knn LOO | AP Apertus | AP Apertus + proto | Gewicht |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for b in bound:
            lines.append(
                f"| {b['quarter']} | {b['n']} ({b['pos']}) | {b['auc_apertus']:.3f} | {b['auc_proto_other']:.3f} | {b['auc_proto_loo']:.3f} | "
                f"{b['auc_knn_loo']:.3f} | {b['ap_apertus']:.3f} | {b['ap_apertus_proto']:.3f} | {b['weight']:+.2f} |"
            )
        lines.append("")
    lines += [
        "## Was diese Zahlen nicht zeigen",
        "",
        "- Zwei Quartale mit zusammen 59 Suva-Urteilen; die zufälligen Ziehungen teilen dieselben Urteile, die Läufe sind nicht unabhängig.",
        "- Suvas Auswahl ist kein Juristen-Feedback im Betrieb: echte Klicks folgen dem, was auf dem Bildschirm steht.",
        "- Gemessen wird die Rangfolge der noch offenen Urteile desselben Quartals, nicht die Wirkung auf spätere Quartale.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--periods", nargs="*", default=list(DEFAULT_LABEL_PERIODS))
    parser.add_argument("--judgements-dir", default="judgements")
    parser.add_argument("--tables-dir", default=DEFAULT_TABLES_DIR)
    parser.add_argument("--vectors-dir", default=DEFAULT_VECTORS_DIR)
    parser.add_argument("--out", default=None, help="Default: <tables-dir>/feedback_vector_sim.csv")
    parser.add_argument("--report", default="results/reports/feedback_vector_sim.md")
    args = parser.parse_args(argv)

    from hack_apertus_coffee_exe.triage.config import load_config

    config = load_config()
    selected_ids = load_selected_ids(args.judgements_dir)
    records = {}
    for name in args.periods:
        path = scores_path(args.tables_dir, DateRange.from_period(name))
        if not path.exists():
            raise SystemExit(f"{path} is missing")
        records[name] = json.loads(path.read_text(encoding="utf-8"))
    rows = simulate(records, selected_ids, config.target_recall, config.list_factor, Path(args.vectors_dir))
    out_path = Path(args.out) if args.out else Path(args.tables_dir) / "feedback_vector_sim.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: (round(value, 4) if isinstance(value, float) else value) for key, value in row.items()})
    summary = summarise(rows)
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    bound = upper_bound(records, selected_ids, config.target_recall, Path(args.vectors_dir))
    report.write_text(render(summary, rows, config.target_recall or 0.0, bound), encoding="utf-8")
    for b in bound:
        print(f"upper bound {b['quarter']}: AUC apertus {b['auc_apertus']:.3f} proto(other) {b['auc_proto_other']:.3f} "
              f"knn(loo) {b['auc_knn_loo']:.3f} | AP {b['ap_apertus']:.3f} -> +proto {b['ap_apertus_proto']:.3f}")
    print(f"{len(rows)} runs -> {out_path}, report -> {report}")
    print("prior  protocol   k | dAP current  +proto vs cur (better)  +knn vs cur (better) | w_proto w_knn")
    for e in summary:
        print(f"{e['prior_sd']:>5} {e['protocol']:>8} {e['k']:>3} | {e['current|d_ap']:+.3f}      "
              f"{e['current+proto|ap_vs_current']:+.3f} ({e['current+proto|better_share']:.0%})        "
              f"{e['current+knn|ap_vs_current']:+.3f} ({e['current+knn|better_share']:.0%}) | "
              f"{e['current+proto|weight']:+.2f}  {e['current+knn|weight']:+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
