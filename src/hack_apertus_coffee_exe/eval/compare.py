"""Overall comparison of relevance methods (notebook ``notebooks/triage/04_gesamtvergleich``).

Same protocol as ``eval/relevance_eval.py`` (issue #62): two labelled quarters
(2025-Q4, 2026-Q1, 59 Suva judgements together). Each quarter is scored with
knowledge from the *other* quarter only (one forward and one backward fold), and
the numbers of both folds are added up.

Stage 1 (free, every judgement): similarity to the Suva references or the learned
score, each on raw or centred e5 vectors. Stage 2 (Apertus, a subset): 8B with the
triage prompt (v1) or prompt v2, Abi's two-pass 70B scoring with calibration (#62),
or any external score file (e.g. a thinking-mode run). Mixed by rank.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from .learned import DecisionCache, learned_scores
from .relevance import Corpus, score_period


@dataclass(frozen=True)
class Fold:
    """Score ``test`` with labels (references, training data, calibration) from ``train`` only."""

    name: str  # quarter that is measured, e.g. "2026-Q1"
    date_from: str
    date_to: str
    train_name: str
    train_from: str
    train_to: str
    # Stage-2 prompts may only show Suva cases decided before the measured quarter.
    prompt_ref_from: str
    prompt_ref_to: str


FOLDS = (
    Fold("2025-Q4", "2025-10-01", "2025-12-31", "2026-Q1", "2026-01-01", "2026-03-31", "2025-07-01", "2025-09-30"),
    Fold("2026-Q1", "2026-01-01", "2026-03-31", "2025-Q4", "2025-10-01", "2025-12-31", "2025-10-01", "2025-12-31"),
)

STAGE1 = {
    "Ähnlichkeit, roh (heute)": ("similarity", False),
    "Ähnlichkeit, zentriert": ("similarity", True),
    "Gelernt, roh": ("learned", False),
    "Gelernt, zentriert": ("learned", True),
}


def stage1(corpus: Corpus, fold: Fold, kind: str, centre: bool) -> pd.DataFrame:
    """Stage-1 ranking of every judgement of the fold's quarter."""
    if kind == "similarity":
        return score_period(corpus, date_from=fold.date_from, date_to=fold.date_to,
                            ref_date_from=fold.train_from, ref_date_to=fold.train_to, centre=centre)
    return learned_scores(corpus, date_from=fold.date_from, date_to=fold.date_to,
                          train_from=fold.train_from, train_to=fold.train_to, centre=centre)


# --- stage 2 sources -------------------------------------------------------------------


def apertus_8b(version: str, fold: Fold) -> dict[str, float]:
    """Cached 8B answers (``results/tables/apertus_<version>_scores_<quarter>.jsonl``)."""
    return {k: r["apertus_score"] for k, r in DecisionCache(apertus_8b_path(version, fold)).rows.items()
            if r.get("apertus_score") is not None}


def apertus_8b_path(version: str, fold: Fold) -> Path:
    year, q = fold.name.split("-")  # 2026-Q1 -> apertus_v1_scores_q1_2026.jsonl
    return Path(f"results/tables/apertus_{version}_scores_{q.lower()}_{year}.jsonl")


def fill_apertus_8b(db: Any, fold: Fold, version: str, doc_keys: list[str], *, client: Any, references: dict[str, Any]) -> None:
    """Ask Apertus 8B for every doc_key not cached yet. The prompt shows only Suva cases decided
    before the quarter (``prompt_ref_*``), so stage 2 never sees later labels."""
    from .learned import PROMPT_V2, ask_apertus

    rows = db.score_candidates(date_from=fold.date_from, date_to=fold.date_to,
                               ref_date_from=fold.prompt_ref_from, ref_date_to=fold.prompt_ref_to)
    similar = {r["doc_key"]: r["similar_cases"] for r in rows}
    ask_apertus(doc_keys, similar, DecisionCache(apertus_8b_path(version, fold)), client=client, references=references,
                system_prompt=PROMPT_V2 if version == "v2" else None)


def doc_key_of(corpus: Corpus, case_id: str, decision_date: str) -> str | None:
    meta = corpus.meta
    hits = meta.index[(meta["case_id"] == case_id) & (meta["decision_date"] == decision_date)]
    return hits[0] if len(hits) else None


