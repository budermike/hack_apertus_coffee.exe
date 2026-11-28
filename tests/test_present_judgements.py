"""Judgement list for the UI (present/judgements.py) and its routes in summary/api.py. No network."""

import json
import os
import threading
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

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
        "caseNumber": "8C_221/2025",
        "date": "2025-11-10",
        "url": "https://search.bger.ch/x",
        "lang": "fr",
        "area": "UV",
        "subject": {"de": subject, "fr": subject},
        "explanation": {"de": "Integritätsentschädigung, UVG.", "fr": "Integritätsentschädigung, UVG."},
        "score": 55.0,
        "adjustment": 0,
        "summarized": True,
        "signals": [],
        "topics": [],
        "similarCases": [{"caseNumber": "8C_720/2024", "subject": "Arrêt du TF 8C_720/2024", "similarity": 0.4457}],
        "ampel": "gelb",
        "rank": 2,
        "outcome": "teilweise",
    }
    bare = to_ui_item(ROWS[2])
    assert bare["subject"] == {"de": "5A_1/2026", "fr": "5A_1/2026"} and bare["area"] is None
    assert "outcome" not in bare and bare["similarCases"] == []
    assert "court" not in bare


def test_court_signals_and_topics_are_passed_on():
    row = {
        "case_id": "8C_7/2025", "rank_score": 91.0, "keyword_hits": ["Suva", "Chamber:8C"], "key_topics": ["Rückfall"],
        "metadata": {"subject": "Unfallversicherung", "department": "IV. öffentlich-rechtliche Abteilung"},
    }
    item = to_ui_item(row)
    assert item["signals"] == ["Suva", "Chamber:8C"] and item["topics"] == ["Rückfall"]
    assert item["court"] == {"de": "IV. öffentlich-rechtliche Abteilung", "fr": "IV. öffentlich-rechtliche Abteilung"}


def test_question_rule_and_criteria_come_from_the_relevance_scoring():
    # shape of metadata.relevance as written by `triage.cli relevance` (#62)
    relevance = {
        "area": "UV", "outcome": "teilweise", "p": 0.7492, "threshold": 0.0445, "principle": 2, "uv_use": 2,
        "deep_score": 85,
        "question": " Wie ist Art. 32 Abs. 1 UVV bei einer nicht versicherten Nebentätigkeit anzuwenden? ",
        "rule": "Angerechnet wird nur der Teil der IV-Rente, der die unfallbedingte Invalidität abdeckt.",
        "criteria": [
            {"a_id": "K1", "b_stelle": "E4.4.1 bis E4.4.4", "beleg_status": "exakt",
             "c_zitat": "«Il est établi qu'au moment de son accident en 2008, l'intéressée exerçait une activité»"},
            {"a_id": "N2", "b_stelle": "E5.3", "beleg_status": "ungefähr",
             "c_zitat": "Die Vorinstanz hat den Sachverhalt willkürfrei festgestellt"},
        ],
    }
    row = {"case_id": "8C_137/2025", "rank_score": 89.5, "key_topics": ["K1", "N2"],
           "metadata": {"subject": "Assurance-accidents", "relevance": relevance}}
    item = to_ui_item(row)
    question = "Wie ist Art. 32 Abs. 1 UVV bei einer nicht versicherten Nebentätigkeit anzuwenden?"
    assert item["question"] == {"de": question, "fr": question}
    assert item["rule"] == {"de": relevance["rule"], "fr": relevance["rule"]}
    assert item["criteria"] == [
        # the UI puts «…» around the quote itself
        {"id": "K1", "at": "E4.4.1 bis E4.4.4", "status": "exakt",
         "quote": "Il est établi qu'au moment de son accident en 2008, l'intéressée exerçait une activité"},
        {"id": "N2", "at": "E5.3", "status": "ungefähr",
         "quote": "Die Vorinstanz hat den Sachverhalt willkürfrei festgestellt"},
    ]
    assert item["topics"] == ["K1", "N2"] and item["score"] == 89.5
    assert item["principle"] == 2 and item["uvUse"] == 2


def test_rule_is_not_repeated_in_the_explanation():
    # the relevance scoring writes "<reason> Merksatz: <rule>" into relevance_reason
    rule = "Angerechnet wird nur der unfallbedingte Teil der IV-Rente."
    row = {"case_id": "8C_137/2025", "relevance_reason": f"Materielle Frage der Unfallversicherung. Merksatz: {rule}",
           "metadata": {"relevance": {"rule": rule}}}
    item = to_ui_item(row)
    assert item["explanation"] == {"de": "Materielle Frage der Unfallversicherung.", "fr": "Materielle Frage der Unfallversicherung."}
    assert item["rule"] == {"de": rule, "fr": rule}
    # a reason that only mentions a rule, or has no rule in the details, stays as it is
    for reason, relevance in (
        ("Kein Bezug zur Suva. Merksatz: etwas anderes", {"rule": rule}),
        (f"Materielle Frage. Merksatz: {rule}", {"rule": None}),
        (f"Materielle Frage. Merksatz: {rule}", None),
    ):
        kept = to_ui_item({"case_id": "8C_1/2026", "relevance_reason": reason, "metadata": {"relevance": relevance}})
        assert kept["explanation"]["de"] == reason


