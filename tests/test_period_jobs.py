"""Tests for dynamic period crawl/triage (#69, #91), single-case add (#70) and FR reason (#67)."""

from __future__ import annotations

import json
import threading
import time
from datetime import date, timedelta
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
        model = "apertus-v1-5-8b"

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
        crawl_cache_dir=tmp_path / "crawl_cache",
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


def test_partial_overlap_only_crawls_and_scores_uncovered_days(tmp_path):
    """Searching 2026-06-05..2026-07-01 when June is pre-scored must only process 2026-07-01."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    crawl_cache = tmp_path / "crawl_cache"
    (crawl_cache / "2026-07").mkdir(parents=True)
    july_record = {
        "id": "8C_601_2026",
        "case_id": "8C_601/2026",
        "case_ids": ["8C_601/2026"],
        "decision_date": "2026-07-01",
        "subject": "Unfallversicherung (Taggeld)",
        "language": "de",
        "url": "https://search.bger.ch/8C_601",
        "text": (
            "Urteil vom 1. Juli 2026\nBesetzung\nEinzelrichter A.\n"
            "Sachverhalt:\nA. Unfall.\nErwägungen:\n1. Die Suva verneinte den Anspruch.\n"
            "Demnach erkennt das Bundesgericht:\n1. Die Beschwerde wird abgewiesen."
        ),
    }
    (crawl_cache / "2026-07" / "2026-07-01_8C_601_2026.json").write_text(json.dumps(july_record), encoding="utf-8")

    (raw_dir / "2026-06").mkdir(parents=True)
    june_record = {**july_record, "id": "8C_100_2026", "case_id": "8C_100/2026", "decision_date": "2026-06-05"}
    (raw_dir / "2026-06" / "2026-06-05_8C_100_2026.json").write_text(json.dumps(june_record), encoding="utf-8")

    pre_scored = [
        {"case_id": "8C_100/2026", "judgement_date": "2026-06-05", "rank_score": 88.0, "metadata": {"subject": "UV"}},
        {"case_id": "8C_101/2026", "judgement_date": "2026-06-30", "rank_score": 45.0, "metadata": {"subject": "IV"}},
    ]
    triage_path = _write_triage(tmp_path / "triage.json", pre_scored)
    store_dir = tmp_path / "periods"
    jl = JudgementList(
        triage_path,
        config=TriageConfig(),
        crawl_dir=raw_dir,
        extra_crawl_dir=crawl_cache,
        extra_triage_dir=store_dir,
    )

    scored_keys = []

    class FakeClient:
        model = "apertus-v1-5-8b"

        def call(self, stage, key, system, user, schema, max_tokens):
            scored_keys.append((stage, key))
            if stage == "triage":
                return {
                    "a_rechtsfrage": "Frage?",
                    "b_kriterien": [{"a_id": "K5", "b_stelle": "E1", "c_zitat": "Die Suva verneinte den Anspruch"}],
                    "c_begruendung": "Suva beteiligt.",
                    "d_score": 78,
                }
            return {"a_grundsatz": 2, "b_nutzen_uv": 2, "c_korrektur": False, "d_merksatz": "Merksatz.", "e_score": 78}

    crawled_ranges = []

    def fake_ensure(since, until, raw_dir, summary_db_path, on_progress):
        crawled_ranges.append((since.isoformat(), until.isoformat()))
        on_progress(SimpleNamespace(stage="crawling", completed_days=1, total_days=1, message="1 Tag"))

    mgr = PeriodTriageManager(
        raw_dir=raw_dir,
        crawl_cache_dir=crawl_cache,
        store_dir=store_dir,
        judgements=jl,
        ensure_fn=fake_ensure,
        client_factory=FakeClient,
    )
    try:
        assert mgr.ensure_async("2026-06-10", "2026-06-25")["status"] == "done"
        assert crawled_ranges == []

        snap = mgr.ensure_async("2026-06-05", "2026-07-01")
        assert snap["status"] == "running"
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            cur = mgr.status("2026-06-05", "2026-07-01")
            if cur["status"] != "running":
                break
            time.sleep(0.02)
        assert cur["status"] == "done"
        assert crawled_ranges == [("2026-07-01", "2026-07-01")]
        assert len(scored_keys) == 2 and all("8C_601" in k for _, k in scored_keys)
        assert mgr.covers("2026-06-05", "2026-07-01") is True

        items = jl.items("2026-06-05", "2026-07-01")
        assert {i["caseNumber"] for i in items} == {"8C_100/2026", "8C_101/2026", "8C_601/2026"}
        july_item = next(i for i in items if i["caseNumber"] == "8C_601/2026")
        assert july_item["singleJudge"] is True
        assert july_item["outcome"] == "abgewiesen"
    finally:
        mgr.stop()


def test_superseded_job_cancels_and_stale_error_clears_when_superset_succeeds(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    store_dir = tmp_path / "periods"
    jl = JudgementList(_write_triage(tmp_path / "triage.json", []), config=TriageConfig(), crawl_dir=raw_dir, extra_triage_dir=store_dir)

    started_first = threading.Event()
    fail_once = {"flag": True}

    def scripted_ensure(since, until, raw_dir, summary_db_path, on_progress):
        if since.isoformat() == "2026-07-01" and until.isoformat() == "2026-07-10":
            started_first.set()
            for step in range(40):
                time.sleep(0.02)
                on_progress(SimpleNamespace(stage="crawling", completed_days=step, total_days=40, message="slow"))
        elif since.isoformat() == "2026-07-20" and fail_once["flag"]:
            fail_once["flag"] = False
            return SimpleNamespace(failed_days=["2026-07-20"])
        else:
            on_progress(SimpleNamespace(stage="crawling", completed_days=1, total_days=1, message="fast"))
        return SimpleNamespace(failed_days=[])

    mgr = PeriodTriageManager(
        raw_dir=raw_dir,
        crawl_cache_dir=tmp_path / "crawl_cache",
        store_dir=store_dir,
        judgements=jl,
        ensure_fn=scripted_ensure,
        client_factory=Mock(),
    )
    try:
        mgr.ensure_async("2026-07-01", "2026-07-10")
        assert started_first.wait(timeout=2.0)

        # Supersede slow job with 2026-07-15..2026-07-15
        t0 = time.monotonic()
        mgr.ensure_async("2026-07-15", "2026-07-15")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            cur = mgr.status("2026-07-15", "2026-07-15")
            if cur["status"] != "running":
                break
            time.sleep(0.02)
        assert cur["status"] == "done"
        assert (time.monotonic() - t0) < 0.6
        assert mgr.covers("2026-07-01", "2026-07-10") is False

        # Trigger error on 2026-07-20..2026-07-21, then succeed on superset 2026-07-19..2026-07-22
        mgr.ensure_async("2026-07-20", "2026-07-21")
        while mgr.status("2026-07-20", "2026-07-21")["status"] == "running":
            time.sleep(0.02)
        assert mgr.status("2026-07-20", "2026-07-21")["status"] == "error"

        mgr.ensure_async("2026-07-19", "2026-07-22")
        while mgr.status("2026-07-19", "2026-07-22")["status"] == "running":
            time.sleep(0.02)
        assert mgr.status("2026-07-20", "2026-07-21")["status"] == "done"

        # Re-searching a recently-cancelled range must start a fresh job rather than attaching to dying one
        started_first.clear()
        mgr.ensure_async("2026-07-01", "2026-07-10")
        assert started_first.wait(timeout=2.0)
        mgr.ensure_async("2026-07-25", "2026-07-25")  # supersedes 2026-07-01..2026-07-10
        # Wait until 2026-07-25 starts/finishes, OR re-search 2026-07-01..2026-07-10 immediately
        snap_again = mgr.ensure_async("2026-07-01", "2026-07-10")
        assert snap_again["status"] == "running"
        # Wait past the dying thread's sleep so any clobber would have occurred, then confirm healthy completion
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            cur = mgr.status("2026-07-01", "2026-07-10")
            if cur["status"] in ("done", "error"):
                break
            time.sleep(0.02)
        assert cur["status"] == "done"
        assert mgr.covers("2026-07-01", "2026-07-10") is True
    finally:
        mgr.stop()


def test_fast_crawl_only_fetches_social_chambers_and_skips_empty_recent_days(tmp_path, monkeypatch):
    import hack_apertus_coffee_exe.crawl.crawl as crawl_mod

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    crawl_cache = tmp_path / "crawl_cache"
    store_dir = tmp_path / "periods"
    cfg_file = tmp_path / "crawl.json"
    cfg_file.write_text(json.dumps({
        "source": "entscheidsuche",
        "from_date": "2026-04-01",
        "to_date": "2026-06-30",
        "out_dir": str(crawl_cache),
        "sources": {"entscheidsuche": {"base_url": "https://example.invalid", "delay_seconds": 0.0}},
    }), encoding="utf-8")

    jl = JudgementList(_write_triage(tmp_path / "triage.json", []), config=TriageConfig(), crawl_dir=raw_dir, extra_crawl_dir=crawl_cache, extra_triage_dir=store_dir)

    recent_empty = (date.today() - timedelta(days=2)).isoformat()
    old_empty = (date.today() - timedelta(days=45)).isoformat()
    fetched_ids = []

    def fake_list_day(_cfg, day):
        if day.isoformat() == recent_empty or day.isoformat() == old_empty:
            return [{"case_id": "1C_10/2026", "decision_date": day.isoformat(), "department": "I", "subject": "Bau", "language": "de", "metadata": {}}]
        return []

    def fake_fetch(_cfg, entry):
        fetched_ids.append(entry["case_id"])
        return "<html></html>", "https://example.invalid/doc"

    monkeypatch.setitem(crawl_mod.SOURCES, "entscheidsuche", (fake_list_day, fake_fetch))

    mgr = PeriodTriageManager(
        raw_dir=raw_dir,
        crawl_cache_dir=crawl_cache,
        store_dir=store_dir,
        judgements=jl,
        client_factory=Mock(),
        recent_empty_lag_days=21,
    )
    monkeypatch.setattr(mgr, "_crawl_config", lambda: crawl_mod.load_config(cfg_file, out_dir=str(crawl_cache)))
    try:
        # Recent empty day must NOT be poisoned in covered_days.json
        mgr.ensure_async(recent_empty, recent_empty)
        while mgr.status(recent_empty, recent_empty)["status"] == "running":
            time.sleep(0.02)
        assert fetched_ids == []  # 1C_10/2026 skipped
        assert mgr.covers(recent_empty, recent_empty) is False
        assert list(raw_dir.iterdir()) == []  # raw_dir strictly untouched

        # Old empty day (>21 days ago) IS marked covered so it is not re-listed forever
        mgr.ensure_async(old_empty, old_empty)
        while mgr.status(old_empty, old_empty)["status"] == "running":
            time.sleep(0.02)
        assert mgr.covers(old_empty, old_empty) is True

        # Cancelling mid-download in _crawl_missing_days_fast must NOT mark the day covered in _index.json
        mid_day = "2025-11-12"
        started_dl = threading.Event()
        release_dl = threading.Event()

        def list_with_social(_cfg, day):
            return [{"case_id": "8C_55/2025", "decision_date": day.isoformat(), "department": "IV", "subject": "UV", "language": "de", "metadata": {}}]

        def blocking_fetch(_cfg, entry):
            started_dl.set()
            release_dl.wait(timeout=2.0)
            return "<html></html>", "https://example.invalid/doc"

        monkeypatch.setitem(crawl_mod.SOURCES, "entscheidsuche", (list_with_social, blocking_fetch))
        cancel_ev = threading.Event()
        err_holder = []

        def run_fast():
            try:
                mgr._crawl_missing_days_fast([date.fromisoformat(mid_day)], lambda *_a: None, cancel_ev)
            except BaseException as exc:
                err_holder.append(exc)

        th = threading.Thread(target=run_fast)
        th.start()
        assert started_dl.wait(timeout=2.0)
        cancel_ev.set()
        release_dl.set()
        th.join(timeout=2.0)
        assert err_holder and type(err_holder[0]).__name__ == "_CancelledJob"
        idx_file = crawl_cache / "_index.json"
        if idx_file.exists():
            assert mid_day not in json.loads(idx_file.read_text(encoding="utf-8")).get("days", {})
    finally:
        mgr.stop()


def test_add_single_case_and_translate_fr_http(tmp_path):
    import urllib.error
    import urllib.request

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    crawl_cache = tmp_path / "crawl_cache"
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
        model = "apertus-v1-5-8b"

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
    jl = JudgementList(
        triage_path,
        config=TriageConfig(),
        crawl_dir=raw_dir,
        extra_crawl_dir=crawl_cache,
        extra_triage_dir=store_dir,
        fr_cache_path=fr_cache,
    )
    backend = Backend(tmp_path / "s.sqlite", raw_dir, Mock(), extra_crawl_dir=crawl_cache)
    mgr = PeriodTriageManager(
        raw_dir=raw_dir,
        crawl_cache_dir=crawl_cache,
        store_dir=store_dir,
        judgements=jl,
        backend=backend,
        fetch_case_fn=lambda cid, _cache_dir: record,
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
        assert list(raw_dir.iterdir()) == []
        assert mgr.covers("2026-07-01", "2026-07-31") is False
        assert mgr.covers("2026-08-01", "2026-08-31") is False

        fr_req = urllib.request.Request(f"{base}/judgements/8C_777_2026/reason-fr", method="POST", data=b"{}")
        with urllib.request.urlopen(fr_req) as res:
            fr_payload = json.load(res)
        assert fr_payload["explanation"]["fr"] == "La Suva est directement concernée."
        assert fr_payload["question"]["fr"].startswith("Existe-t-il")

        with urllib.request.urlopen(f"{base}/judgements?from=2026-08-01&to=2026-08-31") as res:
            listed = json.load(res)
        assert listed[0]["explanation"]["fr"] == "La Suva est directement concernée."

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
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    store_dir = tmp_path / "periods"
    jl = JudgementList(_write_triage(tmp_path / "triage.json", []), config=TriageConfig(), crawl_dir=raw_dir, extra_triage_dir=store_dir)

    today = date.today().isoformat()
    mgr = PeriodTriageManager(
        raw_dir=raw_dir,
        crawl_cache_dir=tmp_path / "crawl_cache",
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