def relevance_records(tables_dir: str | Path, quarter: str, passes: int = 2) -> list[dict[str, Any]]:
    """Score file of ``triage.cli relevance`` for ``quarter`` (``2026-Q1``).

    ``passes=1`` drops the second pass ("Vertiefung"): it is a separate call that only runs
    for a triage score >= 50, so without it the records are exactly what a one-pass run gives.
    """
    year, q = quarter.split("-")
    records = json.loads((Path(tables_dir) / f"relevance_scores_{q.lower()}_{year}.json").read_text(encoding="utf-8"))
    if passes == 1:
        records = [{**r, "deep": None} for r in records]
    return records


def apertus_calls(tables_dir: str | Path, quarter: str, passes: int = 2) -> int:
    """Gateway calls of one quarter: one per judgement in pass 1, plus pass 2 where it ran."""
    records = relevance_records(tables_dir, quarter, passes)
    return sum(bool(r.get("triage")) for r in records) + sum(bool(r.get("deep")) for r in records)


def calibrated_lists(tables_dir: str | Path, passes: int = 2, factors: tuple[float, ...] = (1.0, 0.5, 0.3)) -> pd.DataFrame:
    """Abi's own measurement (``eval/relevance_eval``): calibrate on one quarter, list on the other, both added."""
    from hack_apertus_coffee_exe.eval.relevance_eval import hits_at, holdout, pooled as pooled_list
    from hack_apertus_coffee_exe.triage.relevance_period import load_selected_ids

    records = {f.name: relevance_records(tables_dir, f.name, passes) for f in FOLDS}
    folds = holdout(records, load_selected_ids("judgements"))
    top30 = sum(hits_at(f["p"], f["y"]) for f in folds)
    rows = []
    for factor in factors:
        r = pooled_list(folds, factor)
        rows.append({"list_factor": factor, "Auf der Liste": r["selected"], "Gefunden (von 59)": r["tp"],
                     "Recall": r["recall"], "F2": r["f2"], "Top 30 je Quartal": top30})
    return pd.DataFrame(rows)


def abi_two_pass(corpus: Corpus, fold: Fold, tables_dir: str | Path = "results/tables", passes: int = 2) -> dict[str, float]:
    """Abi's relevance value p (0..100) for the fold's quarter, calibrated on the train quarter (#62).

    Reads the score files of ``triage.cli relevance``; judgements outside Abi's candidate
    set (not III./IV. public-law division in UV/IV/MV) or without an answer get no value.
    ``passes=1`` measures the same answers without the second pass.
    """
    from hack_apertus_coffee_exe.triage import calibration
    from hack_apertus_coffee_exe.triage.relevance_period import load_selected_ids

    selected = load_selected_ids("judgements")
    cal = calibration.calibrate(relevance_records(tables_dir, fold.train_name, passes), selected)
    out: dict[str, float] = {}
    for record in relevance_records(tables_dir, fold.name, passes):
        p = calibration.relevance(record, cal)
        key = doc_key_of(corpus, record["case_id"], record["decision_date"])
        if p is not None and key is not None:
            out[key] = 100.0 * p
    return out


def external_scores(path: str | Path, corpus: Corpus) -> dict[str, float]:
    """Scores from another run: JSON/JSONL rows with ``doc_key`` or ``case_id`` + ``decision_date`` and ``score``."""
    text = Path(path).read_text(encoding="utf-8").strip()
    rows = json.loads(text) if text.startswith("[") else [json.loads(line) for line in text.splitlines() if line.strip()]
    out: dict[str, float] = {}
    for row in rows:
        key = row.get("doc_key") or doc_key_of(corpus, row["case_id"], row["decision_date"])
        if key is not None and row.get("score") is not None:
            out[key] = float(row["score"])
    return out


# --- combining and measuring -----------------------------------------------------------


