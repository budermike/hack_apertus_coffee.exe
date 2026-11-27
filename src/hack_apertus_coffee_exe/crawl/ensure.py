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
from datetime import date, timedelta
import json
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

from hack_apertus_coffee_exe.crawl import crawl
from hack_apertus_coffee_exe.crawl.vectordb import DEFAULT_DB_DIR, ISO_DATE_PREFIX_RE, VectorDB
from hack_apertus_coffee_exe.triage.embeddings import Embedder

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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coerce_date(value: date | str) -> date:
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


def _existing_db_doc_keys(db_dir: Path, date_from: str, date_to: str, db: VectorDB | None) -> set[str]:
    """Read ingested doc_keys for [date_from, date_to] without loading any embedder."""
    if db is not None:
        return {r["doc_key"] for r in db.query(date_from=date_from, date_to=date_to, include_vectors=False)}

    sqlite_path = db_dir / "metadata.sqlite3"
    if not sqlite_path.exists():
        return set()

    conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True, timeout=10.0)
    try:
        rows = conn.execute(
            "SELECT doc_key FROM judgements WHERE decision_date >= ? AND decision_date <= ?",
            (date_from, date_to),
        ).fetchall()
        return {str(row[0]) for row in rows}
    finally:
        conn.close()


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
    raw_dir: str | Path | None = None,
    db_dir: str | Path = DEFAULT_DB_DIR,
    embedder: Embedder | None = None,
    db: VectorDB | None = None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    today: date | None = None,
) -> PeriodStatus:
    """Ensure every past day in ``[date_from, date_to]`` is crawled AND ingested.

    Re-checking an already covered and ingested period performs zero HTTP
    requests and returns without loading the embedding model.
    """
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
        covered_days = [d for d in all_days if d.isoformat() in index.get("days", {})]
        missing_days = [d for d in all_days if d.isoformat() not in index.get("days", {})]

        raw_files = _scan_period_raw_files(out_dir, start, effective_end)
        existing_keys = _existing_db_doc_keys(db_path, start_iso, effective_end_iso, db)

        # Preserve order grouped by day for files already on disk that are missing in VectorDB.
        uningested_by_day: dict[str, list[Path]] = {}
        preexisting_valid = 0
        for iso_day, doc_key, file_path in raw_files:
            if not crawl.is_valid(file_path):
                continue
            preexisting_valid += 1
            if doc_key not in existing_keys:
                uningested_by_day.setdefault(iso_day, []).append(file_path)

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

        # Fast path: all days covered in _index.json AND every valid raw file already in VectorDB.
        if not missing_days and not uningested_by_day:
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
            )

        active_db: list[VectorDB] = [db] if db is not None else []

        def _get_db() -> VectorDB:
            if not active_db:
                active_db.append(VectorDB(db_dir=db_path, embedder=embedder))
            return active_db[0]

        new_judgements = 0
        ingested_judgements = 0
        failed_days: list[str] = []
        completed_steps = 0

        # Finish ingest for any valid raw judgements already on disk but missing from VectorDB.
        for iso_day in sorted(uningested_by_day):
            paths = uningested_by_day[iso_day]
            records = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
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
                _get_db().ingest_records(records, reference_ids=None)
                ingested_judgements += len(records)
                existing_keys.update(f"{iso_day}_{r.get('id') or crawl.safe_case_id(r['case_id'])}" for r in records)
            except Exception as exc:  # pragma: no cover - defensive guard
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

            day_records = [json.loads(p.read_text(encoding="utf-8")) for p in day_res["paths"]]
            pending_records = [
                r
                for r in day_records
                if f"{r['decision_date']}_{r.get('id') or crawl.safe_case_id(r['case_id'])}" not in existing_keys
            ]
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
                    _get_db().ingest_records(pending_records, reference_ids=None)
                    ingested_judgements += len(pending_records)
                    existing_keys.update(
                        f"{r['decision_date']}_{r.get('id') or crawl.safe_case_id(r['case_id'])}"
                        for r in pending_records
                    )
                except Exception as exc:
                    logger.error("%s: ingest failed: %s", iso_day, exc)
                    if iso_day not in failed_days:
                        failed_days.append(iso_day)
                    completed_steps += 1
                    continue

            index.setdefault("days", {})[iso_day] = {
                "source": cfg["source"],
                "judgements": day_res["judgements"],
                "crawled_at": crawl.now_utc(),
                "ingested_at": crawl.now_utc(),
            }
            crawl.write_json(out_dir / "_index.json", index)
            days_crawled += 1
            completed_steps += 1

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
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ensure a date span is crawled and ingested into Vector-DB (#35).")
    parser.add_argument("--from", dest="date_from", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--source", choices=list(crawl.SOURCES), help="Override crawler source")
    parser.add_argument("--config", default="configs/crawl.json")
    parser.add_argument("--raw-dir", default=None)
    parser.add_argument("--db-dir", default=DEFAULT_DB_DIR)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    status = ensure_period(
        args.date_from,
        args.date_to,
        config_path=args.config,
        source=args.source,
        raw_dir=args.raw_dir,
        db_dir=args.db_dir,
    )
    print(json.dumps(status.to_dict(), ensure_ascii=False, indent=2))
    return 0 if not status.failed_days else 1


if __name__ == "__main__":
    raise SystemExit(main())