def test_judgements_without_relevance_details_keep_their_shape():
    # `triage.cli score` writes no metadata.relevance; the relevance scoring leaves question,
    # rule and criteria empty for judgements it did not send to Apertus
    not_scored = {"area": "ZIV", "outcome": "unklar", "question": "", "rule": None, "criteria": []}
    not_a_list = {"criteria": 7, "question": 3, "rule": ["x"]}
    for meta in ({}, {"relevance": None}, {"relevance": "kaputt"}, {"relevance": not_scored}, {"relevance": not_a_list}):
        item = to_ui_item({"case_id": "5A_1/2026", "metadata": meta})
        assert "question" not in item and "rule" not in item and "criteria" not in item


def test_malformed_criteria_are_skipped_and_quote_marks_inside_a_quote_stay():
    relevance = {"criteria": [
        "kein Objekt",
        {"a_id": "K1"},                                    # no quote
        {"c_zitat": "Zitat ohne Kriterium"},               # no criterion
        {"a_id": "K2", "c_zitat": "  "},                   # empty quote
        {"a_id": "K4", "c_zitat": "«»"},                   # empty quote in marks
        {"a_id": "K3", "c_zitat": "«Unfall» im Sinne von «Art. 4 ATSG»", "b_stelle": None},
    ]}
    item = to_ui_item({"case_id": "8C_2/2026", "metadata": {"relevance": relevance}})
    assert item["criteria"] == [
        {"id": "K3", "at": "", "quote": "«Unfall» im Sinne von «Art. 4 ATSG»", "status": None},
    ]
    assert "question" not in item and "rule" not in item


def test_area_code_takes_the_first_area_named():
    assert area_code("Assurance-accidents (rente d'invalidité; atteinte à l'intégrité)") == "UV"
    assert area_code("Invalidenversicherung (Invalidenrente)") == "IV"
    assert area_code("Assicurazione contro gli infortuni") == "UV"
    assert area_code("Prestations complémentaires à l'AVS/AI") == "EL"
    assert area_code("Prestazione complementare all'AVS/AI (restituzione)") == "EL"
    assert area_code("Assicurazione sociale cantonale (riduzione dei premi)") == "SOZ"
    assert area_code("Assicurazione per la vecchiaia e per i superstiti (obbligo contributivo)") == "AHV"
    assert area_code("Assegno familiare") == "FZ"
    assert area_code("Assicurazione contro le malattie") == "KV"
    assert area_code("Ehescheidung") is None


def test_area_falls_back_to_relevance_area_only_for_known_codes():
    assert to_ui_item({"case_id": "8C_1/2026", "metadata": {"relevance": {"area": "UV"}}})["area"] == "UV"
    for invalid in ("andere", "ZIV", None, 123):
        assert to_ui_item({"case_id": "8C_2/2026", "metadata": {"relevance": {"area": invalid}}})["area"] is None
    item = to_ui_item({
        "case_id": "8C_3/2026",
        "metadata": {"subject": "Unfallversicherung", "relevance": {"area": "IV"}},
    })
    assert item["area"] == "UV"


def test_outcome_from_the_crawl_folder(tmp_path):
    mock = Path("src/hack_apertus_coffee_exe/triage/mock_crawl")
    rows = [{"case_id": "8C_901/2026", "final_score": 80.0, "judgement_date": "2026-10-14", "metadata": {}}]
    judgements = JudgementList(_write(tmp_path, rows), config=TriageConfig(), crawl_dir=mock)
    assert judgements.items()[0]["outcome"] == "teilweise"


def test_missing_or_empty_crawl_dir_leaves_outcomes_empty(tmp_path):
    empty_dir = tmp_path / "empty_crawl"
    empty_dir.mkdir()
    index_only_dir = tmp_path / "index_only_crawl"
    index_only_dir.mkdir()
    (index_only_dir / "_index.json").write_text("{}", encoding="utf-8")
    for crawl_dir in (tmp_path / "missing", empty_dir, index_only_dir):
        judgements = JudgementList(_write(tmp_path), config=TriageConfig(), crawl_dir=crawl_dir)
        first = judgements.items()
        second = judgements.items()
        assert len(first) == 3 and len(second) == 3
        assert all("outcome" not in item for item in first)
        assert all("outcome" not in item for item in second)


def test_outcomes_are_loaded_once_under_concurrent_access(tmp_path, monkeypatch):
    import hack_apertus_coffee_exe.present.judgements as judgements_mod

    crawl_dir = tmp_path / "crawl"
    crawl_dir.mkdir()
    (crawl_dir / "8C_7_2025.json").write_text("{}", encoding="utf-8")
    calls = 0

    def fake_load_judgements(_dir):
        nonlocal calls
        calls += 1
        time.sleep(0.02)
        return [SimpleNamespace(case_id="8C_7/2025", text="Demnach erkennt das Bundesgericht:\n1. Die Beschwerde wird abgewiesen.")]

    monkeypatch.setattr(judgements_mod, "load_judgements", fake_load_judgements)
    judgements = JudgementList(_write(tmp_path), config=TriageConfig(), crawl_dir=crawl_dir)

    barrier = threading.Barrier(6)
    results: list[dict[str, str]] = []
    errors: list[BaseException] = []

    def worker():
        try:
            barrier.wait(timeout=5)
            results.append(judgements.outcomes())
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert calls == 1
    assert len(results) == 6
    assert all(res == {"8C_7_2025": "abgewiesen"} for res in results)


