"""Offline tests for eval/eval.py (#21): metrics, threshold sweep, missed list, report."""

from __future__ import annotations

import csv
import json
import math

import pytest

from hack_apertus_coffee_exe.crawl import vectordb
from hack_apertus_coffee_exe.eval import eval as ev
from hack_apertus_coffee_exe.triage.date_filter import DateRange
from hack_apertus_coffee_exe.triage.embeddings import TfidfEmbedder


def _cases() -> list[ev.ScoredCase]:
    # Ranked: A(+) 90, B(-) 80, C(+) 80, D(-) 70, E(+) 10
    return [
        ev.ScoredCase("D", 70.0, False),
        ev.ScoredCase("A", 90.0, True),
        ev.ScoredCase("E", 10.0, True),
        ev.ScoredCase("C", 80.0, True),
        ev.ScoredCase("B", 80.0, False),
    ]


def test_f2_weights_recall_four_times():
    assert ev.f2(0.5, 1.0) == pytest.approx(2.5 / 3.0)
    assert ev.f2(1.0, 0.5) == pytest.approx(2.5 / 4.5)
    assert ev.f2(0.0, 1.0) == 0.0
    assert ev.f2(1.0, 0.0) == 0.0


def test_metrics_at_selects_scores_at_or_above_threshold():
    row = ev.metrics_at(_cases(), 80.0)
    assert (row["selected"], row["tp"], row["fp"], row["fn"]) == (3, 2, 1, 1)
    assert row["precision"] == pytest.approx(0.6667)
    assert row["recall"] == pytest.approx(0.6667)

    nothing = ev.metrics_at(_cases(), 99.0)
    assert (nothing["selected"], nothing["precision"], nothing["recall"], nothing["f2"]) == (0, 0.0, 0.0, 0.0)


def test_sweep_has_one_row_per_distinct_score_and_matches_metrics_at():
    rows = ev.sweep_thresholds(_cases())
    assert [r["threshold"] for r in rows] == [90.0, 80.0, 70.0, 10.0]
    assert [r["selected"] for r in rows] == [1, 3, 4, 5]
    for row in rows:
        assert row == ev.metrics_at(_cases(), row["threshold"])


def test_best_f2_and_tie_break():
    # F2: 90 -> 0.385, 80 -> 0.667, 70 -> 0.625, 10 -> 0.882
    assert ev.best_f2(ev.sweep_thresholds(_cases()))["threshold"] == 10.0

    tied = [
        {"threshold": 50.0, "f2": 0.7, "recall": 0.8},
        {"threshold": 40.0, "f2": 0.7, "recall": 0.9},
        {"threshold": 30.0, "f2": 0.7, "recall": 0.9},
    ]
    assert ev.best_f2(tied)["threshold"] == 40.0  # higher recall first, then stricter threshold
    with pytest.raises(ValueError):
        ev.best_f2([])


def test_recall_at_n_and_missed_cases_with_rank():
    assert ev.recall_at_n(_cases(), 1) == pytest.approx(1 / 3)
    assert ev.recall_at_n(_cases(), 3) == pytest.approx(2 / 3)
    assert ev.recall_at_n([ev.ScoredCase("X", 1.0, False)], 5) == 0.0

    missed = ev.missed_cases(_cases(), 80.0)
    assert [(rank, case.case_id) for rank, case in missed] == [(5, "E")]


def test_load_triage_scores_labels_joined_cases_and_adds_unscored_positives(tmp_path):
    path = tmp_path / "triage.json"
    path.write_text(
        json.dumps(
            [
                {"case_id": "8C_1/2026", "rank_score": 77.0, "final_score": 1.0, "judgement_date": "2026-01-05", "language": "de"},
                {"case_id": "8C_3/2026", "rank_score": None, "final_score": 55.0, "metadata": {"subject": "Unfallversicherung"}},
                {"case_id": "5A_9/2026", "final_score": 20.0},
            ]
        ),
        encoding="utf-8",
    )
    labelled = [
        {"case_id": "8C_1/2026", "case_ids": ["8C_1/2026"], "is_reference": True, "decision_date": "2026-01-05", "language": "de", "subject": "UV"},
        # Joined case scored under its second number.
        {"case_id": "8C_2/2026", "case_ids": ["8C_2/2026", "8C_3/2026"], "is_reference": True, "decision_date": "2026-01-06", "language": "fr", "subject": "AA"},
        {"case_id": "5A_9/2026", "case_ids": ["5A_9/2026"], "is_reference": False, "decision_date": "2026-01-07", "language": "de", "subject": "ZGB"},
        # On the Suva list but absent from the triage file.
        {"case_id": "9C_4/2026", "case_ids": ["9C_4/2026"], "is_reference": True, "decision_date": "2026-01-08", "language": "it", "subject": None},
    ]
    cases = {c.case_id: c for c in ev.load_triage_scores(path, labelled)}

    assert cases["8C_1/2026"].score == 77.0 and cases["8C_1/2026"].relevant
    assert cases["8C_3/2026"].score == 55.0 and cases["8C_3/2026"].relevant  # falls back to final_score
    assert cases["8C_3/2026"].subject == "Unfallversicherung"
    assert not cases["5A_9/2026"].relevant
    assert "8C_2/2026" not in cases  # already counted through 8C_3/2026
    assert cases["9C_4/2026"].score == -math.inf and cases["9C_4/2026"].relevant

    evaluation = ev.evaluate("triage", list(cases.values()))
    assert all(math.isfinite(r["threshold"]) for r in evaluation.sweep)
    assert evaluation.sweep[-1]["fn"] == 1  # the unscored Suva judgement is missed at every threshold
    assert [case.case_id for _, case in ev.missed_cases(evaluation.cases, 0.0)] == ["9C_4/2026"]


