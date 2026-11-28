import numpy as np
import pandas as pd
import pytest

from hack_apertus_coffee_exe.eval import learned
from hack_apertus_coffee_exe.eval.learned import (
    PROMPT_V2,
    DecisionCache,
    ask_apertus,
    combine,
    fit_logistic,
    learned_scores,
    raw_path,
)
from hack_apertus_coffee_exe.eval.relevance import Corpus


def test_fit_logistic_separates_simple_classes():
    X = np.array([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0], [0.1, 0.9]])
    y = np.array([1.0, 1.0, 0.0, 0.0])
    w, b = fit_logistic(X, y, l2=0.01)
    assert (X[:2] @ w + b).min() > (X[2:] @ w + b).max()


def _corpus() -> Corpus:
    rng = np.random.default_rng(0)
    rows, vectors = [], {}
    for i in range(40):
        topic = "uv" if i % 4 == 0 else "other"
        date = f"2025-12-{(i % 28) + 1:02d}" if i < 30 else f"2026-01-{(i % 28) + 1:02d}"
        key = f"k{i}"
        rows.append({"doc_key": key, "case_id": f"{'8C' if topic == 'uv' else '2C'}_{i}/2025", "case_ids": [f"x{i}"],
                     "decision_date": date, "language": "de", "subject": "", "is_reference": topic == "uv",
                     "is_negative_reference": False})
        direction = np.array([10.0, 1.0, 0.0]) if topic == "uv" else np.array([10.0, 0.0, 1.0])
        vectors[key] = (direction + rng.normal(0, 0.05, 3))[None, :]
    return Corpus(None, rows=rows, vectors=vectors, fts_keys=set())


def test_learned_scores_rank_suva_like_cases_first_without_leakage():
    frame = learned_scores(_corpus(), date_from="2026-01-01", date_to="2026-01-31")
    assert set(frame["decision_date"]) <= {f"2026-01-{d:02d}" for d in range(1, 32)}
    top = frame.head(int(frame["is_reference"].sum()))
    assert top["is_reference"].all()


def test_combine_puts_shortlist_first_and_mixes_scores():
    stage1 = pd.DataFrame({"doc_key": ["a", "b", "c"], "case_id": ["a", "b", "c"], "score": [3.0, 2.0, 1.0],
                           "is_reference": [False, True, False]})
    out = combine(stage1, {"b": 100.0, "c": 0.0}, weight=0.5)
    assert out["doc_key"].tolist() == ["b", "c", "a"]   # only b and c were asked; a keeps stage-1 order after them


def test_decision_cache_roundtrip(tmp_path):
    cache = DecisionCache(tmp_path / "c.jsonl")
    cache.add({"doc_key": "a", "apertus_score": 90})
    assert DecisionCache(tmp_path / "c.jsonl").rows["a"]["apertus_score"] == 90


def test_ask_apertus_uses_prompt_override_and_skips_cached(tmp_path, monkeypatch):
    seen = []

    class FakeDecision:
        apertus_score, relevant, reason, model = 70.0, True, "ok", "fake"

    class FakeClient:
        def complete(self, messages):
            seen.append(messages[0]["content"])
            return FakeDecision()

    monkeypatch.setattr(learned, "build_messages",
                        lambda client, key, similar, refs: [{"role": "system", "content": "v1"}, {"role": "user", "content": key}])
    cache = DecisionCache(tmp_path / "c.jsonl")
    cache.add({"doc_key": "done", "apertus_score": 10})
    ask_apertus(["done", "new"], {}, cache, client=FakeClient(), references={}, workers=1, system_prompt=PROMPT_V2)
    assert seen == [PROMPT_V2]
    assert cache.rows["new"]["apertus_score"] == 70.0


def test_raw_path_follows_crawl_layout():
    assert raw_path("2026-03-18_2C_497_2025").as_posix() == "data/raw/2026-03/2026-03-18_2C_497_2025.json"
