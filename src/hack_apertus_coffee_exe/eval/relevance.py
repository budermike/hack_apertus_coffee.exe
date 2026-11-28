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


# --- centred embeddings (notebook 02) -----------------------------------------------
#
# e5 puts all judgements close together (cosine ~0.92 even across unrelated fields), so
# the similarity part of the score barely separates. Subtracting the corpus mean vector
# ("centring") removes the direction all judgements share. The scorer below mirrors
# ``VectorDB.score_candidates`` (hybrid formula, same domain bonus) with an optional
# centring step, so both variants are measured with identical rules.

SUBJECT_KEYWORDS = ("unfall", "accident", "infortun", "uvg", "laa", "lainf")
FTS_KEYWORDS = ("UVG", "LAA", "LAINF", "Suva")


class Corpus:
    """All judgements of the Vector-DB with their chunk vectors, loaded once."""

    def __init__(
        self,
        db: VectorDB | None,
        *,
        rows: list[dict[str, Any]] | None = None,
        vectors: dict[str, np.ndarray] | None = None,
        fts_keys: set[str] | None = None,
    ) -> None:
        """Load from ``db``; tests pass ``rows``/``vectors``/``fts_keys`` directly instead."""
        self.db = db
        rows = rows if rows is not None else db.query()
        self.meta = pd.DataFrame(rows).set_index("doc_key", drop=False)
        self.meta[["subject", "language"]] = self.meta[["subject", "language"]].fillna("")
        self.vectors = vectors if vectors is not None else {r["doc_key"]: db.load_vectors(r["doc_key"]) for r in rows}
        self._fts_keys = fts_keys

    def keys(self, date_from: str | None = None, date_to: str | None = None) -> list[str]:
        dates = self.meta["decision_date"]
        mask = pd.Series(True, index=self.meta.index)
        if date_from:
            mask &= dates >= date_from
        if date_to:
            mask &= dates <= date_to
        return [k for k in self.meta.index[mask] if self.vectors[k].shape[0] > 0]

    def mean_vector(self, date_to: str) -> np.ndarray:
        """Mean chunk vector of every judgement decided up to ``date_to`` (no labels used)."""
        return np.vstack([self.vectors[k] for k in self.keys(date_to=date_to)]).mean(axis=0)

    def fts_keys(self) -> set[str]:
        if self._fts_keys is None:
            self._fts_keys = {
                hit["doc_key"] for kw in FTS_KEYWORDS for hit in self.db.fulltext_search(kw, top_k=100000)
            }
        return self._fts_keys

    def domain_bonus(self, doc_key: str) -> float:
        """Same fixed bonus as ``score_candidates``: 8C chamber, accident subject, UVG/LAA in text."""
        row = self.meta.loc[doc_key]
        bonus = 0.05 if any(cid.startswith("8C_") for cid in row["case_ids"]) else 0.0
        if any(kw in (row["subject"] or "").lower() for kw in SUBJECT_KEYWORDS):
            bonus += 0.08
        if doc_key in self.fts_keys():
            bonus += 0.06
        return bonus


def _normalise(mat: np.ndarray) -> np.ndarray:
    return mat / np.maximum(np.linalg.norm(mat, axis=-1, keepdims=True), 1e-12)


