"""Ensure a requested date span is crawled and ingested into the Vector-DB (#35).

Provides ``ensure_period(date_from, date_to) -> PeriodStatus`` for Triage and the UI:

1. Checks ``data/raw/_index.json`` and ``data/processed/vectordb/metadata.sqlite3``
   for uncovered days or un-ingested raw JSON files (without importing PyTorch or
   making HTTP requests when everything is already covered).
2. Acquires an in-process thread lock plus an OS file lock so concurrent requests
   do not double-crawl the same period.
3. Clamps or rejects future dates (including today, whose judgements are incomplete).
4. Crawls only missing days, embeds and ingests only new judgements, and marks a
   day covered in ``_index.json`` only when both crawl AND ingest succeed.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
import json
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable, Iterator

import numpy as np

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

from hack_apertus_coffee_exe.crawl import crawl
from hack_apertus_coffee_exe.crawl.vectordb import (
    DEFAULT_DB_DIR,
    DEFAULT_JUDGEMENTS_DIR,
    ISO_DATE_PREFIX_RE,
    VectorDB,
    embedder_fingerprint,
    load_reference_case_ids,
)
from hack_apertus_coffee_exe.summary import store
from hack_apertus_coffee_exe.triage.embeddings import Embedder, TfidfEmbedder

logger = logging.getLogger(__name__)

_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[Path, threading.Lock] = {}


@dataclass(frozen=True)
class ProgressEvent:
    """Progress update emitted to UI/CLI callbacks during ``ensure_period``."""

    stage: str  # "checking" | "crawling" | "ingesting" | "error" | "done"
    message: str
    completed_days: int
    total_days: int
    current_day: str | None = None
    new_judgements: int = 0
    existing_judgements: int = 0


@dataclass(frozen=True)
class PeriodStatus:
    """Summary of what ``ensure_period`` found, crawled, ingested, or could not fetch."""

    date_from: str
    date_to: str
    effective_date_to: str
    new_judgements: int
    existing_judgements: int
    ingested_judgements: int
    days_already_covered: int
    days_crawled: int
    failed_days: list[str]
    skipped_future_days: list[str]
    complete: bool
    message: str
    cases_saved: int = 0
    cases_existing: int = 0
    summary_errors: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coerce_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _iter_days(start: date, end: date) -> Iterator[date]:
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _iter_months(start: date, end: date) -> Iterator[str]:
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield f"{year:04d}-{month:02d}"
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1


def _get_thread_lock(lock_path: Path) -> threading.Lock:
    resolved = lock_path.resolve()
    with _LOCKS_GUARD:
        lock = _PATH_LOCKS.get(resolved)
        if lock is None:
            lock = threading.Lock()
            _PATH_LOCKS[resolved] = lock
        return lock


@contextmanager
def _period_lock(lock_path: Path) -> Iterator[None]:
    """Serialize concurrent ensure_period calls across threads and OS processes."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    thread_lock = _get_thread_lock(lock_path)
    with thread_lock:
        if fcntl is None:  # pragma: no cover
            yield
            return
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _emit(on_progress: Callable[[ProgressEvent], None] | None, event: ProgressEvent) -> None:
    if on_progress is not None:
        on_progress(event)


def _record_doc_key(record: dict[str, Any]) -> str:
    doc_id = record.get("id") or crawl.safe_case_id(record["case_id"])
    return f"{record['decision_date']}_{doc_id}"


def _dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for record in records:
        dk = _record_doc_key(record)
        if dk not in seen:
            seen.add(dk)
            unique.append(record)
    return unique


