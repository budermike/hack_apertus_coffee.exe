"""Relevance-threshold analysis on the Vector-DB (notebook ``notebooks/triage/01_relevance_threshold``).

Scores each quarter leakage-free with ``VectorDB.score_candidates`` (only Suva
references decided *before* the quarter are used) and compares the scores of the
known Suva cases with all other judgements. Reads stored vectors only, so no
embedding model (PyTorch) is needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from hack_apertus_coffee_exe.crawl.vectordb import DEFAULT_DB_DIR, VectorDB

# Quarters with Suva reference cases (Excel BG_Urteile_2025_Q4_2026_Q1 + Export_*.html).
LABELLED_QUARTERS: dict[str, tuple[str, str]] = {
    "2025-Q4": ("2025-10-01", "2025-12-31"),
    "2026-Q1": ("2026-01-01", "2026-03-31"),
}


class ReadOnlyEmbedder:
    """Stands in for the stored embedder: scoring reads saved vectors and never embeds."""

    def __init__(self, name: str, dim: int) -> None:
        self.name = name
        self.dim = dim

    def embed(self, *args: Any, **kwargs: Any) -> np.ndarray:
        raise RuntimeError("read-only analysis: embedding new text is not supported")


def open_vectordb(db_dir: str | Path = DEFAULT_DB_DIR) -> VectorDB:
    """Open the existing Vector-DB without loading the embedding model."""
    meta = json.loads((Path(db_dir) / "embedder_meta.json").read_text(encoding="utf-8"))
    return VectorDB(db_dir, embedder=ReadOnlyEmbedder(meta["name"], int(meta.get("dim", 0))))


def score_quarters(
    db: VectorDB,
    quarters: dict[str, tuple[str, str]] = LABELLED_QUARTERS,
    **score_kwargs: Any,
) -> pd.DataFrame:
    """One row per judgement and quarter: score, rank and whether Suva summarised it."""
    frames = []
    for quarter, (date_from, date_to) in quarters.items():
        rows = db.score_candidates(date_from=date_from, date_to=date_to, **score_kwargs)
        frame = pd.DataFrame(
            {
                "quarter": quarter,
                "case_id": [r["case_id"] for r in rows],
                "decision_date": [r["decision_date"] for r in rows],
                "division": [r.get("division") for r in rows],
                "subject": [r.get("subject") for r in rows],
                "score": [r["score"] for r in rows],
                "max_similarity": [r["max_similarity"] for r in rows],
                "is_reference": [bool(r["is_reference"]) for r in rows],
            }
        )
        frame["rank"] = frame["score"].rank(ascending=False, method="min").astype(int)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def threshold_candidates(ref_scores: pd.Series | np.ndarray) -> dict[str, float]:
    """Candidate cut-offs derived from the scores of the known relevant cases."""
    s = np.asarray(ref_scores, dtype=float)
    mean, std = float(s.mean()), float(s.std(ddof=1))
    return {
        "min": float(s.min()),
        "p05": float(np.percentile(s, 5)),
        "p10": float(np.percentile(s, 10)),
        "mean-1sd": mean - std,
        "mean-2sd": mean - 2 * std,
        "mean-3sd": mean - 3 * std,
    }


def evaluate_threshold(scores: pd.DataFrame, threshold: float) -> dict[str, float]:
    """Recall, precision and workload (cases above the threshold) for one cut-off."""
    flagged = scores["score"] >= threshold
    refs = scores["is_reference"]
    tp = int((flagged & refs).sum())
    n_flagged = int(flagged.sum())
    return {
        "threshold": threshold,
        "flagged": n_flagged,
        "flagged_share": n_flagged / len(scores) if len(scores) else 0.0,
        "recall": tp / int(refs.sum()) if refs.any() else 0.0,
        "precision": tp / n_flagged if n_flagged else 0.0,
    }


def best_f_beta(scores: pd.DataFrame, beta: float = 2.0) -> dict[str, float]:
    """Threshold with the highest F-beta (beta=2: recall counts four times as much)."""
    best: dict[str, float] = {"f_beta": -1.0}
    for threshold in np.unique(scores.loc[scores["is_reference"], "score"]):
        row = evaluate_threshold(scores, float(threshold))
        p, r = row["precision"], row["recall"]
        f = (1 + beta**2) * p * r / (beta**2 * p + r) if (p + r) else 0.0
        if f > best["f_beta"]:
            best = {**row, "f_beta": f}
    return best
