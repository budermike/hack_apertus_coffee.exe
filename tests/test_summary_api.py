"""Tests for the summary HTTP backend (no gateway; the summarizer is faked)."""

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hack_apertus_coffee_exe.summary import store
from hack_apertus_coffee_exe.summary.api import Backend, make_server

CRAWL_DIR = Path("src/hack_apertus_coffee_exe/triage/mock_crawl")
CASE = "8C_901/2026"
SAFE = "8C_901_2026"


def fake_result(judgement, similar_case_ids=()):
    return SimpleNamespace(
        case_id=judgement.case_id,
        title_de=f"BGer-Urteil {judgement.case_id}",
        title_fr=f"Arrêt du TF {judgement.case_id}",
        summary_de="Zusammenfassung DE",
        summary_fr="Résumé FR",
        facts={"form": "fall"},
        examples=["8C_177/2025"],
        model="apertus-v1-5-8b",
        prompt_version="v1",
        coherence_ok=True,
        attempts={},
        check=SimpleNamespace(to_dicts=lambda: []),
        text=lambda lang: "Zusammenfassung DE" if lang == "de" else "Résumé FR",
    )


@pytest.fixture
def summarizer():
    return SimpleNamespace(summarize=Mock(side_effect=fake_result))


@pytest.fixture
def backend(tmp_path, summarizer):
    return Backend(tmp_path / "s.db", CRAWL_DIR, lambda: summarizer)


def test_get_without_summary_is_404_and_never_generates(backend, summarizer):
    status, _ = backend.get_summary(SAFE)
    assert status == 404
    summarizer.summarize.assert_not_called()


def test_create_generates_once_and_returns_entry(backend, summarizer):
    status, entry = backend.create_summary(SAFE)
    assert status == 200
    assert entry["id"] == SAFE and entry["caseNumber"] == "8C 901/2026"
    assert entry["de"] == "Zusammenfassung DE" and entry["fr"] == "Résumé FR"
    assert entry["url"].startswith("https://search.bger.ch/")
    assert entry["status"] == {"de": "generated", "fr": "generated"}

    status, again = backend.create_summary(CASE)
    assert status == 200 and again == entry
    assert summarizer.summarize.call_count == 1


def test_restart_reads_from_db_without_llm(tmp_path, backend):
    backend.create_summary(SAFE)
    restarted = Backend(tmp_path / "s.db", CRAWL_DIR, lambda: pytest.fail("LLM must not be called"))
    status, entry = restarted.create_summary(SAFE)
    assert status == 200 and entry["de"] == "Zusammenfassung DE"


def test_unknown_judgement_is_404(backend):
    status, payload = backend.create_summary("8C_999_2026")
    assert status == 404 and "not found" in payload["error"]


def test_edit_then_approve(backend):
    backend.create_summary(SAFE)
    status, entry = backend.edit_summary(SAFE, {"de": "Vom Juristen", "fr": "Résumé FR"})
    assert status == 200
    assert entry["de"] == "Zusammenfassung DE"  # still shown until approved
    assert entry["pending"] == {"de": "Vom Juristen", "fr": None}  # unchanged FR is no edit

    status, entry = backend.approve_summary(SAFE, {})
    assert status == 200
    assert entry["de"] == "Vom Juristen" and entry["status"]["de"] == "approved"
    assert entry["pending"] == {"de": None, "fr": None}
    assert entry["editedAt"] is not None


def test_approve_without_edit_is_conflict(backend):
    backend.create_summary(SAFE)
    status, _ = backend.approve_summary(SAFE, {})
    assert status == 409


def test_edit_without_summary_is_404(backend):
    status, _ = backend.edit_summary(SAFE, {"de": "x"})
    assert status == 404


def test_search_knowledge_base(backend):
    backend.create_summary(SAFE)
    status, results = backend.search("résumé")
    assert status == 200 and [e["id"] for e in results] == [SAFE]
    assert backend.search("gibtsnicht")[1] == []


def test_http_roundtrip(backend):
    server = make_server(backend, "127.0.0.1", 0, CRAWL_DIR)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}/api"
    try:
        req = urllib.request.Request(f"{base}/judgements/{SAFE}/summary", method="POST")
        with urllib.request.urlopen(req) as res:
            assert json.loads(res.read())["de"] == "Zusammenfassung DE"

        body = json.dumps({"de": "Neu", "fr": "Résumé FR"}).encode()
        req = urllib.request.Request(
            f"{base}/judgements/{SAFE}/summary", data=body, method="PUT",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as res:
            assert json.loads(res.read())["pending"]["de"] == "Neu"

        with urllib.request.urlopen(f"{base}/knowledge-base?q=resume") as res:
            assert json.loads(res.read()) == []  # accents matter in the simple search

        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(urllib.request.Request(f"{base}/knowledge-base", method="DELETE"))
        assert err.value.code == 405
    finally:
        server.shutdown()
        server.server_close()
    with store.connect(backend.db_path) as conn:
        assert [s.status for s in store.get_history(conn, CASE, "de")] == ["generated", "edited"]
