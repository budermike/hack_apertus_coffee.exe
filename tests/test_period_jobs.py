"""Tests for dynamic period crawl/triage (#69, #91), single-case add (#70) and FR reason (#67)."""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hack_apertus_coffee_exe.crawl.crawl import normalize_case_id
from hack_apertus_coffee_exe.present.judgements import JudgementList
from hack_apertus_coffee_exe.present.period_jobs import PeriodTriageManager
from hack_apertus_coffee_exe.summary.api import Backend, make_server
from hack_apertus_coffee_exe.triage.config import TriageConfig


def _write_triage(path, rows):
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return path


def test_normalize_case_id():
    assert normalize_case_id("8C 123/2026") == "8C_123/2026"
    assert normalize_case_id("8C_123_2026") == "8C_123/2026"
    with pytest.raises(ValueError):
        normalize_case_id("not-a-case")


def test_uncovered_period_crawls_and_triages_in_background(tmp_path):
    raw_dir = tmp_path / "raw"
    (raw_dir / "2026-07").mkdir(parents=True)
    record = {
        "id": "8C_500_2026",
        "case_id": "8C_500/2026",
        "case_ids": ["8C_500/2026"],
        "decision_date": "2026-07-02",
        "subject": "Unfallversicherung (Kausalzusammenhang)",
        "language": "de",
        "url": "https://search.bger.ch/x",
        "text": (
            "Urteil vom 2. Juli 2026\nIV. öffentlich-rechtliche Abteilung\n"
            "Sachverhalt:\nA. Sturz im Betrieb.\n"
            "Erwägungen:\n1. Die Suva lehnte Leistungen ab.\n"
            "Demnach erkennt das Bundesgericht:\n1. Die Beschwerde wird abgewiesen."
        ),
    }
    (raw_dir / "2026-07" / "2026-07-02_8C_500_2026.json").write_text(json.dumps(record), encoding="utf-8")

    triage_path = _write_triage(tmp_path / "triage.json", [])
    store_dir = tmp_path / "periods"
    jl = JudgementList(triage_path, config=TriageConfig(), crawl_dir=raw_dir, extra_triage_dir=store_dir)

    class FakeClient:
        model = "apertus-v1-5-70b"

        def call(self, stage, key, system, user, schema, max_tokens):
            if stage == "triage":
                return {
                    "a_rechtsfrage": "Liegt ein Kausalzusammenhang vor?",
                    "b_kriterien": [{"a_id": "K1", "b_stelle": "E1", "c_zitat": "Die Suva lehnte Leistungen ab"}],
                    "c_begruendung": "Suva ist Partei im UVG-Fall.",
                    "d_score": 82,
                }
            return {"a_grundsatz": 2, "b_nutzen_uv": 3, "c_korrektur": False, "d_merksatz": "Merke UVG.", "e_score": 80}

    ensure_calls = []

    def fake_ensure(since, until, raw_dir, summary_db_path, on_progress):
        ensure_calls.append((since.isoformat(), until.isoformat()))
        on_progress(SimpleNamespace(stage="crawling", completed_days=1, total_days=1, message="OK"))

    mgr = PeriodTriageManager(
        raw_dir=raw_dir,
        store_dir=store_dir,
        judgements=jl,
        ensure_fn=fake_ensure,
        client_factory=FakeClient,
    )
    try:
        assert mgr.covers("2026-07-01", "2026-07-05") is False
        snap = mgr.ensure_async("2026-07-01", "2026-07-05")
        assert snap["status"] == "running"

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            cur = mgr.status("2026-07-01", "2026-07-05")
            if cur["status"] != "running":
                break
            time.sleep(0.02)

        assert cur["status"] == "done"
        assert ensure_calls == [("2026-07-01", "2026-07-05")]
        assert mgr.covers("2026-07-01", "2026-07-05") is True
        items = jl.items("2026-07-01", "2026-07-05")
        assert len(items) == 1 and items[0]["caseNumber"] == "8C_500/2026"
        assert items[0]["outcome"] == "abgewiesen"
    finally:
        mgr.stop()


