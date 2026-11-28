"""Recall target for the list threshold (#75, "90 % drin"). Offline, no Apertus call."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from hack_apertus_coffee_exe.triage import calibration as cal
from hack_apertus_coffee_exe.triage import cli
from hack_apertus_coffee_exe.triage.config import load_config


def test_recall_threshold_keeps_the_target_share_above_it():
    p = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.15, 0.1, 0.05, 0.01]
    y = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0]  # 10 positives
    for target, above in ((0.9, 9), (1.0, 10), (0.5, 5), (0.85, 9)):
        t = cal.recall_threshold(p, y, target)
        assert sum(v > t for v, label in zip(p, y) if label) == above, target
    with pytest.raises(ValueError):
        cal.recall_threshold([0.5], [0], 0.9)


def _records(rows):
    return [{"case_id": c, "case_ids": [c], "area": a, "triage": {"d_score": t}, "deep": {"e_score": d}}
            for c, a, t, d, _ in rows]


ROWS = [("U1", "UV", 95, 90, 1), ("U2", "UV", 85, 70, 1), ("U3", "UV", 70, 40, 1), ("U4", "UV", 60, 20, 0),
        ("U5", "UV", 40, 0, 1), ("U6", "UV", 30, 0, 0), ("I1", "IV", 90, 80, 1), ("I2", "IV", 50, 10, 0),
        ("I3", "IV", 20, 0, 0), ("I4", "IV", 10, 0, 0)]


def test_calibrate_sets_list_and_yellow_threshold_from_the_known_cases():
    records, selected = _records(ROWS), {c for c, *_, y in ROWS if y}
    fitted = cal.calibrate(records, selected, target_recall=0.8, yellow_recall=1.0)
    assert fitted["t_yellow"] <= fitted["t_recall"]
    p = {r["case_id"]: cal.relevance(r, fitted) for r in records}
    green = {c for c in selected if cal.ampel_for(p[c], fitted) == "gruen"}
    assert len(green) >= 0.8 * len(selected)
    assert all(cal.ampel_for(p[c], fitted) in ("gruen", "gelb") for c in selected)  # 100 % at least yellow
    # Without targets the old rule applies: F2 threshold times list_factor, yellow at half of it
    plain = cal.calibrate(records, selected)
    assert "t_recall" not in plain
    assert cal.list_threshold(plain, 0.5) == pytest.approx(plain["t"] * 0.5)
    assert cal.yellow_threshold(plain, 0.5) == pytest.approx(plain["t"] * 0.25)


def test_display_score_puts_the_yellow_bound_on_yellow():
    assert cal.display_score(0.0801, 0.08, t_yellow=0.05) >= 60.0
    assert 40.0 <= cal.display_score(0.0501, 0.08, t_yellow=0.05) < 60.0
    assert cal.display_score(0.05, 0.08, t_yellow=0.05) < 40.0
    assert cal.display_score(0.03, 0.08) == cal.display_score(0.03, 0.08, t_yellow=None)  # default: half


def test_config_target_and_null(tmp_path):
    config = load_config("configs/triage.json")
    assert (config.target_recall, config.yellow_recall) == (0.9, 1.0)
    data = json.loads(Path("configs/triage.json").read_text(encoding="utf-8"))
    data.update(target_recall=None, yellow_recall=None)
    path = tmp_path / "triage.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_config(path).target_recall is None


def test_from_scores_needs_the_score_files(tmp_path, capsys):
    with pytest.raises(FileNotFoundError):
        cli.main(["relevance", "--period", "2026-Q2", "--from-scores", "--tables-dir", str(tmp_path), "--no-export"])


def test_committed_q2_results_are_what_the_config_produces(tmp_path):
    """The Q2 list in results/ comes from the stored Apertus answers with configs/triage.json (no gateway)."""
    tables = tmp_path / "tables"
    tables.mkdir()
    for path in Path("results/tables").glob("relevance_scores_*.json"):
        shutil.copy(path, tables)
    out = tmp_path / "out"
    code = cli.main(["relevance", "--period", "2026-Q2", "--from-scores", "--tables-dir", str(tables),
                     "--export-dir", str(out), "--output", str(out / "triage_q2_2026.json")])
    assert code == 0
    for name in ("triage_q2_2026.json", "ranking_q2_2026.csv", "recall_q2_2026.txt"):
        assert (out / name).read_bytes() == Path("results", name).read_bytes(), name
    assert sorted(p.name for p in tables.glob("relevance_scores_*.json")) == sorted(
        p.name for p in Path("results/tables").glob("relevance_scores_*.json")
    )  # score files are read, not rewritten
    for path in tables.glob("relevance_scores_*.json"):
        assert path.read_bytes() == Path("results/tables", path.name).read_bytes()
