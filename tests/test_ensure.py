"""Offline unit tests for ensure_period (#35)."""

from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
import threading
import time

import httpx
import pytest

from hack_apertus_coffee_exe.crawl import PeriodStatus, ProgressEvent, crawl, ensure, ensure_period
from hack_apertus_coffee_exe.crawl.vectordb import VectorDB
from hack_apertus_coffee_exe.triage.embeddings import TfidfEmbedder


def _sample_html(case_id: str, subject: str, body: str) -> str:
    return (
        '<html><body><div class="content">'
        f'<div class="para">{case_id}</div>'
        '<div class="para">Besetzung: Bundesrichter X</div>'
        '<div class="para">Gegenstand</div>'
        f'<div class="para">{subject},</div>'
        '<div class="para">Erwägungen</div>'
        f'<div class="para">{body}</div>'
        "</div></body></html>"
    )


def _tfidf_embedder() -> TfidfEmbedder:
    emb = TfidfEmbedder()
    emb.fit(
        [
            "Unfallversicherung UVG Suva Kausalzusammenhang Sturz Baustelle",
            "Invalidenversicherung Rente Arbeitseinkommen",
        ]
    )
    return emb


def _install_fake_source(
    monkeypatch,
    cases_by_day: dict[str, list[dict[str, str]]],
    *,
    fail_list_days: set[str] | None = None,
    blocked_days: set[str] | None = None,
    list_delay: float = 0.0,
) -> dict[str, list]:
    """Mock crawl.SOURCES['entscheidsuche'] in memory (zero HTTP requests)."""
    calls: dict[str, list] = {"list": [], "fetch": []}
    fail_list = set(fail_list_days or ())
    blocked = set(blocked_days or ())

    def fake_list_day(_config, day):
        iso = day.isoformat()
        calls["list"].append(iso)
        if list_delay > 0:
            time.sleep(list_delay)
        if iso in blocked:
            raise crawl.Blocked("Incapsula incident")
        if iso in fail_list:
            raise httpx.HTTPError("simulated network error")
        return [
            {
                "case_id": item["case_id"],
                "decision_date": iso,
                "department": "IV. öffentlich-rechtliche Abteilung",
                "subject": item["subject"],
                "language": "de",
                "metadata": {"id": item["case_id"]},
            }
            for item in cases_by_day.get(iso, [])
        ]

    def fake_fetch(_config, entry):
        iso = entry["decision_date"]
        calls["fetch"].append((iso, entry["case_id"]))
        for item in cases_by_day.get(iso, []):
            if item["case_id"] == entry["case_id"]:
                html = _sample_html(item["case_id"], item["subject"], item["body"])
                return html, f"https://example.invalid/{item['case_id']}"
        raise ValueError(f"Unknown case {entry['case_id']}")

    monkeypatch.setitem(crawl.SOURCES, "entscheidsuche", (fake_list_day, fake_fetch))
    return calls