def score_period(
    corpus: Corpus,
    *,
    date_from: str,
    date_to: str,
    ref_date_from: str | None = None,
    ref_date_to: str | None = None,
    centre: bool = True,
    bonus_scale: float = 1.0,
    top_k: int = 3,
    extra_positive: tuple[str, ...] = (),
    extra_negative: tuple[str, ...] = (),
    negative_weight: float = 0.3,
) -> pd.DataFrame:
    """Score every judgement of a period against earlier Suva references.

    ``centre=False, bonus_scale=1`` reproduces the ranking of ``score_candidates``.
    ``extra_positive`` / ``extra_negative`` simulate a boost / buff click on those
    doc_keys without touching the database. Scores are cosine-based (x100); only the
    ranking and thresholds within one variant are comparable.
    """
    if ref_date_to is None:
        ref_date_to = (pd.Timestamp(date_from) - pd.Timedelta(days=1)).date().isoformat()
    meta = corpus.meta
    cand_keys = corpus.keys(date_from, date_to)
    cand_ids = {cid for k in cand_keys for cid in meta.at[k, "case_ids"]}
    ref_window = set(corpus.keys(ref_date_from, ref_date_to))

    def refs(flag: str, extra: tuple[str, ...]) -> list[str]:
        keys = [k for k in ref_window if meta.at[k, flag]] + [k for k in extra if k in ref_window]
        return sorted({k for k in keys if not set(meta.at[k, "case_ids"]) & cand_ids})

    pos_keys = refs("is_reference", extra_positive)
    neg_keys = refs("is_negative_reference", extra_negative)

    mean = corpus.mean_vector(date_to) if centre else 0.0

    def prep(key: str) -> np.ndarray:
        return _normalise(corpus.vectors[key] - mean)

    pos = [prep(k) for k in pos_keys]
    pos_mat = np.vstack(pos)
    pos_owner = np.concatenate([np.full(len(v), i) for i, v in enumerate(pos)])
    pos_doc = _normalise(np.vstack([v.mean(axis=0) for v in pos]))
    neg_mat = np.vstack([prep(k) for k in neg_keys]) if neg_keys else None
    k = min(top_k, len(pos_keys))

    out = []
    for key in cand_keys:
        c = prep(key)
        per_ref = np.full(len(pos_keys), -np.inf)
        np.maximum.at(per_ref, pos_owner, (c @ pos_mat.T).max(axis=0))
        top = np.argsort(-per_ref, kind="stable")[:k]
        chunk_sim = float(per_ref[top].mean())
        doc_sim = float(np.sort((_normalise(c.mean(axis=0)) @ pos_doc.T).ravel())[-k:].mean())
        similarity = 0.25 * doc_sim + 0.75 * chunk_sim
        bonus = corpus.domain_bonus(key) * bonus_scale
        penalty = max(0.0, float((c @ neg_mat.T).max())) * negative_weight if neg_mat is not None else 0.0
        row = meta.loc[key]
        out.append(
            {
                "doc_key": key,
                "case_id": row["case_id"],
                "decision_date": row["decision_date"],
                "language": row["language"],
                "subject": row["subject"],
                "is_reference": bool(row["is_reference"]),
                "similarity": similarity,
                "bonus": bonus,
                "penalty": penalty,
                "score": 100.0 * (similarity + bonus - penalty),
                "top_refs": [meta.at[pos_keys[i], "case_id"] for i in top],
                "top_ref_subjects": [meta.at[pos_keys[i], "subject"] for i in top],
            }
        )
    frame = pd.DataFrame(out).sort_values(["score", "case_id"], ascending=[False, True], ignore_index=True)
    frame["rank"] = np.arange(1, len(frame) + 1)
    return frame


def to_scored_cases(frame: pd.DataFrame) -> list:
    """Rows as ``eval.eval.ScoredCase`` so Abi's sweep, F2 and Recall@n apply unchanged."""
    from hack_apertus_coffee_exe.eval.eval import ScoredCase

    return [
        ScoredCase(
            case_id=r.case_id,
            score=float(r.score),
            relevant=bool(r.is_reference),
            decision_date=r.decision_date,
            language=r.language or "",
            subject=r.subject or "",
        )
        for r in frame.itertuples()
    ]


def summary_row(label: str, cases: list) -> dict[str, Any]:
    """One comparison line with the metrics of ``results/eval.md``."""
    from hack_apertus_coffee_exe.eval.eval import evaluate, recall_at_n

    ev = evaluate(label, cases)
    r90 = next((r for r in ev.sweep if r["recall"] >= 0.9), None)
    r100 = next((r for r in ev.sweep if r["recall"] >= 1.0), None)
    return {
        "Variante": label,
        "F2": ev.best["f2"],
        "Recall (bestes F2)": ev.best["recall"],
        "Precision (bestes F2)": ev.best["precision"],
        "Ausgewählt (bestes F2)": ev.best["selected"],
        "Recall@30": recall_at_n(ev.cases, 30),
        "Recall@100": recall_at_n(ev.cases, 100),
        "Ausgewählt für 90 % Recall": r90["selected"] if r90 else None,
        "Ausgewählt für 100 % Recall": r100["selected"] if r100 else None,
    }


SUBJECT_AREAS = {
    "Unfall": ("unfall", "accident", "infortun"),
    "Invalidität": ("invalid",),
    "Kranken": ("kranken", "maladie", "malattia"),
    "Arbeitslosen": ("arbeitslos", "chômage", "disoccup"),
}


def subject_area(subject: str | None) -> str:
    """Coarse legal field of a subject line (for checking whether nearest references fit)."""
    low = (subject or "").lower()
    for area, words in SUBJECT_AREAS.items():
        if any(w in low for w in words):
            return area
    return "andere"


def pair_similarity(
    corpus: Corpus, keys_a: list[str], keys_b: list[str], *, centre: bool, date_to: str, n: int = 400, seed: int = 0
) -> float:
    """Mean cosine of the document vectors of ``n`` random pairs (a from keys_a, b from keys_b)."""
    rng = np.random.default_rng(seed)
    mean = corpus.mean_vector(date_to) if centre else 0.0

    def doc(key: str) -> np.ndarray:
        return _normalise(_normalise(corpus.vectors[key] - mean).mean(axis=0))

    pairs = [(a, b) for a, b in zip(rng.choice(keys_a, n), rng.choice(keys_b, n)) if a != b]
    return float(np.mean([doc(a) @ doc(b) for a, b in pairs]))


def recall_curve(frame: pd.DataFrame, max_n: int = 300) -> pd.Series:
    """Share of Suva cases among the first n ranked judgements, for n = 1..max_n."""
    hits = frame.sort_values("rank")["is_reference"].to_numpy()[:max_n].cumsum()
    return pd.Series(hits / frame["is_reference"].sum(), index=np.arange(1, len(hits) + 1))