def combine_ranks(stage1_frame: pd.DataFrame, stage2: dict[str, float] | None, weight: float = 0.5) -> pd.DataFrame:
    """Final ranking: judgements with a stage-2 value first, by ``weight`` x stage-2 percentile +
    (1 - ``weight``) x stage-1 percentile; then the rest in stage-1 order (two-stage triage).

    Percentiles put both stages on the same scale (Apertus 8B answers almost only 0 / 95,
    Abi's p lies mostly below 0.3, stage-1 scores have their own range).
    """
    frame = stage1_frame.copy()
    frame["s1"] = 100.0 * frame["score"].rank(pct=True)
    if not stage2:
        frame["final"] = frame["s1"]
    else:
        s2 = frame["doc_key"].map(stage2)
        has = s2.notna()
        s2_pct = 100.0 * s2[has].rank(pct=True)
        frame["final"] = frame["s1"] - 1000.0
        frame.loc[has, "final"] = weight * s2_pct + (1 - weight) * frame.loc[has, "s1"]
    frame = frame.sort_values(["final", "s1", "case_id"], ascending=[False, False, True], ignore_index=True)
    frame["rank"] = np.arange(1, len(frame) + 1)
    return frame


def fold_metrics(frame: pd.DataFrame) -> dict[str, float]:
    """Threshold-free numbers of one ranked quarter."""
    y = frame.sort_values("rank")["is_reference"].to_numpy(bool)
    hits = y.cumsum()
    pos = int(y.sum())

    def length_for(share: float) -> int:
        need = int(np.ceil(share * pos))
        return int(np.argmax(hits >= need) + 1)

    return {"suva": pos, "top30": int(hits[min(29, len(y) - 1)]), "top50": int(hits[min(49, len(y) - 1)]),
            "top100": int(hits[min(99, len(y) - 1)]), "len90": length_for(0.9), "len100": length_for(1.0)}


def pooled(frames: list[pd.DataFrame]) -> dict[str, float]:
    """Both folds added up: hits of 59 Suva judgements, list lengths summed over the two quarters."""
    rows = [fold_metrics(f) for f in frames]
    total = sum(r["suva"] for r in rows)
    return {
        "Top 30 je Quartal (von 59)": sum(r["top30"] for r in rows),
        "Recall Top 50": sum(r["top50"] for r in rows) / total,
        "Recall Top 100": sum(r["top100"] for r in rows) / total,
        "Listenlänge für 90 % Recall": sum(r["len90"] for r in rows),
        "Listenlänge für 100 % Recall": sum(r["len100"] for r in rows),
    }


def recall_curve_pooled(frames: list[pd.DataFrame], max_n: int = 300) -> pd.Series:
    """Share of the 59 Suva judgements found when the first n judgements of *each* quarter are read."""
    hits = np.zeros(max_n)
    total = 0
    for frame in frames:
        y = frame.sort_values("rank")["is_reference"].to_numpy(bool)[:max_n]
        h = y.cumsum()
        hits += np.pad(h, (0, max_n - len(h)), constant_values=h[-1] if len(h) else 0)
        total += int(frame["is_reference"].sum())
    return pd.Series(hits / total, index=np.arange(1, max_n + 1))


def run_grid(
    corpus: Corpus,
    stage2_sources: dict[str, Callable[[Fold], dict[str, float]] | None],
    *,
    weight: float = 0.5,
    folds: tuple[Fold, ...] = FOLDS,
) -> tuple[pd.DataFrame, dict[tuple[str, str], list[pd.DataFrame]]]:
    """Every stage-1 variant x every stage-2 source (None = stage 1 only), pooled over the folds.

    Also adds "stage 2 alone" rows (weight 1, unscored judgements after them in raw-similarity order).
    """
    s1_frames = {(name, f.name): stage1(corpus, f, *spec) for name, spec in STAGE1.items() for f in folds}
    s2_values = {(name, f.name): (src(f) if src else None) for name, src in stage2_sources.items() for f in folds}
    rankings: dict[tuple[str, str], list[pd.DataFrame]] = {}
    rows = []
    for s1_name in [*STAGE1, "nur Stufe 2"]:
        for s2_name in stage2_sources:
            if s1_name == "nur Stufe 2" and stage2_sources[s2_name] is None:
                continue
            frames = []
            for f in folds:
                base = s1_frames[("Ähnlichkeit, roh (heute)" if s1_name == "nur Stufe 2" else s1_name, f.name)]
                w = 1.0 if s1_name == "nur Stufe 2" else weight
                frames.append(combine_ranks(base, s2_values[(s2_name, f.name)], w))
            rankings[(s1_name, s2_name)] = frames
            rows.append({"Stufe 1": s1_name, "Stufe 2": s2_name, **pooled(frames)})
    return pd.DataFrame(rows), rankings
