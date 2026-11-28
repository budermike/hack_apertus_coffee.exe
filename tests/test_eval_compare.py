import json

import numpy as np
import pandas as pd
import pytest

from hack_apertus_coffee_exe.eval.compare import (
    FOLDS,
    combine_ranks,
    external_scores,
    fold_metrics,
    pooled,
    recall_curve_pooled,
)
from hack_apertus_coffee_exe.eval.relevance import Corpus


def _stage1() -> pd.DataFrame:
    return pd.DataFrame({
        "doc_key": ["a", "b", "c", "d"],
        "case_id": ["a", "b", "c", "d"],
        "score": [4.0, 3.0, 2.0, 1.0],
        "is_reference": [False, False, True, True],
    })


def test_folds_never_train_on_the_measured_quarter():
    for fold in FOLDS:
        assert fold.train_to < fold.date_from or fold.train_from > fold.date_to
        assert fold.prompt_ref_to < fold.date_from


def test_combine_without_stage2_keeps_stage1_order():
    out = combine_ranks(_stage1(), None)
    assert out["doc_key"].tolist() == ["a", "b", "c", "d"]


def test_combine_puts_stage2_scored_first_and_lets_stage2_reorder():
    out = combine_ranks(_stage1(), {"c": 90.0, "d": 95.0, "b": 0.0}, weight=1.0)
    assert out["doc_key"].tolist() == ["d", "c", "b", "a"]


def test_fold_metrics_counts_hits_and_list_lengths():
    frame = _stage1().assign(rank=[1, 2, 3, 4])
    m = fold_metrics(frame)
    assert (m["suva"], m["top30"], m["len90"], m["len100"]) == (2, 2, 4, 4)


def test_pooled_adds_both_quarters():
    q = _stage1().assign(rank=[3, 4, 1, 2])   # both Suva cases on top
    p = pooled([q, q])
    assert p["Top 30 je Quartal (von 59)"] == 4
    assert p["Listenlänge für 100 % Recall"] == 4
    assert recall_curve_pooled([q, q], 4).tolist() == [0.5, 1.0, 1.0, 1.0]


def test_external_scores_reads_jsonl_with_case_id_and_date(tmp_path):
    corpus = Corpus(None, rows=[{"doc_key": "2026-01-05_8C_1_2025", "case_id": "8C_1/2025", "case_ids": ["8C_1/2025"],
                                 "decision_date": "2026-01-05", "language": "de", "subject": "",
                                 "is_reference": True, "is_negative_reference": False}],
                    vectors={"2026-01-05_8C_1_2025": np.ones((1, 3))}, fts_keys=set())
    path = tmp_path / "ben.jsonl"
    path.write_text(json.dumps({"case_id": "8C_1/2025", "decision_date": "2026-01-05", "score": 77}) + "\n")
    assert external_scores(path, corpus) == {"2026-01-05_8C_1_2025": pytest.approx(77.0)}


def test_one_pass_drops_the_second_pass(tmp_path):
    from hack_apertus_coffee_exe.eval.compare import apertus_calls, relevance_records

    rows = [{"case_id": "8C_1/2025", "triage": {"d_score": 80}, "deep": {"e_score": 70}},
            {"case_id": "8C_2/2025", "triage": {"d_score": 20}, "deep": None},
            {"case_id": "2C_1/2025", "triage": None, "deep": None}]
    (tmp_path / "relevance_scores_q1_2026.json").write_text(json.dumps(rows))
    assert apertus_calls(tmp_path, "2026-Q1", passes=2) == 3
    assert apertus_calls(tmp_path, "2026-Q1", passes=1) == 2
    assert all(r["deep"] is None for r in relevance_records(tmp_path, "2026-Q1", passes=1))