def _record(case_id: str, decision_date: str, text: str, subject: str = "Unfallversicherung") -> dict:
    safe_id = case_id.replace("/", "_")
    return {
        "id": safe_id,
        "case_id": case_id,
        "case_ids": [case_id],
        "decision_date": decision_date,
        "division": "IV. öffentlich-rechtliche Abteilung",
        "subject": subject,
        "language": "de",
        "url": f"https://search.bger.ch/example/{safe_id}",
        "text": text,
    }


def _build_db(db_dir) -> vectordb.VectorDB:
    emb = TfidfEmbedder()
    emb.fit(
        [
            "Unfallversicherung UVG Suva Kausalzusammenhang Sturz Baustelle Invalidenrente",
            "Ehescheidung Güterrecht ZGB Unterhalt Mietrecht Kündigung",
        ]
    )
    db = vectordb.VectorDB(db_dir=db_dir, embedder=emb)
    db.ingest_records(
        [
            _record("8C_10/2025", "2025-08-20", "Ehescheidung Güterrecht ZGB Unterhalt.", subject="Ehescheidung"),
            _record("8C_50/2025", "2025-11-15", "Suva Unfallversicherung UVG Sturz Baustelle Kausalzusammenhang."),
            _record("8C_70/2026", "2026-02-10", "Unfallversicherung Suva Sturz Baustelle Invalidenrente UVG."),
            _record("5A_80/2026", "2026-02-12", "Ehescheidung Güterrecht ZGB Unterhalt.", subject="Ehescheidung"),
            _record("4A_90/2026", "2026-03-01", "Mietrecht Kündigung.", subject="Mietrecht"),
        ],
        reference_ids={"8C_10/2025", "8C_50/2025", "8C_70/2026"},
    )
    return db


def test_score_vectordb_uses_only_references_of_the_reference_period(tmp_path):
    db = _build_db(tmp_path / "db")
    q1 = DateRange.from_period("2026-Q1")

    cases = ev.score_vectordb(db, q1, DateRange.from_period("2025-Q4"), hybrid=False)
    assert {c.case_id for c in cases} == {"8C_70/2026", "5A_80/2026", "4A_90/2026"}
    assert [c.case_id for c in cases if c.relevant] == ["8C_70/2026"]
    assert ev.rank_cases(cases)[0].case_id == "8C_70/2026"

    # With the Q3/2025 divorce "reference" only, the divorce candidate ranks first instead.
    q3_only = ev.score_vectordb(db, q1, DateRange.from_period("2025-Q3"), hybrid=False)
    assert ev.rank_cases(q3_only)[0].case_id == "5A_80/2026"

    with pytest.raises(ValueError):
        ev.score_vectordb(db, q1, DateRange.from_period("2024-Q4"))


def test_main_writes_report_and_tables(tmp_path, capsys):
    db_dir = tmp_path / "db"
    _build_db(db_dir)
    triage_json = tmp_path / "triage.json"
    triage_json.write_text(
        json.dumps([{"case_id": "5A_80/2026", "final_score": 90.0}, {"case_id": "8C_70/2026", "final_score": 40.0}]),
        encoding="utf-8",
    )
    report = tmp_path / "out" / "eval.md"
    tables = tmp_path / "tables"

    code = ev.main(
        [
            "--db-dir", str(db_dir),
            "--report", str(report),
            "--tables-dir", str(tables),
            "--scores", f"Other scorer={triage_json}",
        ]
    )  # fmt: skip

    assert code == 0
    assert "recall 1.000" in capsys.readouterr().out
    text = report.read_text(encoding="utf-8")
    assert "**Recall: 100.0 %** (1 von 1 Suva-Urteilen gefunden)" in text
    assert "nur die 1 Suva-Urteile mit Entscheiddatum 2025-10-01 bis 2025-12-31" in text
    assert "| Other scorer |" in text
    assert "Keine." in text  # nothing missed at the best threshold

    with (tables / "eval_q1_2026_sweep.csv").open(encoding="utf-8") as handle:
        sweep = list(csv.DictReader(handle))
    assert sweep[0]["tp"] == "1" and sweep[0]["selected"] == "1"
    with (tables / "eval_q1_2026_ground_truth.csv").open(encoding="utf-8") as handle:
        truth = list(csv.DictReader(handle))
    assert [(r["rank"], r["case_id"], r["found"]) for r in truth] == [("1", "8C_70/2026", "True")]


def test_main_rejects_reference_period_overlapping_candidates(tmp_path):
    with pytest.raises(SystemExit):
        ev.main(["--db-dir", str(tmp_path / "db"), "--period", "2026-Q1", "--ref-period", "2026-Q1"])