def test_add_single_case_and_translate_fr_http(tmp_path):
    import urllib.error
    import urllib.request

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    store_dir = tmp_path / "periods"
    fr_cache = tmp_path / "fr_cache.json"
    triage_path = _write_triage(tmp_path / "triage.json", [])

    record = {
        "id": "8C_777_2026",
        "case_id": "8C_777/2026",
        "case_ids": ["8C_777/2026"],
        "decision_date": "2026-08-10",
        "subject": "Unfallversicherung",
        "language": "de",
        "url": "https://search.bger.ch/8C_777",
        "text": (
            "Urteil vom 10. August 2026\nIV. öffentlich-rechtliche Abteilung\n"
            "Sachverhalt:\nA. Unfall.\nErwägungen:\n1. Suva zahlte Taggeld.\n"
            "Demnach erkennt das Bundesgericht:\n1. Die Beschwerde wird abgewiesen."
        ),
    }

    class FakeClient:
        model = "apertus-v1-5-70b"

        def __init__(self):
            self.stages = []

        def call(self, stage, key, system, user, schema, max_tokens):
            self.stages.append(stage)
            if stage == "triage":
                return {
                    "a_rechtsfrage": "Besteht Anspruch auf Taggeld?",
                    "b_kriterien": [{"a_id": "K5", "b_stelle": "E1", "c_zitat": "Suva zahlte Taggeld"}],
                    "c_begruendung": "Suva ist direkt beteiligt.",
                    "d_score": 85,
                }
            if stage == "deep":
                return {"a_grundsatz": 2, "b_nutzen_uv": 3, "c_korrektur": False, "d_merksatz": "Taggeld nach UVG.", "e_score": 82}
            return {
                "reason_fr": "La Suva est directement concernée.",
                "question_fr": "Existe-t-il un droit à l'indemnité journalière ?",
                "rule_fr": "Indemnité journalière selon la LAA.",
            }

    client = FakeClient()
    jl = JudgementList(triage_path, config=TriageConfig(), crawl_dir=raw_dir, extra_triage_dir=store_dir, fr_cache_path=fr_cache)
    backend = Backend(tmp_path / "s.sqlite", raw_dir, Mock())
    mgr = PeriodTriageManager(
        raw_dir=raw_dir,
        store_dir=store_dir,
        judgements=jl,
        backend=backend,
        fetch_case_fn=lambda cid, _raw: record,
        client_factory=lambda: client,
    )
    server = make_server(backend, "127.0.0.1", 0, tmp_path, jl, periods=mgr)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/api"
    try:
        req = urllib.request.Request(
            f"{base}/judgements/add",
            method="POST",
            data=json.dumps({"caseNumber": "8C 777/2026", "summarize": False}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as res:
            added = json.load(res)
        assert res.status == 200 and added["caseNumber"] == "8C_777/2026"
        assert added["outcome"] == "abgewiesen"
        # Adding a single case on 2026-08-10 must NOT mark July or August as covered periods
        assert mgr.covers("2026-07-01", "2026-07-31") is False
        assert mgr.covers("2026-08-01", "2026-08-31") is False

        fr_req = urllib.request.Request(f"{base}/judgements/8C_777_2026/reason-fr", method="POST", data=b"{}")
        with urllib.request.urlopen(fr_req) as res:
            fr_payload = json.load(res)
        assert fr_payload["explanation"]["fr"] == "La Suva est directement concernée."
        assert fr_payload["question"]["fr"].startswith("Existe-t-il")

        # Subsequent GET /api/judgements serves cached French automatically (#67)
        with urllib.request.urlopen(f"{base}/judgements?from=2026-08-01&to=2026-08-31") as res:
            listed = json.load(res)
        assert listed[0]["explanation"]["fr"] == "La Suva est directement concernée."

        # Invalid summarize type yields 400
        bad = urllib.request.Request(
            f"{base}/judgements/add",
            method="POST",
            data=json.dumps({"caseNumber": "8C_1/2026", "summarize": "yes"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(bad)
        assert exc.value.code == 400
    finally:
        mgr.stop()
        backend.queue.stop()
        server.shutdown()
        server.server_close()


def test_failed_crawl_days_and_today_are_never_marked_covered(tmp_path):
    from datetime import date

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    store_dir = tmp_path / "periods"
    jl = JudgementList(_write_triage(tmp_path / "triage.json", []), config=TriageConfig(), crawl_dir=raw_dir, extra_triage_dir=store_dir)

    today = date.today().isoformat()
    mgr = PeriodTriageManager(
        raw_dir=raw_dir,
        store_dir=store_dir,
        judgements=jl,
        ensure_fn=lambda *a, **kw: SimpleNamespace(failed_days=["2026-07-03"]),
    )
    try:
        with pytest.raises(ValueError, match="today"):
            mgr.ensure_async(today, today)

        snap = mgr.ensure_async("2026-07-01", "2026-07-03")
        assert snap["status"] == "running"
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            cur = mgr.status("2026-07-01", "2026-07-03")
            if cur["status"] != "running":
                break
            time.sleep(0.02)
        assert cur["status"] == "error"
        assert mgr.covers("2026-07-01", "2026-07-03") is False
    finally:
        mgr.stop()
