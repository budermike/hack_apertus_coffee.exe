"""Simulation of learning from relevance decisions (eval/feedback_sim.py). No network, no model."""

import csv

import numpy as np
import pytest

from hack_apertus_coffee_exe.eval import feedback_sim
from hack_apertus_coffee_exe.triage.feedback_learning import Example


def test_average_precision_and_list_measures():
    y = np.array([True, False, True, False])
    assert feedback_sim.average_precision(np.array([0.9, 0.8, 0.7, 0.1]), y) == pytest.approx((1 / 1 + 2 / 3) / 2)
    assert np.isnan(feedback_sim.average_precision(np.array([0.9, 0.1]), np.array([False, False])))
    measured = feedback_sim.measure(np.array([0.9, 0.8, 0.02, 0.01]), y, 0.05)
    assert measured["list"] == 2 and measured["recall"] == 0.5 and measured["precision"] == 0.5


def test_one_run_measures_only_the_undecided_and_the_protected_list_never_loses_a_judgement():
    examples = [
        Example(0.9, ("area:UV", "crit:K1"), False),
        Example(0.8, ("area:UV", "crit:K1"), False),
        Example(0.06, ("area:UV", "crit:K1"), True),   # on the list, close to the threshold
        Example(0.5, ("area:IV",), True),
    ]
    run = feedback_sim.one_run(examples, [0, 1], prior_sd=2.0, t_list=0.05)
    assert run["n_rest"] == 2 and run["pos_rest"] == 2 and run["decided_pos"] == 0
    assert run["recall_base"] == 1.0
    assert run["recall_learned"] == 0.5       # two strong "no" push the UV judgement off the list
    assert run["recall_protected"] == 1.0     # ... unless the list is protected
    assert run["list_protected"] >= run["list_base"]


def test_simulation_on_the_committed_score_files(tmp_path):
    out, report = tmp_path / "sim.csv", tmp_path / "report.md"
    assert feedback_sim.main(["--out", str(out), "--report", str(report)]) == 0
    text = report.read_text(encoding="utf-8")
    assert "**(Betrieb)**" in text and "nicht nachgewiesen" in text and "Es geht kein von Suva gewähltes Urteil verloren" in text
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert {r["quarter"] for r in rows} == {"2025-Q4", "2026-Q1"}
    assert {r["protocol"] for r in rows} == {"top", "random"}
    assert len(rows) == 2 * 2 * len(feedback_sim.KS) * len(feedback_sim.PRIOR_SDS)
    for r in rows:
        if r["recall_base"] not in ("", "nan"):
            assert float(r["recall_protected"]) >= float(r["recall_base"]) - 1e-9
            assert float(r["list_protected"]) >= float(r["list_base"]) - 1e-9
    summary = feedback_sim.summarise([{k: (float(v) if k not in ("quarter", "calibrated_on", "protocol") else v) for k, v in r.items()} for r in rows])
    assert len(summary) == 2 * len(feedback_sim.KS) * len(feedback_sim.PRIOR_SDS)