def _expected_fingerprint(
    db_dir: Path,
    db: VectorDB | None,
    embedder: Embedder | None,
) -> str | None:
    """Resolve the active embedder fingerprint without instantiating PyTorch."""
    if db is not None:
        return db.fingerprint
    if embedder is not None:
        return embedder_fingerprint(embedder)
    meta_path = db_dir / "embedder_meta.json"
    if not meta_path.exists():
        return None
    try:
        saved = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    kind = saved.get("kind")
    if kind == "tfidf":
        emb = TfidfEmbedder(
            use_char_ngrams=bool(saved.get("use_char_ngrams", True)),
            char_ngram_range=tuple(saved.get("char_ngram_range", (3, 5))),
        )
        emb.vocab_ = {k: int(v) for k, v in (saved.get("vocab") or {}).items()}
        emb.idf_ = np.asarray(saved.get("idf") or [], dtype=np.float32)
        return embedder_fingerprint(emb)
    name = saved.get("name")
    return f"{name}|ctx_chunk=350/50" if name else None


def _existing_db_doc_keys(
    db_dir: Path,
    date_from: str,
    date_to: str,
    db: VectorDB | None,
    embedder: Embedder | None = None,
) -> set[str]:
    """Read ingested doc_keys for [date_from, date_to] without loading any embedder."""
    expected_fp = _expected_fingerprint(db_dir, db, embedder)
    rows: list[tuple[Any, Any, Any]] = []

    if db is not None:
        with db._connect() as conn:
            rows = conn.execute(
                "SELECT doc_key, fingerprint, vector_file FROM judgements WHERE decision_date >= ? AND decision_date <= ?",
                (date_from, date_to),
            ).fetchall()
    else:
        sqlite_path = db_dir / "metadata.sqlite3"
        if not sqlite_path.exists():
            return set()
        conn = sqlite3.connect(f"{sqlite_path.resolve().as_uri()}?mode=ro", uri=True, timeout=10.0)
        try:
            rows = conn.execute(
                "SELECT doc_key, fingerprint, vector_file FROM judgements WHERE decision_date >= ? AND decision_date <= ?",
                (date_from, date_to),
            ).fetchall()
        except sqlite3.OperationalError:
            return set()
        finally:
            conn.close()

    present: set[str] = set()
    for doc_key, fingerprint, vector_file in rows:
        if expected_fp is not None and str(fingerprint) != expected_fp:
            continue
        if vector_file and (db_dir / str(vector_file)).exists():
            present.add(str(doc_key))
    return present


def _scan_period_raw_files(out_dir: Path, start: date, end: date) -> list[tuple[str, str, Path]]:
    """Return ``(decision_date, doc_key, path)`` for raw JSON files in ``[start, end]``."""
    if not out_dir.exists():
        return []
    start_iso, end_iso = start.isoformat(), end.isoformat()
    found: list[tuple[str, str, Path]] = []
    for month in _iter_months(start, end):
        month_dir = out_dir / month
        if not month_dir.is_dir():
            continue
        for path in sorted(month_dir.glob("*.json")):
            if path.name.startswith((".", "_")):
                continue
            stem = path.stem
            if len(stem) <= 11 or stem[10] != "_":
                continue
            iso_date = stem[:10]
            if ISO_DATE_PREFIX_RE.match(iso_date) and start_iso <= iso_date <= end_iso:
                found.append((iso_date, stem, path))
    return found


def _case_id_from_doc_key(doc_key: str) -> str:
    """Recover original ``8C_123/2026`` case_id from ``2026-06-01_8C_123_2026``."""
    safe_id = doc_key[11:] if len(doc_key) > 11 and doc_key[10] == "_" else doc_key
    if "/" not in safe_id and "_" in safe_id:
        head, _, year = safe_id.rpartition("_")
        return f"{head}/{year}"
    return safe_id


