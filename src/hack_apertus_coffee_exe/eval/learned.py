"""Learned relevance score and Apertus re-ranking (notebook ``notebooks/triage/03_relevance_methods``).

Two ideas to rank the Suva cases higher than plain similarity does:

1. **Learn from the labels.** A logistic regression on the centred document embedding
   (+ the three domain flags of ``score_candidates``) is trained on every judgement
   decided before the scored period: Suva cases = 1, all others = 0. Unlike
   similarity it also learns from the judgements Suva did *not* summarise.
2. **Ask Apertus.** The triage prompt (``triage.llm.ApertusTriageClient``) scores a
   shortlist; the final score mixes Apertus and the stage-1 rank like ``final_score``.

Numpy only, no new dependencies.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .relevance import SUBJECT_KEYWORDS, Corpus, _normalise


# --- 1. learned score -----------------------------------------------------------------


def features(corpus: Corpus, keys: list[str], mean: np.ndarray, *, domain_flags: bool = True) -> np.ndarray:
    """Centred, normalised document embedding (+ 8C / accident subject / UVG-in-text flags)."""
    emb = np.vstack([_normalise(_normalise(corpus.vectors[k] - mean).mean(axis=0)) for k in keys])
    if not domain_flags:
        return emb
    meta = corpus.meta
    flags = np.array(
        [
            [
                any(cid.startswith("8C_") for cid in meta.at[k, "case_ids"]),
                any(w in meta.at[k, "subject"].lower() for w in SUBJECT_KEYWORDS),
                k in corpus.fts_keys(),
            ]
            for k in keys
        ],
        dtype=float,
    )
    return np.hstack([emb, flags])


def fit_logistic(X: np.ndarray, y: np.ndarray, *, l2: float = 1.0, iters: int = 3000, lr: float = 0.5) -> tuple[np.ndarray, float]:
    """Class-balanced, L2-regularised logistic regression by gradient descent (deterministic)."""
    w = np.zeros(X.shape[1])
    b = 0.0
    pos = float(y.mean())
    sample_w = np.where(y == 1, 0.5 / pos, 0.5 / (1 - pos))
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-(X @ w + b)))
        g = sample_w * (p - y)
        w -= lr * (X.T @ g / len(y) + l2 * w / len(y))
        b -= lr * g.mean()
    return w, b


def learned_scores(
    corpus: Corpus,
    *,
    date_from: str,
    date_to: str,
    train_from: str | None = None,
    train_to: str | None = None,
    centre: bool = True,
    domain_flags: bool = True,
    l2: float = 1.0,
    extra_positive: tuple[str, ...] = (),
    extra_negative: tuple[str, ...] = (),
) -> pd.DataFrame:
    """Score a period with a model trained only on judgements decided before it.

    ``train_from`` / ``train_to`` choose another labelled window (e.g. the next quarter for a
    backward hold-out fold). ``centre=False`` uses the raw e5 vectors.
    ``extra_positive`` / ``extra_negative`` flip training labels: that is exactly what a
    boost / buff click would do before the next retraining.
    """
    if train_to is None:
        train_to = (pd.Timestamp(date_from) - pd.Timedelta(days=1)).date().isoformat()
    train = corpus.keys(train_from, train_to)
    test = corpus.keys(date_from, date_to)
    mean = corpus.mean_vector(max(date_to, train_to)) if centre else np.zeros(next(iter(corpus.vectors.values())).shape[1])
    y = corpus.meta.loc[train, "is_reference"].to_numpy(float)
    index = {k: i for i, k in enumerate(train)}
    for key in extra_positive:
        if key in index:
            y[index[key]] = 1.0
    for key in extra_negative:
        if key in index:
            y[index[key]] = 0.0
    w, b = fit_logistic(features(corpus, train, mean, domain_flags=domain_flags), y, l2=l2)
    logit = features(corpus, test, mean, domain_flags=domain_flags) @ w + b
    frame = corpus.meta.loc[test, ["doc_key", "case_id", "decision_date", "language", "subject", "is_reference"]].copy()
    frame["is_reference"] = frame["is_reference"].astype(bool)
    frame["score"] = 100.0 / (1.0 + np.exp(-logit))  # probability-like 0..100
    frame = frame.sort_values(["score", "case_id"], ascending=[False, True], ignore_index=True)
    frame["rank"] = np.arange(1, len(frame) + 1)
    return frame


# --- 2. Apertus re-ranking --------------------------------------------------------------


def raw_path(doc_key: str, raw_dir: str | Path = "data/raw") -> Path:
    """Crawl file of a judgement: ``data/raw/<YYYY-MM>/<doc_key>.json``."""
    return Path(raw_dir) / doc_key[:7] / f"{doc_key}.json"


def build_messages(client: Any, doc_key: str, similar: list[dict[str, Any]], references: dict[str, Any]) -> list[dict[str, str]]:
    """Same prompt as the triage pipeline: metadata, keyword hits, nearest Suva cases, text excerpt."""
    from hack_apertus_coffee_exe.triage.embeddings import ReferenceMatch, SimilarityScore
    from hack_apertus_coffee_exe.triage.pipeline import extract_keyword_hits, load_crawl_items

    item = load_crawl_items(raw_path(doc_key))[0]
    matches = []
    for s in similar:
        ref = references.get(s["case_id"])
        matches.append(
            ReferenceMatch(
                case_id=s["case_id"],
                title=ref.title if ref else (s.get("subject") or ""),
                similarity=float(s["similarity"]),
                summary_snippet=(ref.text_for_language(item.language)[:600] if ref else ""),
                bger_url=ref.bger_url if ref else "",
            )
        )
    sims = [m.similarity for m in matches] or [0.0]
    similarity = SimilarityScore(max_similarity=max(sims), top_k_mean_similarity=float(np.mean(sims)), top_matches=matches)
    keywords = extract_keyword_hits(item.text, item.metadata)
    return client.build_messages(item.case_id, item.text, item.metadata, similarity, keywords)


class DecisionCache:
    """JSONL file of Apertus answers per doc_key, so every judgement is asked only once."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.rows: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.rows[row["doc_key"]] = row

    def add(self, row: dict[str, Any]) -> None:
        with self._lock:
            self.rows[row["doc_key"]] = row
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")


