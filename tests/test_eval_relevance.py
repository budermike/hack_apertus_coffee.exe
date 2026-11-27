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