def test_rule_whitespace_and_invalid_triage_json_and_config_fallback(tmp_path):
    rule = "Angerechnet wird nur der unfallbedingte Teil der IV-Rente."
    item = to_ui_item({
        "case_id": "8C_137/2025",
        "relevance_reason": f"Materielle Frage. Merksatz: {rule}   ",
        "metadata": {"relevance": {"rule": f"  {rule}\n"}},
    })
    assert item["explanation"] == {"de": "Materielle Frage.", "fr": "Materielle Frage."}
    assert item["rule"] == {"de": rule, "fr": rule}

    bad_path = tmp_path / "bad.json"
    bad_path.write_text('{"not": "a list"}', encoding="utf-8")
    with pytest.raises(ValueError, match="expected a JSON list"):
        JudgementList(bad_path, config=TriageConfig()).items()

    status, body = config_response(None)
    assert status == 200
    assert set(body["thresholds"]) == {"high", "medium"}
    assert body["thresholds"]["high"] >= body["thresholds"]["medium"]


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
    from contextlib import closing
    from hack_apertus_coffee_exe.summary import store

    db_path = tmp_path / "s.sqlite"
    judgements = JudgementList(
        _write(tmp_path),
        config=TriageConfig(green_threshold=60.0, yellow_threshold=40.0),
        feedback_map=lambda: _latest_feedback(db_path),
        selected_ids={"8C_720/2024"},
    )
    backend = Backend(db_path, tmp_path, Mock())
    server = make_server(backend, "127.0.0.1", 0, tmp_path, judgements)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/api"
    try:
        with urllib.request.urlopen(f"{base}/judgements?from=2025-01-01&to=2025-12-31&lang=de") as resp:
            items = json.load(resp)
        assert [i["caseNumber"] for i in items] == ["8C_7/2025", "8C_221/2025"]
        assert items[1]["similarCases"][0]["suvaSelected"] is True
        with urllib.request.urlopen(f"{base}/config") as resp:
            assert json.load(resp) == {"thresholds": {"high": 60.0, "medium": 40.0}}
        request = urllib.request.Request(
            f"{base}/judgements/8C_7_2025/feedback", method="POST",
            data=json.dumps({"adjustment": -20, "direction": -1, "level": "low", "decision": "low"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as resp:
            payload = json.load(resp)
            assert resp.status == 200 and payload["stored"] is True
        with urllib.request.urlopen(f"{base}/judgements?from=2025-01-01&to=2025-12-31&lang=de") as resp:
            after = json.load(resp)
        top = next(i for i in after if i["id"] == "8C_7_2025")
        assert top["adjustment"] == -20.0 and top["userLevel"] == "low"
    finally:
        server.shutdown()
        server.server_close()
    with closing(store.connect(db_path)) as conn:
        assert store.feedback_examples(conn) == {"8C_7/2025": False}


def _latest_feedback(db_path: Path):
    from contextlib import closing
    from hack_apertus_coffee_exe.summary import store

    with closing(store.connect(db_path)) as conn:
        return store.get_latest_feedback(conn)


def test_single_judge_is_detected_from_crawl_text(tmp_path):
    from hack_apertus_coffee_exe.present.judgements import _is_single_judge

    mock = Path("src/hack_apertus_coffee_exe/triage/mock_crawl")
    rows = [{"case_id": "8C_901/2026", "final_score": 80.0, "judgement_date": "2026-10-14",
             "metadata": {"similar_cases": []}}]
    judgements = JudgementList(_write(tmp_path, rows), config=TriageConfig(), crawl_dir=mock)
    item = judgements.items()[0]
    assert isinstance(item["singleJudge"], bool)
    assert _is_single_judge("Urteil\nBesetzung\nEinzelrichter Müller\nSachverhalt:\n...") is True
    # lower-court mention in facts must NOT mark a three-judge panel as single-judge
    assert _is_single_judge("Urteil\nBesetzung\nBundesrichter A, B, C\nSachverhalt:\nGegen Einzelrichter...\n") is False


def test_invalid_feedback_payload_returns_400(tmp_path):
    backend = Backend(tmp_path / "s.sqlite", tmp_path, Mock())
    server = make_server(backend, "127.0.0.1", 0, tmp_path)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/api"
    try:
        for bad in ({"adjustment": [1]}, {"direction": [1]}, {"adjustment": True}, {"decision": "bogus"}):
            req = urllib.request.Request(
                f"{base}/judgements/8C_7_2025/feedback", method="POST",
                data=json.dumps(bad).encode(), headers={"Content-Type": "application/json"},
            )
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(req)
            assert exc.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