# Experiment: v1 (triage/llm.py) treats only accident insurance as Suva-relevant, but 14 of
# the 51 Suva cases before 2026 are invalidity insurance (IV). v2 widens the scope to the IV
# questions that matter for accident insurance too and spells out the 0-100 scale, because
# v1 answers almost only 0 or 95. Derived from the training period only.
PROMPT_V2 = """\
Du bist eine erfahrene Juristin / ein erfahrener Jurist bei der Suva (Schweizerische Unfallversicherungsanstalt).
Du entscheidest, ob die Suva-Rechtsabteilung ein neues Bundesgerichtsurteil intern zusammenfassen soll.

Relevant für die Suva sind:
- Unfallversicherung (UVG/LAA): Unfallbegriff, Kausalzusammenhang, Berufskrankheiten, Leistungen, Renten, Integritätsentschädigung, Rückfälle;
- Invalidenversicherung (IVG/LAI), wenn Fragen betroffen sind, die auch die Unfallversicherung entscheidet: Invaliditätsbemessung, Einkommensvergleich, Beweiswert medizinischer Gutachten, Arbeitsfähigkeit, Revision von Renten;
- Verfahrensrecht der Sozialversicherung (ATSG/LPGA), das für UVG-Verfahren gilt.
Ein Urteil ist besonders relevant, wenn es Rechtsprechung präzisiert oder ändert, einen neuen Sachverhalt beurteilt oder den bisher von der Suva zusammengefassten Fällen inhaltlich ähnelt.
Wenig relevant sind reine Nichteintretensentscheide, Kostenfragen und Fälle ohne neue Rechtsfrage.

Nutze die ganze Skala:
- 90–100: neue oder präzisierte Rechtsprechung zu UVG/IV-Kernfragen, klar zusammenfassen;
- 60–89: relevanter Sachverhalt, wahrscheinlich zusammenfassen;
- 30–59: Bezug zur Sozialversicherung, aber wenig Neues;
- 0–29: kein Bezug zu Unfall- oder Invalidenversicherung oder rein formeller Entscheid.

Antworte AUSSCHLIESSLICH mit gültigem JSON im folgenden Format:
{
  "relevant": true oder false,
  "apertus_score": Ganzzahl von 0 bis 100,
  "reason": "Prägnante juristische Begründung in 2-3 Sätzen: Rechtsgebiet, Bezug zur Suva (oder warum dieser fehlt) und Verweis auf den ähnlichsten Referenzfall mit Geschäftsnummer.",
  "key_topics": ["Stichwort 1", "Stichwort 2"]
}
"""


def ask_apertus(
    doc_keys: list[str],
    similar_by_key: dict[str, list[dict[str, Any]]],
    cache: DecisionCache,
    *,
    client: Any,
    references: dict[str, Any],
    workers: int = 4,
    system_prompt: str | None = None,
) -> None:
    """Score every doc_key that is not cached yet; failures are logged as score None.

    ``system_prompt`` replaces the triage system prompt (None = prompt v1 of ``triage/llm.py``).
    """

    def one(key: str) -> None:
        try:
            messages = build_messages(client, key, similar_by_key.get(key, []), references)
            if system_prompt is not None:
                messages[0] = {"role": "system", "content": system_prompt}
            decision = client.complete(messages)
            cache.add({"doc_key": key, "apertus_score": decision.apertus_score, "relevant": decision.relevant,
                       "reason": decision.reason, "model": decision.model})
        except Exception as exc:  # keep going; the gateway is shared and may time out
            cache.add({"doc_key": key, "apertus_score": None, "error": f"{type(exc).__name__}: {exc}"[:300]})

    todo = [k for k in doc_keys if k not in cache.rows or cache.rows[k].get("apertus_score") is None]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, todo))


def combine(stage1: pd.DataFrame, apertus: dict[str, float | None], weight: float) -> pd.DataFrame:
    """Re-rank: shortlisted judgements by ``weight * Apertus + (1 - weight) * stage-1 percentile``,
    placed before the rest, which keep their stage-1 order (as a two-stage triage would)."""
    frame = stage1.copy()
    frame["stage1_pct"] = 100.0 * frame["score"].rank(pct=True)
    frame["apertus"] = frame["doc_key"].map(apertus)
    has = frame["apertus"].notna()
    frame["score"] = np.where(has, weight * frame["apertus"].fillna(0) + (1 - weight) * frame["stage1_pct"],
                              frame["stage1_pct"] - 1000.0)
    frame = frame.sort_values(["score", "case_id"], ascending=[False, True], ignore_index=True)
    frame["rank"] = np.arange(1, len(frame) + 1)
    return frame
