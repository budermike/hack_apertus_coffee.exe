import numpy as np
import pandas as pd
import pytest

from hack_apertus_coffee_exe.eval.relevance import (
    ReadOnlyEmbedder,
    best_f_beta,
    evaluate_threshold,
    threshold_candidates,
)


def _scores() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "score": [95.0, 90.0, 85.0, 80.0, 75.0, 70.0],
            "is_reference": [True, False, True, False, False, False],
        }
    )


def test_threshold_candidates_min_and_spread():
    cands = threshold_candidates([90.0, 80.0, 100.0])
    assert cands["min"] == 80.0
    assert cands["mean-1sd"] == pytest.approx(90.0 - 10.0)
    assert cands["mean-2sd"] < cands["mean-1sd"]


def test_evaluate_threshold_counts_workload_and_recall():
    row = evaluate_threshold(_scores(), 85.0)
    assert row["flagged"] == 3
    assert row["recall"] == 1.0
    assert row["precision"] == pytest.approx(2 / 3)


def test_best_f_beta_prefers_full_recall():
    best = best_f_beta(_scores(), beta=2.0)
    assert best["threshold"] == 85.0
    assert best["recall"] == 1.0


def test_read_only_embedder_refuses_to_embed():
    with pytest.raises(RuntimeError):
        ReadOnlyEmbedder("x", 3).embed(["text"])
    assert np.isscalar(ReadOnlyEmbedder("x", 3).dim)


from hack_apertus_coffee_exe.eval.relevance import Corpus, recall_curve, score_period


def _corpus() -> Corpus:
    """Every vector shares one big common direction (like e5); topic sits in a small extra part."""
    common = np.array([10.0, 0.0, 0.0])
    topic = {"uv": np.array([0.0, 1.0, 0.0]), "other": np.array([0.0, 0.0, 1.0])}
    docs = [
        ("r1", "8C_1/2025", "2025-12-01", "uv", True),
        ("r2", "8C_2/2025", "2025-12-02", "uv", True),
        ("o1", "2C_1/2025", "2025-12-03", "other", False),
        ("c1", "8C_3/2026", "2026-01-05", "uv", False),
        ("c2", "6B_1/2026", "2026-01-06", "other", False),
    ]
    rows = [
        {"doc_key": k, "case_id": cid, "case_ids": [cid], "decision_date": d, "language": "de",
         "subject": "", "is_reference": ref, "is_negative_reference": False}
        for k, cid, d, _, ref in docs
    ]
    vectors = {k: (common + topic[t])[None, :] for k, _, _, t, _ in docs}
    return Corpus(None, rows=rows, vectors=vectors, fts_keys=set())


def test_centring_spreads_scores_and_keeps_order():
    q = dict(date_from="2026-01-01", date_to="2026-01-31", bonus_scale=0.0)
    raw = score_period(_corpus(), centre=False, **q).set_index("case_id")["score"]
    centred = score_period(_corpus(), centre=True, **q).set_index("case_id")["score"]
    assert raw["8C_3/2026"] > raw["6B_1/2026"]
    assert centred["8C_3/2026"] > centred["6B_1/2026"]
    assert (centred["8C_3/2026"] - centred["6B_1/2026"]) > 10 * (raw["8C_3/2026"] - raw["6B_1/2026"])


def test_buff_click_only_hits_similar_cases_when_centred():
    q = dict(date_from="2026-01-01", date_to="2026-01-31", bonus_scale=0.0, centre=True)
    before = score_period(_corpus(), **q).set_index("case_id")["score"]
    after = score_period(_corpus(), extra_negative=("o1",), **q).set_index("case_id")["score"]
    assert after["6B_1/2026"] < before["6B_1/2026"]          # same topic as the buffed case
    assert after["8C_3/2026"] == pytest.approx(before["8C_3/2026"])  # unrelated case unchanged


def test_recall_curve_counts_hits_in_rank_order():
    frame = pd.DataFrame({"rank": [1, 2, 3], "is_reference": [False, True, True]})
    assert recall_curve(frame).tolist() == [0.0, 0.5, 1.0]
