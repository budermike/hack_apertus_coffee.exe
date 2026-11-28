"""Prototype vector shift from lawyer decisions (eval/feedback_vector_sim.py). Offline, synthetic data."""

from __future__ import annotations

import numpy as np

from hack_apertus_coffee_exe.eval import feedback_vector_sim as fvs
from hack_apertus_coffee_exe.triage import feedback_learning
from hack_apertus_coffee_exe.triage.feedback_learning import Example


def _unit_rows(m):
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def test_doc_vector_is_the_normalised_mean_of_the_stored_chunks(tmp_path):
    folder = tmp_path / "2026-01"
    folder.mkdir()
    np.savez(folder / "2026-01-05_8C_1_2026.npz", format=np.array("dense"), dense=np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32))
    vec = fvs.doc_vector(tmp_path, {"case_id": "8C_1/2026", "decision_date": "2026-01-05"})
    assert np.allclose(vec, [2 ** -0.5, 2 ** -0.5])
    assert fvs.doc_vector(tmp_path, {"case_id": "8C_2/2026", "decision_date": "2026-01-05"}) is None


def test_shift_feature_points_towards_relevant_decisions_and_never_sees_its_own_label():
    E = _unit_rows(np.array([[1.0, 0.1], [0.9, 0.2], [0.1, 1.0], [0.2, 0.9], [0.95, 0.15]]))
    decided, labels = [0, 2], [True, False]
    for kind in ("proto", "knn"):
        x = fvs.shift_feature(E, decided, labels, kind)
        assert x[1] > x[3] and x[4] > x[3]          # near the relevant one scores higher than near the rejected one
        assert abs(x.mean()) < 1e-9                  # standardised over the quarter
    # leave-one-out: the only "relevant" decision gets no credit from its own vector. Without LOO
    # judgement 0 would sit exactly on its own prototype and top the ranking; with it, its close
    # undecided neighbours 1 and 4 (which do see 0 as relevant) rank above it
    x = fvs.shift_feature(E, [0, 2], [True, False], "proto")
    assert x[4] > x[0] and x[1] > x[0]


def test_fit_without_dense_columns_matches_the_learning_in_operation():
    examples = [
        Example(0.8, ("area:UV", "crit:K1"), False), Example(0.6, ("area:UV",), True),
        Example(0.3, ("area:IV", "crit:K4"), True), Example(0.2, ("area:IV",), False), Example(0.5, ("area:UV", "crit:K1"), True),
    ]
    decided = [0, 1, 2, 3]
    predict, weight = fvs.fit_with_dense(examples, decided, None, 0.25)
    corrections = feedback_learning.fit_corrections([examples[i] for i in decided], 0.25)
    expected = feedback_learning.corrected(examples[4].p0, examples[4].features, corrections)
    assert np.isnan(weight) and abs(float(predict([4])[0]) - expected) < 1e-3


def test_auc_counts_ties_half():
    assert fvs.auc([0.9, 0.1], [True, False]) == 1.0
    assert fvs.auc([0.5, 0.5], [True, False]) == 0.5
    assert np.isnan(fvs.auc([0.5, 0.4], [True, True]))
