"""Judgement list for the UI (present/judgements.py) and its routes in summary/api.py. No network."""

import json
import os
import threading
import urllib.request
from pathlib import Path
from unittest.mock import Mock

from hack_apertus_coffee_exe.present.judgements import (
    JudgementList,
    area_code,
    config_response,
    list_response,
    record_feedback,
    to_ui_item,
)
from hack_apertus_coffee_exe.summary.api import Backend, make_server
from hack_apertus_coffee_exe.triage.config import TriageConfig

ROWS = [
    {
        "case_id": "8C_221/2025", "rank": 2, "ampel": "gelb", "final_score": 55.04, "rank_score": 55.04,
        "judgement_date": "2025-11-10", "relevance_reason": "Integritätsentschädigung, UVG.", "language": "fr",
        "similar_cases": [{"case_id": "8C_720/2024", "title": "Arrêt du TF 8C_720/2024", "similarity": 0.4457}],
        "metadata": {"subject": "Assurance-accidents (indemnité pour atteinte à l'intégrité)",
                     "url": "https://search.bger.ch/x"},
    },
    {
        "case_id": "8C_7/2025", "rank": 1, "ampel": "gruen", "final_score": 91.0, "rank_score": 91.0,
        "judgement_date": "2025-09-25", "relevance_reason": "Suva ist Partei.",
        "metadata": {"subject": "Unfallversicherung", "url": "https://search.bger.ch/y"},
    },
    {
        "case_id": "5A_1/2026", "rank": 3, "ampel": "rot", "final_score": 3.0,
        "judgement_date": "2026-01-15", "relevance_reason": "Familienrecht.", "metadata": {},
    },
]


def _write(tmp_path: Path, rows=ROWS) -> Path:
    path = tmp_path / "triage.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return path


def test_to_ui_item_matches_the_ui_contract():
    item = to_ui_item(ROWS[0], summarized=True, outcome="teilweise")
    subject = ROWS[0]["metadata"]["subject"]
    assert item == {
        "id": "8C_221_2025",
        "caseNumber": "8C 221/2025",
        "date": "2025-11-10",
        "url": "https://search.bger.ch/x",
        "lang": "fr",
        "area": "UV",
        "subject": {"de": subject, "fr": subject},
        "explanation": {"de": "Integritätsentschädigung, UVG.", "fr": "Integritätsentschädigung, UVG."},
        "score": 55.0,
        "adjustment": 0,
        "summarized": True,
        "similarCases": [{"caseNumber": "8C 720/2024", "subject": "Arrêt du TF 8C_720/2024", "similarity": 0.4457}],
        "ampel": "gelb",
        "rank": 2,
        "outcome": "teilweise",
    }
    bare = to_ui_item(ROWS[2])
    assert bare["subject"] == {"de": "5A_1/2026", "fr": "5A_1/2026"} and bare["area"] is None
    assert "outcome" not in bare and bare["similarCases"] == []


def test_area_code_takes_the_first_area_named():
    assert area_code("Assurance-accidents (rente d'invalidité; atteinte à l'intégrité)") == "UV"
    assert area_code("Invalidenversicherung (Invalidenrente)") == "IV"
    assert area_code("Assicurazione contro gli infortuni") == "UV"
    assert area_code("Prestations complémentaires à l'AVS/AI") == "EL"
    assert area_code("Ehescheidung") is None


def test_outcome_from_the_crawl_folder(tmp_path):
    mock = Path("src/hack_apertus_coffee_exe/triage/mock_crawl")
    rows = [{"case_id": "8C_901/2026", "final_score": 80.0, "judgement_date": "2026-10-14", "metadata": {}}]
    judgements = JudgementList(_write(tmp_path, rows), config=TriageConfig(), crawl_dir=mock)
    assert judgements.items()[0]["outcome"] == "teilweise"


def test_items_filter_by_date_sort_by_score_and_mark_summarized(tmp_path):
    judgements = JudgementList(_write(tmp_path), config=TriageConfig(), summarized_ids=lambda: ["8C_7/2025"])
    items = judgements.items("2025-09-01", "2025-12-31")
    assert [i["id"] for i in items] == ["8C_7_2025", "8C_221_2025"]
    assert [i["summarized"] for i in items] == [True, False]
    assert len(judgements.items()) == 3


def test_file_is_reloaded_when_triage_is_rerun(tmp_path):
    path = _write(tmp_path)
    judgements = JudgementList(path, config=TriageConfig())
    assert len(judgements.items()) == 3
    path.write_text(json.dumps(ROWS[:1]), encoding="utf-8")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 10))
    assert len(judgements.items()) == 1


def test_responses_without_triage_file_and_thresholds():
    status, body = list_response(None, {})
    assert status == 503 and "--triage" in body["error"]
    cfg = TriageConfig(green_threshold=70.0, yellow_threshold=40.0)
    assert record_feedback("8C_7_2025", {"level": "high"})["stored"] is False
    assert config_response(Mock(thresholds=lambda: {"high": cfg.green_threshold, "medium": cfg.yellow_threshold})) == (
        200, {"thresholds": {"high": 70.0, "medium": 40.0}}
    )


def test_routes_in_summary_api(tmp_path):
    judgements = JudgementList(_write(tmp_path), config=TriageConfig(green_threshold=60.0, yellow_threshold=40.0))
    backend = Backend(tmp_path / "s.sqlite", tmp_path, Mock())
    server = make_server(backend, "127.0.0.1", 0, tmp_path, judgements)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/api"
    try:
        with urllib.request.urlopen(f"{base}/judgements?from=2025-01-01&to=2025-12-31&lang=de") as resp:
            items = json.load(resp)
        assert [i["caseNumber"] for i in items] == ["8C 7/2025", "8C 221/2025"]
        with urllib.request.urlopen(f"{base}/config") as resp:
            assert json.load(resp) == {"thresholds": {"high": 60.0, "medium": 40.0}}
        request = urllib.request.Request(
            f"{base}/judgements/8C_7_2025/feedback", method="POST",
            data=json.dumps({"adjustment": -20, "direction": -1, "level": "medium"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as resp:
            assert resp.status == 200 and json.load(resp)["stored"] is False
    finally:
        server.shutdown()
        server.server_close()