def test_ensure_period_first_crawl_then_cached_fast_path(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    db_dir = tmp_path / "vectordb"
    emb = _tfidf_embedder()
    calls = _install_fake_source(
        monkeypatch,
        {
            "2026-07-01": [
                {"case_id": "8C_1/2026", "subject": "Unfallversicherung", "body": "Suva UVG Unfall auf Baustelle."},
            ],
            "2026-07-02": [],
        },
    )

    first = ensure_period(
        "2026-07-01",
        "2026-07-02",
        source="entscheidsuche",
        raw_dir=raw_dir,
        db_dir=db_dir,
        embedder=emb,
        today=date(2026, 7, 10),
    )
    assert isinstance(first, PeriodStatus)
    assert first.complete is True
    assert first.new_judgements == 1
    assert first.ingested_judgements == 1
    assert first.days_crawled == 2
    assert first.days_already_covered == 0
    assert first.failed_days == []
    assert calls["list"] == ["2026-07-01", "2026-07-02"]
    assert calls["fetch"] == [("2026-07-01", "8C_1/2026")]

    index = crawl.load_index(raw_dir)
    assert set(index["days"]) == {"2026-07-01", "2026-07-02"}
    assert "ingested_at" in index["days"]["2026-07-01"]

    db = VectorDB(db_dir=db_dir, embedder=emb)
    assert db.count() == 1
    assert db.query(date_from="2026-07-01", date_to="2026-07-02")[0]["case_id"] == "8C_1/2026"

    # Second call with same span must make zero HTTP requests and not touch VectorDB/embedder.
    calls["list"].clear()
    calls["fetch"].clear()

    def _forbid_db(*_args, **_kwargs):  # pragma: no cover
        raise AssertionError("VectorDB should not be instantiated on cached fast path")

    monkeypatch.setattr(ensure, "VectorDB", _forbid_db)
    second = ensure_period(
        "2026-07-01",
        "2026-07-02",
        source="entscheidsuche",
        raw_dir=raw_dir,
        db_dir=db_dir,
        today=date(2026, 7, 10),
    )
    assert second.complete is True
    assert second.new_judgements == 0
    assert second.existing_judgements == 1
    assert second.days_already_covered == 2
    assert second.days_crawled == 0
    assert calls["list"] == []
    assert calls["fetch"] == []


def test_ensure_period_crawls_only_missing_gap_days(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    db_dir = tmp_path / "vectordb"
    emb = _tfidf_embedder()
    calls = _install_fake_source(
        monkeypatch,
        {
            "2026-07-01": [{"case_id": "8C_10/2026", "subject": "UVG", "body": "Suva Unfall."}],
            "2026-07-02": [{"case_id": "8C_11/2026", "subject": "UVG", "body": "Suva Taggeld."}],
            "2026-07-03": [{"case_id": "8C_12/2026", "subject": "UVG", "body": "Suva Rente."}],
        },
    )

    ensure_period(
        "2026-07-01",
        "2026-07-01",
        source="entscheidsuche",
        raw_dir=raw_dir,
        db_dir=db_dir,
        embedder=emb,
        today=date(2026, 7, 10),
    )
    calls["list"].clear()
    calls["fetch"].clear()

    status = ensure_period(
        "2026-07-01",
        "2026-07-03",
        source="entscheidsuche",
        raw_dir=raw_dir,
        db_dir=db_dir,
        embedder=emb,
        today=date(2026, 7, 10),
    )
    assert calls["list"] == ["2026-07-02", "2026-07-03"]
    assert status.days_already_covered == 1
    assert status.days_crawled == 2
    assert status.new_judgements == 2
    assert status.existing_judgements == 1
    assert status.complete is True


def test_ensure_period_retries_failed_crawl_day_without_marking_index(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    db_dir = tmp_path / "vectordb"
    emb = _tfidf_embedder()
    fail_days = {"2026-07-02"}
    calls = _install_fake_source(
        monkeypatch,
        {
            "2026-07-01": [{"case_id": "8C_20/2026", "subject": "UVG", "body": "Suva Unfall."}],
            "2026-07-02": [{"case_id": "8C_21/2026", "subject": "UVG", "body": "Suva Kausalität."}],
        },
        fail_list_days=fail_days,
    )

    first = ensure_period(
        "2026-07-01",
        "2026-07-02",
        source="entscheidsuche",
        raw_dir=raw_dir,
        db_dir=db_dir,
        embedder=emb,
        today=date(2026, 7, 10),
    )
    assert first.complete is False
    assert first.failed_days == ["2026-07-02"]
    assert first.days_crawled == 1
    assert set(crawl.load_index(raw_dir)["days"]) == {"2026-07-01"}

    # Fix failure: second call must retry only 2026-07-02 and complete.
    fail_days.clear()
    calls["list"].clear()
    _install_fake_source(
        monkeypatch,
        {
            "2026-07-01": [{"case_id": "8C_20/2026", "subject": "UVG", "body": "Suva Unfall."}],
            "2026-07-02": [{"case_id": "8C_21/2026", "subject": "UVG", "body": "Suva Kausalität."}],
        },
        fail_list_days=fail_days,
    )
    # Re-track list calls cleanly:
    calls2 = _install_fake_source(
        monkeypatch,
        {
            "2026-07-01": [{"case_id": "8C_20/2026", "subject": "UVG", "body": "Suva Unfall."}],
            "2026-07-02": [{"case_id": "8C_21/2026", "subject": "UVG", "body": "Suva Kausalität."}],
        },
    )
    second = ensure_period(
        "2026-07-01",
        "2026-07-02",
        source="entscheidsuche",
        raw_dir=raw_dir,
        db_dir=db_dir,
        embedder=emb,
        today=date(2026, 7, 10),
    )
    assert calls2["list"] == ["2026-07-02"]
    assert second.complete is True
    assert second.failed_days == []
    assert second.new_judgements == 1
    assert set(crawl.load_index(raw_dir)["days"]) == {"2026-07-01", "2026-07-02"}


def test_ensure_period_failed_ingest_does_not_mark_index_and_retries_without_refetch(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    db_dir = tmp_path / "vectordb"
    emb = _tfidf_embedder()
    db = VectorDB(db_dir=db_dir, embedder=emb)
    calls = _install_fake_source(
        monkeypatch,
        {"2026-07-01": [{"case_id": "8C_30/2026", "subject": "UVG", "body": "Suva Rente."}]},
    )

    real_ingest = db.ingest_records

    def broken_ingest(*_args, **_kwargs):
        raise RuntimeError("simulated embedder outage")

    monkeypatch.setattr(db, "ingest_records", broken_ingest)
    first = ensure_period(
        "2026-07-01",
        "2026-07-01",
        source="entscheidsuche",
        raw_dir=raw_dir,
        db_dir=db_dir,
        db=db,
        today=date(2026, 7, 10),
    )
    assert first.complete is False
    assert first.failed_days == ["2026-07-01"]
    assert "2026-07-01" not in crawl.load_index(raw_dir)["days"]
    assert db.count() == 0

    # Restore ingest_records; valid JSON already on disk must not be re-downloaded.
    monkeypatch.setattr(db, "ingest_records", real_ingest)
    calls["fetch"].clear()
    second = ensure_period(
        "2026-07-01",
        "2026-07-01",
        source="entscheidsuche",
        raw_dir=raw_dir,
        db_dir=db_dir,
        db=db,
        today=date(2026, 7, 10),
    )
    assert second.complete is True
    assert second.failed_days == []
    assert second.ingested_judgements == 1
    assert calls["fetch"] == []
    assert "2026-07-01" in crawl.load_index(raw_dir)["days"]
    assert db.count() == 1


def test_ensure_period_rejects_and_clamps_future_dates(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    db_dir = tmp_path / "vectordb"
    emb = _tfidf_embedder()
    calls = _install_fake_source(
        monkeypatch,
        {
            "2026-07-04": [{"case_id": "8C_40/2026", "subject": "UVG", "body": "Suva Fall."}],
            "2026-07-05": [{"case_id": "8C_41/2026", "subject": "UVG", "body": "Today."}],
        },
    )

    with pytest.raises(ValueError):
        ensure_period("2026-07-06", "2026-07-05", raw_dir=raw_dir, db_dir=db_dir, today=date(2026, 7, 10))
    with pytest.raises(ValueError):
        ensure_period("2026-07-05", "2026-07-07", raw_dir=raw_dir, db_dir=db_dir, today=date(2026, 7, 5))

    status = ensure_period(
        "2026-07-04",
        "2026-07-06",
        source="entscheidsuche",
        raw_dir=raw_dir,
        db_dir=db_dir,
        embedder=emb,
        today=date(2026, 7, 5),
    )
    assert calls["list"] == ["2026-07-04"]
    assert status.effective_date_to == "2026-07-04"
    assert status.skipped_future_days == ["2026-07-05", "2026-07-06"]
    assert status.complete is False  # requested end included today/future
    assert status.new_judgements == 1


def test_ensure_period_lock_prevents_concurrent_double_crawl(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    db_dir = tmp_path / "vectordb"
    emb = _tfidf_embedder()
    calls = _install_fake_source(
        monkeypatch,
        {"2026-07-01": [{"case_id": "8C_50/2026", "subject": "UVG", "body": "Suva Lock."}]},
        list_delay=0.05,
    )

    barrier = threading.Barrier(2)
    results: list[PeriodStatus] = []
    errors: list[BaseException] = []

    def worker():
        try:
            barrier.wait(timeout=5)
            res = ensure_period(
                "2026-07-01",
                "2026-07-01",
                source="entscheidsuche",
                raw_dir=raw_dir,
                db_dir=db_dir,
                embedder=emb,
                today=date(2026, 7, 10),
            )
            results.append(res)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not errors
    assert len(results) == 2
    assert calls["list"] == ["2026-07-01"]
    assert calls["fetch"] == [("2026-07-01", "8C_50/2026")]
    assert sorted(r.new_judgements for r in results) == [0, 1]


def test_ensure_period_emits_progress_events_and_handles_blocked(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    db_dir = tmp_path / "vectordb"
    emb = _tfidf_embedder()
    events: list[ProgressEvent] = []
    _install_fake_source(
        monkeypatch,
        {
            "2026-07-01": [{"case_id": "8C_60/2026", "subject": "UVG", "body": "Suva OK."}],
            "2026-07-02": [{"case_id": "8C_61/2026", "subject": "UVG", "body": "Blocked."}],
        },
        blocked_days={"2026-07-02"},
    )

    status = ensure_period(
        "2026-07-01",
        "2026-07-03",
        source="entscheidsuche",
        raw_dir=raw_dir,
        db_dir=db_dir,
        embedder=emb,
        on_progress=events.append,
        today=date(2026, 7, 10),
    )
    assert [e.stage for e in events] == ["checking", "crawling", "ingesting", "crawling", "error", "done"]
    assert status.failed_days == ["2026-07-02", "2026-07-03"]
    assert status.complete is False
    assert set(crawl.load_index(raw_dir)["days"]) == {"2026-07-01"}


def test_ensure_cli_prints_json_status(tmp_path, monkeypatch, capsys):
    raw_dir = tmp_path / "raw"
    db_dir = tmp_path / "vectordb"
    emb = _tfidf_embedder()
    _install_fake_source(
        monkeypatch,
        {"2026-07-01": [{"case_id": "8C_70/2026", "subject": "UVG", "body": "Suva CLI."}]},
    )

    real_ensure = ensure.ensure_period
    monkeypatch.setattr(
        ensure,
        "ensure_period",
        lambda *a, **kw: real_ensure(*a, **kw, embedder=emb, today=date(2026, 7, 10)),
    )
    code = ensure.main(
        [
            "--from", "2026-07-01",
            "--to", "2026-07-01",
            "--source", "entscheidsuche",
            "--raw-dir", str(raw_dir),
            "--db-dir", str(db_dir),
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["new_judgements"] == 1
    assert payload["complete"] is True


def test_ensure_period_ingests_uningested_files_on_covered_day_and_handles_failure(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    db_dir = tmp_path / "vectordb"
    emb = _tfidf_embedder()
    _install_fake_source(
        monkeypatch,
        {"2026-07-01": [{"case_id": "8C_80/2026", "subject": "UVG", "body": "Suva Covered."}]},
    )

    # First run crawls and ingests normally.
    first = ensure_period(
        "2026-07-01",
        "2026-07-01",
        source="entscheidsuche",
        raw_dir=raw_dir,
        db_dir=db_dir,
        embedder=emb,
        today=date(2026, 7, 10),
    )
    assert first.complete is True

    # Simulate a second valid raw file sitting on the covered day that is not yet in VectorDB.
    extra_rec = {
        "id": "8C_81_2026",
        "case_id": "8C_81/2026",
        "case_ids": ["8C_81/2026"],
        "decision_date": "2026-07-01",
        "department": "IV. öffentlich-rechtliche Abteilung",
        "subject": "UVG",
        "language": "de",
        "url": "https://example.invalid/8C_81_2026",
        "text": "Unfallversicherung Suva nachgereichtes Urteil.",
        "fetch": {"text_sha256": crawl.sha256("Unfallversicherung Suva nachgereichtes Urteil.")},
    }
    crawl.write_json(raw_dir / "2026-07" / "2026-07-01_8C_81_2026.json", extra_rec)

    db = VectorDB(db_dir=db_dir, embedder=emb)
    real_ingest = db.ingest_records
    monkeypatch.setattr(db, "ingest_records", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("boom")))
    failed = ensure_period(
        "2026-07-01",
        "2026-07-01",
        source="entscheidsuche",
        raw_dir=raw_dir,
        db_dir=db_dir,
        db=db,
        today=date(2026, 7, 10),
    )
    assert failed.failed_days == ["2026-07-01"]
    assert failed.complete is False

    monkeypatch.setattr(db, "ingest_records", real_ingest)
    repaired = ensure_period(
        "2026-07-01",
        "2026-07-01",
        source="entscheidsuche",
        raw_dir=raw_dir,
        db_dir=db_dir,
        db=db,
        today=date(2026, 7, 10),
    )
    assert repaired.failed_days == []
    assert repaired.ingested_judgements == 1
    assert repaired.complete is True
    assert db.count() == 2


def test_ensure_period_recrawls_corrupt_file_on_covered_day(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    db_dir = tmp_path / "vectordb"
    emb = _tfidf_embedder()
    calls = _install_fake_source(
        monkeypatch,
        {"2026-07-01": [{"case_id": "8C_82/2026", "subject": "UVG", "body": "Suva Repariert."}]},
    )

    ensure_period(
        "2026-07-01",
        "2026-07-01",
        source="entscheidsuche",
        raw_dir=raw_dir,
        db_dir=db_dir,
        embedder=emb,
        today=date(2026, 7, 10),
    )
    calls["list"].clear()
    calls["fetch"].clear()

    # Corrupt the saved JSON even though _index.json marks the day covered.
    target_json = raw_dir / "2026-07" / "2026-07-01_8C_82_2026.json"
    target_json.write_text("{corrupt json", encoding="utf-8")

    repaired = ensure_period(
        "2026-07-01",
        "2026-07-01",
        source="entscheidsuche",
        raw_dir=raw_dir,
        db_dir=db_dir,
        embedder=emb,
        today=date(2026, 7, 10),
    )
    assert calls["list"] == ["2026-07-01"]
    assert calls["fetch"] == [("2026-07-01", "8C_82/2026")]
    assert repaired.days_crawled == 1
    assert repaired.new_judgements == 1
    assert repaired.complete is True
    assert list((raw_dir / "2026-07").glob("*.corrupt-*"))
    assert crawl.is_valid(target_json)


def test_ensure_period_handles_empty_sqlite_and_fingerprint_or_missing_npz(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    db_dir = tmp_path / "vectordb"
    emb = _tfidf_embedder()
    _install_fake_source(
        monkeypatch,
        {"2026-07-01": [{"case_id": "8C_83/2026", "subject": "UVG", "body": "Suva Vector Check."}]},
    )

    # Empty 0-byte metadata.sqlite3 must not crash the fast-path SQLite check.
    db_dir.mkdir(parents=True, exist_ok=True)
    (db_dir / "metadata.sqlite3").write_bytes(b"")

    status = ensure_period(
        "2026-07-01",
        "2026-07-01",
        source="entscheidsuche",
        raw_dir=raw_dir,
        db_dir=db_dir,
        embedder=emb,
        today=date(2026, 7, 10),
    )
    assert status.complete is True
    assert status.ingested_judgements == 1

    # Deleting the .npz vector file must trigger re-ingest instead of taking the fast path.
    npz_path = next((db_dir / "vectors").rglob("*.npz"))
    npz_path.unlink()
    rebuilt = ensure_period(
        "2026-07-01",
        "2026-07-01",
        source="entscheidsuche",
        raw_dir=raw_dir,
        db_dir=db_dir,
        embedder=emb,
        today=date(2026, 7, 10),
    )
    assert rebuilt.ingested_judgements == 1
    assert npz_path.exists()

    # Different embedder fingerprint must also detect stale vectors and re-embed.
    other_emb = TfidfEmbedder()
    other_emb.fit(["Suva Unfallversicherung UVG Kausalzusammenhang neues Vokabular"])
    reembedded = ensure_period(
        "2026-07-01",
        "2026-07-01",
        source="entscheidsuche",
        raw_dir=raw_dir,
        db_dir=db_dir,
        embedder=other_emb,
        today=date(2026, 7, 10),
    )
    assert reembedded.ingested_judgements == 1


def test_ensure_period_accepts_datetime_and_no_double_count_on_retry(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    db_dir = tmp_path / "vectordb"
    emb = _tfidf_embedder()
    db = VectorDB(db_dir=db_dir, embedder=emb)
    _install_fake_source(
        monkeypatch,
        {"2026-07-01": [{"case_id": "8C_84/2026", "subject": "UVG", "body": "Suva Retry."}]},
    )

    real_ingest = db.ingest_records
    monkeypatch.setattr(db, "ingest_records", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("fail once")))
    failed = ensure_period(
        datetime(2026, 7, 1, 9, 30),
        datetime(2026, 7, 1, 18, 0),
        source="entscheidsuche",
        raw_dir=raw_dir,
        db_dir=db_dir,
        db=db,
        today=date(2026, 7, 10),
    )
    assert failed.complete is False

    monkeypatch.setattr(db, "ingest_records", real_ingest)
    events: list[ProgressEvent] = []
    retried = ensure_period(
        datetime(2026, 7, 1, 0, 0),
        datetime(2026, 7, 1, 23, 59),
        source="entscheidsuche",
        raw_dir=raw_dir,
        db_dir=db_dir,
        db=db,
        on_progress=events.append,
        today=date(2026, 7, 10),
    )
    assert retried.complete is True
    assert retried.failed_days == []
    assert retried.ingested_judgements == 1
    assert all(ev.total_days == 1 for ev in events)