def _existing_summary_numbers(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT case_number FROM case_numbers").fetchall()
    return {str(row[0]) for row in rows}


def _resolve_reference_ids(
    db_path: Path, judgements_dir: str | Path | None
) -> set[str] | None:
    if judgements_dir is not None:
        base = Path(judgements_dir)
        return load_reference_case_ids(base) if base.exists() else None
    if db_path.resolve() == Path(DEFAULT_DB_DIR).resolve():
        base = Path(DEFAULT_JUDGEMENTS_DIR)
        return load_reference_case_ids(base) if base.exists() else None
    return None


def _default_config(start: date, effective_end: date, source: str | None, raw_dir: str | Path | None) -> dict[str, Any]:
    chosen = source or "entscheidsuche"
    base_cfg: dict[str, Any] = {
        "source": chosen,
        "from_date": start,
        "to_date": effective_end,
        "out_dir": str(raw_dir or "data/raw"),
        "report_path": "results/tables/crawl_report.csv",
        "sources": {
            "bger": {
                "base_url": "https://search.bger.ch/ext/eurospider/live/de/php/aza/http/index.php",
                "delay_seconds": 2.0,
            },
            "entscheidsuche": {
                "base_url": "https://entscheidsuche.ch/_searchV2.php",
                "delay_seconds": 0.5,
            },
        },
    }
    if chosen not in base_cfg["sources"]:
        raise ValueError(f"source must be one of {list(base_cfg['sources'])}")
    base_cfg.update(base_cfg["sources"][chosen])
    return base_cfg


def _status_message(
    new_judgements: int,
    existing_judgements: int,
    ingested_judgements: int,
    failed_days: list[str],
    skipped_future_days: list[str],
) -> str:
    if failed_days:
        return (
            f"{len(failed_days)} Tag(e) fehlgeschlagen ({', '.join(failed_days[:3])}); "
            f"{new_judgements} neue und {existing_judgements} vorhandene Urteile bereit."
        )
    if new_judgements > 0:
        msg = f"{new_judgements} neue Urteile geladen ({existing_judgements} bereits vorhanden)."
    elif ingested_judgements > 0:
        msg = f"{ingested_judgements} vorhandene Urteile eingebettet ({existing_judgements} insgesamt)."
    else:
        msg = f"Zeitraum bereits vollständig ({existing_judgements} Urteile)."
    if skipped_future_days:
        msg += f" {len(skipped_future_days)} zukünftige/heutige Tage übersprungen."
    return msg


def ensure_period(
    date_from: date | str,
    date_to: date | str,
    *,
    config_path: str | Path = "configs/crawl.json",
    source: str | None = None,
    refresh: bool = False,
    raw_dir: str | Path | None = None,
    db_dir: str | Path = DEFAULT_DB_DIR,
    embedder: Embedder | None = None,
    db: VectorDB | None = None,
    vectordb: VectorDB | None = None,
    conn: sqlite3.Connection | None = None,
    summary_db_path: str | Path | None = None,
    judgements_dir: str | Path | None = None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    today: date | None = None,
) -> PeriodStatus:
    """Ensure every past day in ``[date_from, date_to]`` is crawled, in Vector-DB, and in Summaries-DB.

    Re-checking an already covered, ingested, and synced period performs zero
    HTTP requests and returns without loading the embedding model.
    """
    if db is not None and vectordb is not None and db is not vectordb:
        raise ValueError("Pass either 'db' or 'vectordb', not two different instances")
    injected_db = vectordb if vectordb is not None else db

    start = _coerce_date(date_from)
    requested_end = _coerce_date(date_to)
    if start > requested_end:
        raise ValueError(f"date_from ({start}) must not be after date_to ({requested_end})")

    ref_today = today or date.today()
    if start >= ref_today:
        raise ValueError(
            f"Zeitraum liegt in der Zukunft ({start.isoformat()} >= {ref_today.isoformat()}); "
            "Urteile des laufenden oder eines zukünftigen Tages liegen noch nicht vollständig vor."
        )

    skipped_future_days: list[str] = []
    effective_end = requested_end
    if requested_end >= ref_today:
        effective_end = ref_today - timedelta(days=1)
        skipped_future_days = [d.isoformat() for d in _iter_days(ref_today, requested_end)]

    cfg_path = Path(config_path)
    if cfg_path.exists():
        cfg = crawl.load_config(
            cfg_path,
            from_date=start.isoformat(),
            to_date=effective_end.isoformat(),
            source=source,
            out_dir=str(raw_dir) if raw_dir is not None else None,
        )
    else:
        cfg = _default_config(start, effective_end, source, raw_dir)

    out_dir = Path(cfg["out_dir"])
    db_path = Path(db_dir)
    start_iso = start.isoformat()
    effective_end_iso = effective_end.isoformat()

    with _period_lock(db_path / ".ensure_period.lock"):
        index = crawl.load_index(out_dir)
        all_days = list(_iter_days(start, effective_end))
        if refresh:
            covered_days: list[date] = []
            missing_days = list(all_days)
        else:
            covered_days = [d for d in all_days if d.isoformat() in index.get("days", {})]
            missing_days = [d for d in all_days if d.isoformat() not in index.get("days", {})]
        covered_set = {d.isoformat() for d in covered_days}

        raw_files = _scan_period_raw_files(out_dir, start, effective_end)
        existing_keys = _existing_db_doc_keys(db_path, start_iso, effective_end_iso, injected_db, embedder)

        summary_conn = conn if conn is not None else store.connect(summary_db_path)
        owns_conn = conn is None
        try:
            existing_summary_numbers = _existing_summary_numbers(summary_conn)

            # Split raw files strictly: covered days with un-ingested valid JSONs go to
            # uningested_by_day; covered days with corrupt JSONs move to missing_days so
            # crawl_day repairs them; uncovered days are handled atomically in missing_days.
            uningested_by_day: dict[str, list[Path]] = {}
            unsynced_summary_paths: list[Path] = []
            corrupt_covered_days: set[str] = set()
            preexisting_valid = 0
            for iso_day, doc_key, file_path in raw_files:
                if not crawl.is_valid(file_path):
                    if iso_day in covered_set:
                        corrupt_covered_days.add(iso_day)
                    continue
                preexisting_valid += 1
                if iso_day in covered_set and doc_key not in existing_keys:
                    uningested_by_day.setdefault(iso_day, []).append(file_path)
                inferred_cid = _case_id_from_doc_key(doc_key)
                if iso_day in covered_set and inferred_cid not in existing_summary_numbers:
                    unsynced_summary_paths.append(file_path)

            if corrupt_covered_days:
                for iso_day in corrupt_covered_days:
                    covered_set.discard(iso_day)
                    uningested_by_day.pop(iso_day, None)
                covered_days = [d for d in covered_days if d.isoformat() not in corrupt_covered_days]
                missing_set = {d.isoformat() for d in missing_days} | corrupt_covered_days
                missing_days = sorted(date.fromisoformat(d) for d in missing_set)

            total_steps = len(missing_days) + len(uningested_by_day)
            _emit(
                on_progress,
                ProgressEvent(
                    stage="checking",
                    message=f"Prüfe Zeitraum {start_iso} bis {effective_end_iso} ...",
                    completed_days=0,
                    total_days=total_steps,
                    existing_judgements=preexisting_valid,
                ),
            )

            # Fast path: all days covered in _index.json, every valid raw file in Vector-DB,
            # and every covered judgement registered in Summaries-DB.
            if not missing_days and not uningested_by_day and not unsynced_summary_paths:
                msg = _status_message(0, preexisting_valid, 0, [], skipped_future_days)
                _emit(
                    on_progress,
                    ProgressEvent(
                        stage="done",
                        message=msg,
                        completed_days=0,
                        total_days=0,
                        new_judgements=0,
                        existing_judgements=preexisting_valid,
                    ),
                )
                return PeriodStatus(
                    date_from=start_iso,
                    date_to=requested_end.isoformat(),
                    effective_date_to=effective_end_iso,
                    new_judgements=0,
                    existing_judgements=preexisting_valid,
                    ingested_judgements=0,
                    days_already_covered=len(covered_days),
                    days_crawled=0,
                    failed_days=[],
                    skipped_future_days=skipped_future_days,
                    complete=not skipped_future_days,
                    message=msg,
                    cases_saved=0,
                    cases_existing=preexisting_valid,
                    summary_errors=0,
                )

            active_db: list[VectorDB] = [injected_db] if injected_db is not None else []
            ref_ids = _resolve_reference_ids(db_path, judgements_dir)

            def _get_db() -> VectorDB:
                if not active_db:
                    active_db.append(VectorDB(db_dir=db_path, embedder=embedder))
                return active_db[0]

            new_judgements = 0
            ingested_judgements = 0
            cases_saved = 0
            cases_existing = 0
            summary_errors = 0
            failed_days: list[str] = []
            completed_steps = 0

            def _sync_records_to_summary_db(records: list[dict[str, Any]]) -> None:
                nonlocal cases_saved, cases_existing, summary_errors
                for rec in records:
                    cid = rec.get("case_id")
                    if not isinstance(cid, str) or not cid:
                        summary_errors += 1
                        continue
                    numbers = {cid, *(rec.get("case_ids") or ())}
                    if all(n in existing_summary_numbers for n in numbers):
                        cases_existing += 1
                        continue
                    try:
                        store.save_case_from_crawl(summary_conn, rec)
                        existing_summary_numbers.update(numbers)
                        cases_saved += 1
                    except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                        logger.warning("Summaries-DB sync failed for %s: %s", cid, exc)
                        summary_errors += 1

            # Ensure Summaries-DB has cases/case_numbers for already-covered raw files.
            if unsynced_summary_paths:
                covered_records: list[dict[str, Any]] = []
                for p in unsynced_summary_paths:
                    try:
                        covered_records.append(json.loads(p.read_text(encoding="utf-8")))
                    except (OSError, ValueError) as exc:
                        logger.warning("Failed reading %s for Summaries-DB sync: %s", p, exc)
                        summary_errors += 1
                _sync_records_to_summary_db(covered_records)

            # Finish ingest for any valid raw judgements on already-covered days missing from VectorDB.
            for iso_day in sorted(uningested_by_day):
                paths = uningested_by_day[iso_day]
                records = _dedupe_records([json.loads(p.read_text(encoding="utf-8")) for p in paths])
                _emit(
                    on_progress,
                    ProgressEvent(
                        stage="ingesting",
                        message=f"{len(records)} vorhandene Urteile werden eingebettet ({iso_day}) ...",
                        completed_days=completed_steps,
                        total_days=total_steps,
                        current_day=iso_day,
                        new_judgements=new_judgements,
                        existing_judgements=preexisting_valid,
                    ),
                )
                try:
                    _get_db().ingest_records(records, reference_ids=ref_ids)
                    ingested_judgements += len(records)
                    existing_keys.update(_record_doc_key(r) for r in records)
                    _sync_records_to_summary_db(records)
                except Exception as exc:
                    logger.error("%s: ingest failed: %s", iso_day, exc)
                    if iso_day not in failed_days:
                        failed_days.append(iso_day)
                completed_steps += 1

            # Crawl and ingest missing days one by one; only covered when BOTH succeed.
            days_crawled = 0
            for idx, day in enumerate(missing_days):
                iso_day = day.isoformat()
                _emit(
                    on_progress,
                    ProgressEvent(
                        stage="crawling",
                        message=f"Lade Urteile für {iso_day} ...",
                        completed_days=completed_steps,
                        total_days=total_steps,
                        current_day=iso_day,
                        new_judgements=new_judgements,
                        existing_judgements=preexisting_valid,
                    ),
                )
                try:
                    day_res = crawl.crawl_day(cfg, day, out_dir)
                except crawl.Blocked as exc:
                    logger.error("%s: crawl blocked: %s", iso_day, exc)
                    for rem in missing_days[idx:]:
                        rem_iso = rem.isoformat()
                        if rem_iso not in failed_days:
                            failed_days.append(rem_iso)
                    _emit(
                        on_progress,
                        ProgressEvent(
                            stage="error",
                            message=f"Crawler blockiert bei {iso_day}: {exc}",
                            completed_days=completed_steps,
                            total_days=total_steps,
                            current_day=iso_day,
                            new_judgements=new_judgements,
                            existing_judgements=preexisting_valid,
                        ),
                    )
                    break

                new_judgements += day_res["downloaded"]
                if not day_res["day_ok"]:
                    if iso_day not in failed_days:
                        failed_days.append(iso_day)
                    completed_steps += 1
                    continue

                day_records = _dedupe_records(
                    [json.loads(p.read_text(encoding="utf-8")) for p in day_res["paths"]]
                )
                pending_records = [r for r in day_records if _record_doc_key(r) not in existing_keys]
                if pending_records:
                    _emit(
                        on_progress,
                        ProgressEvent(
                            stage="ingesting",
                            message=f"{len(pending_records)} neue Urteile werden geladen ({iso_day}) ...",
                            completed_days=completed_steps,
                            total_days=total_steps,
                            current_day=iso_day,
                            new_judgements=new_judgements,
                            existing_judgements=preexisting_valid,
                        ),
                    )
                    try:
                        _get_db().ingest_records(pending_records, reference_ids=ref_ids)
                        ingested_judgements += len(pending_records)
                        existing_keys.update(_record_doc_key(r) for r in pending_records)
                    except Exception as exc:
                        logger.error("%s: ingest failed: %s", iso_day, exc)
                        if iso_day not in failed_days:
                            failed_days.append(iso_day)
                        completed_steps += 1
                        continue

                _sync_records_to_summary_db(day_records)

                index.setdefault("days", {})[iso_day] = {
                    "source": cfg["source"],
                    "judgements": day_res["judgements"],
                    "crawled_at": crawl.now_utc(),
                    "ingested_at": crawl.now_utc(),
                }
                crawl.write_json(out_dir / "_index.json", index)
                days_crawled += 1
                completed_steps += 1

            # Count covered cases that did not need a summary sync during this run.
            if cases_existing == 0 and preexisting_valid > cases_saved:
                cases_existing = max(0, preexisting_valid - cases_saved - summary_errors)

            msg = _status_message(
                new_judgements,
                preexisting_valid,
                ingested_judgements,
                failed_days,
                skipped_future_days,
            )
            _emit(
                on_progress,
                ProgressEvent(
                    stage="done",
                    message=msg,
                    completed_days=completed_steps,
                    total_days=total_steps,
                    new_judgements=new_judgements,
                    existing_judgements=preexisting_valid,
                ),
            )
            return PeriodStatus(
                date_from=start_iso,
                date_to=requested_end.isoformat(),
                effective_date_to=effective_end_iso,
                new_judgements=new_judgements,
                existing_judgements=preexisting_valid,
                ingested_judgements=ingested_judgements,
                days_already_covered=len(covered_days),
                days_crawled=days_crawled,
                failed_days=failed_days,
                skipped_future_days=skipped_future_days,
                complete=(not failed_days and not skipped_future_days),
                message=msg,
                cases_saved=cases_saved,
                cases_existing=cases_existing,
                summary_errors=summary_errors,
            )
        finally:
            if owns_conn:
                summary_conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ensure a date span is crawled and ingested into Vector-DB (#35, #49).")
    parser.add_argument("--from", dest="date_from", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--source", choices=list(crawl.SOURCES), help="Override crawler source")
    parser.add_argument("--refresh", action="store_true", help="Re-check days already in _index.json")
    parser.add_argument("--config", default="configs/crawl.json")
    parser.add_argument("--raw-dir", default=None)
    parser.add_argument("--db-dir", default=DEFAULT_DB_DIR)
    parser.add_argument("--summary-db", default=None, help="Path to summaries.db (default: SUMMARY_DB_PATH)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    status = ensure_period(
        args.date_from,
        args.date_to,
        config_path=args.config,
        source=args.source,
        refresh=args.refresh,
        raw_dir=args.raw_dir,
        db_dir=args.db_dir,
        summary_db_path=args.summary_db,
    )
    print(json.dumps(status.to_dict(), ensure_ascii=False, indent=2))
    return 0 if not status.failed_days else 1


if __name__ == "__main__":
    raise SystemExit(main())
