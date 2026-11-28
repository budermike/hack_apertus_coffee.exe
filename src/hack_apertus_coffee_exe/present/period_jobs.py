"""Fast day-level dynamic period fetch/triage (#69, #91), single-case add (#70), and FR reason (#67)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, timedelta
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Iterator

from ..crawl import crawl as crawl_mod
from ..summary import store

if TYPE_CHECKING:
    from ..crawl.ensure import ProgressEvent
from ..summary.judgement import operative_outcome
from ..triage import relevance, relevance_period
from ..triage.config import TriageConfig, load_config
from ..triage.llm import GatewayConfig
from ..triage.pipeline import CrawlItem, load_crawl_items
from .judgements import JudgementList, _OUTCOMES, _is_single_judge, to_ui_item, ui_id

logger = logging.getLogger(__name__)

RECENT_EMPTY_LAG_DAYS = 21


class _CancelledJob(Exception):
    """Raised when a newer search supersedes the active period fetch/triage job."""


def _iter_days(start: date, end: date) -> Iterator[date]:
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def _parse_range(date_from: Any, date_to: Any) -> tuple[date, date]:
    if not isinstance(date_from, str) or not isinstance(date_to, str):
        raise ValueError("from and to must be ISO dates (YYYY-MM-DD)")
    since = date.fromisoformat(date_from)
    until = date.fromisoformat(date_to)
    if since > until:
        raise ValueError("from must not be after to")
    today = date.today()
    if since >= today:
        raise ValueError("today and future dates are not completely available yet")
    return since, min(until, today - timedelta(days=1))


def _is_social_chamber_id(case_id: str) -> bool:
    return str(case_id).split("_")[0] in relevance.SOCIAL_CHAMBERS


class PeriodTriageManager:
    """Fetches and scores only uncovered past days, reusing all existing crawl and triage caches."""

    def __init__(
        self,
        *,
        raw_dir: str | Path,
        store_dir: str | Path,
        judgements: JudgementList | None,
        crawl_cache_dir: str | Path | None = None,
        db_path: str | Path | None = None,
        backend: Any = None,
        config: TriageConfig | None = None,
        calibration_path: str | Path = "results/tables/relevance_calibration_q2_2026.json",
        judgements_dir: str | Path = "judgements",
        model: str | None = None,
        ensure_fn: Callable[..., Any] | None = None,
        fetch_case_fn: Callable[[str, str | Path], dict[str, Any]] | None = None,
        client_factory: Callable[[], Any] | None = None,
        recent_empty_lag_days: int = RECENT_EMPTY_LAG_DAYS,
    ) -> None:
        self.raw_dir = Path(raw_dir)
        self.crawl_cache_dir = Path(crawl_cache_dir) if crawl_cache_dir else Path(store_dir) / "_crawl"
        self.store_dir = Path(store_dir)
        self.judgements = judgements
        self.db_path = db_path
        self.backend = backend
        self.config = config or (judgements.config if judgements is not None else load_config())
        self.calibration_path = Path(calibration_path)
        self.judgements_dir = Path(judgements_dir)
        self.model = model
        self._ensure_fn = ensure_fn
        self._fetch_case_fn = fetch_case_fn or (
            lambda cid, target: crawl_mod.fetch_single_case(cid, target, read_dirs=(self.raw_dir,))
        )
        self._client_factory = client_factory or (
            lambda: relevance.ApertusRelevanceClient(
                config=GatewayConfig.from_env(model=self.model),
                cache_dir=self.config.cache_dir,
            )
        )
        self.recent_empty_lag_days = recent_empty_lag_days
        self._lock = threading.Lock()
        self._covered_lock = threading.Lock()
        self._pool_lock = threading.Lock()
        self._cached_pool: list[relevance.Precedent] | None = None
        self._jobs: dict[tuple[str, str], dict[str, Any]] = {}
        self._job_cancels: dict[tuple[str, str], threading.Event] = {}
        self._active_cancel: threading.Event | None = None
        self._threads: list[threading.Thread] = []

    def invalidate_pool(self) -> None:
        with self._pool_lock:
            self._cached_pool = None

    def _covered_days_file(self) -> Path:
        return self.store_dir / "covered_days.json"

    def _legacy_covered_file(self) -> Path:
        return self.store_dir / "_covered.json"

    def _read_persisted_days_unlocked(self) -> set[str]:
        days: set[str] = set()
        path = self._covered_days_file()
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    days.update(str(d) for d in loaded if isinstance(d, str))
            except (OSError, ValueError):
                pass
        legacy = self._legacy_covered_file()
        if legacy.is_file():
            try:
                pairs = json.loads(legacy.read_text(encoding="utf-8"))
                for pair in pairs if isinstance(pairs, list) else ():
                    if isinstance(pair, list) and len(pair) == 2:
                        s, e = date.fromisoformat(pair[0]), date.fromisoformat(pair[1])
                        for d in _iter_days(s, e):
                            days.add(d.isoformat())
            except (OSError, ValueError):
                pass
        return days

    def _all_covered_days(self) -> set[str]:
        with self._covered_lock:
            days = self._read_persisted_days_unlocked()
        if self.judgements is not None:
            ranges: list[tuple[str, str]] = []
            if hasattr(self.judgements, "covered_ranges"):
                ranges = self.judgements.covered_ranges()
            elif hasattr(self.judgements, "available_range"):
                avail = self.judgements.available_range()
                if isinstance(avail, dict) and avail.get("from") and avail.get("to"):
                    ranges = [(avail["from"], avail["to"])]
            for s_iso, e_iso in ranges:
                try:
                    for d in _iter_days(date.fromisoformat(s_iso), date.fromisoformat(e_iso)):
                        days.add(d.isoformat())
                except ValueError:
                    continue
        return days

    def _mark_days_covered(self, days_to_add: list[str]) -> None:
        if not days_to_add:
            return
        with self._covered_lock:
            self.store_dir.mkdir(parents=True, exist_ok=True)
            covered = self._read_persisted_days_unlocked()
            covered.update(days_to_add)
            target = self._covered_days_file()
            tmp = target.with_name(f".covered_days.{os.getpid()}.{threading.get_ident()}.json.tmp")
            tmp.write_text(json.dumps(sorted(covered), ensure_ascii=False), encoding="utf-8")
            tmp.replace(target)

    def covers(self, date_from: str, date_to: str) -> bool:
        try:
            since, until = _parse_range(date_from, date_to)
        except ValueError:
            return False
        covered = self._all_covered_days()
        return all(d.isoformat() in covered for d in _iter_days(since, until))

    def _load_calibration(self) -> dict[str, Any]:
        candidates = [
            self.calibration_path,
            Path(__file__).resolve().parents[3] / "results" / "tables" / "relevance_calibration_q2_2026.json",
        ]
        for path in candidates:
            if path.is_file():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and "w" in data and "t" in data:
                        return data
                except (OSError, ValueError):
                    pass
        return {"w": [4.0, 2.5, 0.8, -0.8], "t": 0.1484}

    def _precedent_pool(self) -> list[relevance.Precedent]:
        with self._pool_lock:
            if self._cached_pool is not None:
                return self._cached_pool
            try:
                selected = (
                    self.judgements.selected_ids
                    if self.judgements and self.judgements.selected_ids is not None
                    else set()
                )
                if not selected and self.judgements_dir.exists():
                    selected = relevance_period.load_selected_ids(self.judgements_dir)
                pool = relevance_period.precedent_pool(
                    self.raw_dir,
                    self.config.label_periods,
                    selected,
                    relevance_period.load_summaries_de(self.judgements_dir) if self.judgements_dir.exists() else {},
                    feedback_overrides=relevance_period._load_feedback_overrides(self.db_path),
                )
                self._cached_pool = pool
                return pool
            except Exception as exc:
                logger.warning("Could not build precedent pool: %s", exc)
                return []

    def status(self, date_from: str | None, date_to: str | None) -> dict[str, Any]:
        since, until = _parse_range(date_from, date_to)
        df, dt = since.isoformat(), until.isoformat()
        with self._lock:
            job = self._jobs.get((df, dt))
            job_cancel = self._job_cancels.get((df, dt))
            if job is not None and job.get("status") == "running" and (job_cancel is None or not job_cancel.is_set()):
                return dict(job)
        if self.covers(df, dt):
            return {"status": "done", "stage": "done", "from": df, "to": dt, "completed": 1, "total": 1, "message": ""}
        with self._lock:
            if job is not None:
                return dict(job)
        return {"status": "idle", "stage": "idle", "from": df, "to": dt, "completed": 0, "total": 0, "message": ""}

    def ensure_async(self, date_from: str | None, date_to: str | None) -> dict[str, Any]:
        since, until = _parse_range(date_from, date_to)
        df, dt = since.isoformat(), until.isoformat()
        if self.covers(df, dt):
            return {"status": "done", "stage": "done", "from": df, "to": dt, "completed": 1, "total": 1, "message": ""}
        with self._lock:
            existing = self._jobs.get((df, dt))
            existing_cancel = self._job_cancels.get((df, dt))
            if (
                existing is not None
                and existing["status"] == "running"
                and existing_cancel is not None
                and not existing_cancel.is_set()
            ):
                return dict(existing)
            if self._active_cancel is not None:
                self._active_cancel.set()
            cancel_event = threading.Event()
            self._active_cancel = cancel_event
            self._job_cancels[(df, dt)] = cancel_event
            covered = self._all_covered_days()
            missing_count = sum(1 for d in _iter_days(since, until) if d.isoformat() not in covered)
            job = {
                "status": "running",
                "stage": "crawling",
                "from": df,
                "to": dt,
                "completed": 0,
                "total": max(1, missing_count),
                "message": f"Lade {missing_count} fehlende Tage ({df} – {dt}) …",
                "error": None,
            }
            self._jobs[(df, dt)] = job
            self._threads = [th for th in self._threads if th.is_alive()]
            t = threading.Thread(
                target=self._run_period_job,
                args=(since, until, cancel_event),
                name=f"period-{df}-{dt}",
                daemon=True,
            )
            self._threads.append(t)
            t.start()
            return dict(job)

    def _crawl_config(self) -> dict[str, Any]:
        cfg_candidates = [
            Path("configs/crawl.json"),
            Path(__file__).resolve().parents[3] / "configs" / "crawl.json",
        ]
        cfg_path = next((p for p in cfg_candidates if p.is_file()), cfg_candidates[0])
        return crawl_mod.load_config(cfg_path, out_dir=str(self.crawl_cache_dir))

    def _existing_day_valid(self, entry: dict[str, Any]) -> bool:
        for base in (self.raw_dir, self.crawl_cache_dir):
            if base is None:
                continue
            path = crawl_mod.record_path(base, entry)
            if crawl_mod.is_valid(path):
                return True
        return False

    def _crawl_missing_days_fast(
        self,
        missing_days: list[date],
        on_progress: Callable[[int, int, str], None],
        cancel_event: threading.Event,
    ) -> tuple[list[str], set[str]]:
        """Crawl missing days into ``crawl_cache_dir`` fetching ONLY ``SOCIAL_CHAMBERS`` (never mutates raw_dir)."""
        cfg = self._crawl_config()
        list_day, fetch_doc = crawl_mod.SOURCES[cfg["source"]]
        fast_cfg = dict(cfg)
        if cfg["source"] == "entscheidsuche":
            fast_cfg["delay_seconds"] = min(float(cfg.get("delay_seconds", 0.5)), 0.15)

        raw_index = crawl_mod.load_index(self.raw_dir) if self.raw_dir.exists() else {"days": {}}
        self.crawl_cache_dir.mkdir(parents=True, exist_ok=True)
        cache_index = crawl_mod.load_index(self.crawl_cache_dir)

        failed_days: list[str] = []
        days_with_candidates: set[str] = set()
        today = date.today()
        lag_cutoff = today - timedelta(days=self.recent_empty_lag_days)
        total = len(missing_days)

        for idx, day in enumerate(missing_days, start=1):
            if cancel_event.is_set():
                raise _CancelledJob("Suche abgebrochen.")
            iso = day.isoformat()
            on_progress(idx - 1, total, f"Prüfe/lade {iso} ({idx}/{total} fehlende Tage) …")

            if iso in raw_index.get("days", {}) or iso in cache_index.get("days", {}):
                days_with_candidates.add(iso)
                on_progress(idx, total, f"Tag {iso} aus Cache ({idx}/{total}) …")
                continue

            try:
                entries = list_day(fast_cfg, day)
            except Exception as exc:
                logger.warning("Listing failed for %s: %s", iso, exc)
                failed_days.append(iso)
                continue

            social_entries = [e for e in entries if _is_social_chamber_id(e.get("case_id", ""))]
            if social_entries:
                days_with_candidates.add(iso)
            to_fetch = [e for e in social_entries if not self._existing_day_valid(e)]

            day_ok = True
            if to_fetch:
                def _download(entry: dict[str, Any]) -> None:
                    if cancel_event.is_set():
                        return
                    html, url = fetch_doc(fast_cfg, entry)
                    record = crawl_mod.build_record(fast_cfg, entry, html, url)
                    dest = crawl_mod.record_path(self.crawl_cache_dir, entry)
                    crawl_mod.write_json(dest, record)

                with ThreadPoolExecutor(max_workers=4) as pool:
                    futures = [pool.submit(_download, entry) for entry in to_fetch]
                    for fut in futures:
                        try:
                            fut.result()
                        except Exception as exc:
                            logger.warning("Fetch failed on %s: %s", iso, exc)
                            day_ok = False
                if cancel_event.is_set():
                    raise _CancelledJob("Suche abgebrochen.")

            if day_ok:
                # Never permanently cache 0-hit recent days before BGer/entscheidsuche publishes them
                if social_entries or day < lag_cutoff:
                    cache_index.setdefault("days", {})[iso] = {
                        "source": cfg["source"],
                        "judgements": len(social_entries),
                        "crawled_at": crawl_mod.now_utc(),
                    }
                    crawl_mod.write_json(self.crawl_cache_dir / "_index.json", cache_index)
            else:
                failed_days.append(iso)
            on_progress(idx, total, f"{len(social_entries)} Fachurteile für {iso} geladen ({idx}/{total})")

        return failed_days, days_with_candidates

    def _load_items_for_days(self, target_days: set[str]) -> list[CrawlItem]:
        """Read crawled judgements for ``target_days`` from both ``raw_dir`` and ``crawl_cache_dir``."""
        by_id: dict[str, CrawlItem] = {}
        months = sorted({d[:7] for d in target_days})
        for root in (self.raw_dir, self.crawl_cache_dir):
            if root is None or not root.exists():
                continue
            for month in months:
                folder = root / month
                if not folder.is_dir():
                    continue
                for item in load_crawl_items(folder):
                    d = str(item.metadata.get("decision_date") or "")
                    if d in target_days:
                        by_id[item.case_id] = item
        return list(by_id.values())

    def _already_scored_ids(self) -> set[str]:
        if self.judgements is None:
            return set()
        seen: set[str] = set()
        try:
            for r in self.judgements.rows():
                cid = r.get("case_id")
                if cid:
                    seen.add(str(cid))
                for extra_cid in (r.get("metadata") or {}).get("case_ids") or ():
                    seen.add(str(extra_cid))
        except Exception:
            pass
        return seen

    def _run_period_job(self, since: date, until: date, cancel_event: threading.Event) -> None:
        key = (since.isoformat(), until.isoformat())

        def set_job(**updates: Any) -> None:
            with self._lock:
                if self._job_cancels.get(key) is not cancel_event:
                    return
                self._jobs[key].update(updates)

        try:
            covered_days = self._all_covered_days()
            missing_days = [d for d in _iter_days(since, until) if d.isoformat() not in covered_days]
            if not missing_days:
                set_job(status="done", stage="done", completed=1, total=1, message="Zeitraum bereits im Cache.")
                return

            if self._ensure_fn is not None:
                def legacy_progress(ev: ProgressEvent) -> None:
                    if cancel_event.is_set():
                        raise _CancelledJob("Suche abgebrochen.")
                    set_job(
                        stage=ev.stage if ev.stage in ("checking", "crawling", "ingesting") else "crawling",
                        completed=ev.completed_days,
                        total=max(1, ev.total_days),
                        message=ev.message,
                    )

                ensure_res = self._ensure_fn(
                    missing_days[0],
                    missing_days[-1],
                    raw_dir=self.raw_dir,
                    summary_db_path=self.db_path,
                    on_progress=legacy_progress,
                )
                failed_raw = (
                    ensure_res.get("failed_days") if isinstance(ensure_res, dict)
                    else getattr(ensure_res, "failed_days", None)
                )
                failed_days = list(failed_raw or [])
                days_with_candidates: set[str] | None = None
            else:
                def crawl_progress(done: int, total: int, msg: str) -> None:
                    set_job(stage="crawling", completed=done, total=max(1, total), message=msg)

                failed_days, days_with_candidates = self._crawl_missing_days_fast(
                    missing_days, crawl_progress, cancel_event
                )

            if failed_days and self._ensure_fn is not None:
                raise RuntimeError(f"Crawl fehlgeschlagen für: {', '.join(failed_days[:5])}")
            if cancel_event.is_set():
                raise _CancelledJob("Suche abgebrochen.")

            failed_set = set(failed_days)
            successful_days = [d for d in missing_days if d.isoformat() not in failed_set]
            target_day_isos = {d.isoformat() for d in successful_days}
            items = self._load_items_for_days(target_day_isos) if target_day_isos else []
            already_scored = self._already_scored_ids()
            candidates = [
                i for i in items
                if relevance.is_candidate(i) and i.case_id not in already_scored
            ]

            payload: list[dict[str, Any]] = []
            scored_days: set[str] = set()
            if candidates:
                set_job(
                    stage="triaging",
                    completed=0,
                    total=len(candidates),
                    message=f"Apertus bewertet {len(candidates)} neue Fachurteile …",
                )
                client = self._client_factory()
                pool = self._precedent_pool()

                def triage_progress(done: int, total: int) -> None:
                    if cancel_event.is_set():
                        return
                    set_job(
                        stage="triaging",
                        completed=done,
                        total=max(1, total),
                        message=f"Apertus bewertet Relevanz ({done}/{total}) …",
                    )

                records = relevance.score_items(candidates, pool, client, workers=4, progress=triage_progress)
                if cancel_event.is_set():
                    raise _CancelledJob("Suche abgebrochen.")
                if all(relevance.not_asked(r) for r in records):
                    err = next((r["error"] for r in records if r.get("error")), "Gateway nicht erreichbar")
                    raise RuntimeError(f"Apertus-Triage fehlgeschlagen: {err}")

                # Do not permanently cover days whose items failed due to transport errors (not_asked)
                transport_failed_days = {
                    str(r.get("decision_date") or "")[:10]
                    for r in records
                    if relevance.not_asked(r) and r.get("decision_date")
                }
                failed_set.update(transport_failed_days)
                valid_records = [r for r in records if not relevance.not_asked(r)]
                scored_days = {str(r.get("decision_date") or "")[:10] for r in valid_records if r.get("decision_date")}

                if valid_records:
                    cal = self._load_calibration()
                    results = relevance_period.to_results(
                        valid_records,
                        cal,
                        {i.case_id: i for i in candidates},
                        list_factor=self.config.list_factor,
                        config=self.config,
                    )
                    rec_by_id = {r["case_id"]: r for r in valid_records}
                    payload = []
                    for r in results:
                        row_dict = r.to_dict()
                        src_rec = rec_by_id.get(row_dict["case_id"])
                        if src_rec and isinstance(src_rec.get("single_judge"), bool):
                            row_dict.setdefault("metadata", {}).setdefault("relevance", {})["single_judge"] = src_rec["single_judge"]
                        payload.append(row_dict)

            if cancel_event.is_set():
                raise _CancelledJob("Suche abgebrochen.")

            if payload:
                self.store_dir.mkdir(parents=True, exist_ok=True)
                stamp = f"{key[0]}_{key[1]}_{int(time.time() * 1000)}.json"
                out_path = self.store_dir / stamp
                tmp = out_path.with_name(f".{out_path.name}.tmp")
                tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
                tmp.replace(out_path)

            if self._ensure_fn is not None:
                mark_days = [d.isoformat() for d in successful_days if d.isoformat() not in failed_set]
            else:
                lag_cutoff = date.today() - timedelta(days=self.recent_empty_lag_days)
                cand_days = (days_with_candidates or set()) | scored_days
                mark_days = [
                    d.isoformat()
                    for d in successful_days
                    if d.isoformat() not in failed_set and (d < lag_cutoff or d.isoformat() in cand_days)
                ]
            self._mark_days_covered(mark_days)

            if self.backend is not None and hasattr(self.backend, "invalidate_crawl_index"):
                self.backend.invalidate_crawl_index()
            if self.judgements is not None:
                self.judgements.invalidate()

            if failed_set and not payload and failed_days:
                raise RuntimeError(f"Crawl fehlgeschlagen für: {', '.join(sorted(failed_set)[:5])}")

            set_job(
                status="done",
                stage="done",
                completed=self._jobs[key]["total"],
                message=f"{len(payload)} neue Urteile bereit.",
            )
        except _CancelledJob:
            logger.info("Cancelled period job for %s..%s", *key)
            set_job(status="idle", stage="idle", message="Abgebrochen.")
        except Exception as exc:
            logger.exception("Period job failed for %s..%s", *key)
            set_job(status="error", stage="error", error=str(exc), message=str(exc))

    def add_case(self, case_number: str, *, summarize: bool = True) -> dict[str, Any]:
        """Fetch into crawl_cache_dir, score via Apertus v1.5 and register one single judgement (#70)."""
        normalized = crawl_mod.normalize_case_id(case_number)
        record = self._fetch_case_fn(normalized, self.crawl_cache_dir)
        if self.backend is not None and hasattr(self.backend, "invalidate_crawl_index"):
            self.backend.invalidate_crawl_index()

        try:
            with closing(store.connect(self.db_path)) as conn:
                store.save_case_from_crawl(conn, record)
        except Exception:
            pass

        item = CrawlItem(
            case_id=record["case_id"],
            text=record.get("text") or "",
            language=record.get("language") or "de",
            metadata={
                "case_ids": record.get("case_ids") or [record["case_id"]],
                "decision_date": record.get("decision_date"),
                "url": record.get("url"),
                "subject": record.get("subject"),
                "department": record.get("department"),
                "division": record.get("division"),
            },
        )
        cal = self._load_calibration()
        scored_rec = relevance.score_item(item, self._precedent_pool(), self._client_factory())
        if scored_rec.get("error"):
            raise RuntimeError(f"Apertus-Bewertung fehlgeschlagen: {scored_rec['error']}")
        result = relevance_period.to_results(
            [scored_rec], cal, {item.case_id: item}, list_factor=self.config.list_factor, config=self.config
        )[0].to_dict()

        added_path = self.store_dir / "added_cases.json"
        self.store_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            existing = []
            if added_path.exists():
                try:
                    loaded = json.loads(added_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, list):
                        existing = loaded
                except (OSError, ValueError):
                    pass
            by_id = {r["case_id"]: r for r in existing if isinstance(r, dict) and r.get("case_id")}
            by_id[result["case_id"]] = result
            tmp = added_path.with_name(f".added_cases.{os.getpid()}.{threading.get_ident()}.json.tmp")
            tmp.write_text(json.dumps(list(by_id.values()), ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(added_path)

        if self.judgements is not None:
            self.judgements.invalidate()
        summarized = False
        if self.backend is not None and summarize:
            key = ui_id(result["case_id"])
            status_code, _ = self.backend.get_summary(key)
            summarized = status_code == 200
            if not summarized:
                self.backend.queue.enqueue([key], priority=True)
        return to_ui_item(
            result,
            summarized=summarized,
            outcome=_OUTCOMES.get(operative_outcome(item.text).result, "unklar"),
            single_judge=_is_single_judge(item.text),
            selected_ids=self.judgements.selected_ids if self.judgements is not None else None,
            fr_cache=self.judgements._fr_cache() if self.judgements is not None else None,
            subject_lookup=self.judgements.subject_for if self.judgements is not None else None,
        )

    def translate_reason_fr(self, case_number: str) -> dict[str, Any]:
        """Translate German explanation/question/rule into French via Apertus v1.5 (#67)."""
        if self.judgements is None:
            raise LookupError("no triage results loaded")
        target_id = ui_id(case_number)
        row = next(
            (
                r for r in self.judgements.rows()
                if ui_id(r["case_id"]) == target_id
                or any(ui_id(str(c)) == target_id for c in ((r.get("metadata") or {}).get("case_ids") or ()))
            ),
            None,
        )
        if row is None:
            raise LookupError(f"judgement {case_number} not found")
        ui_item = to_ui_item(row, fr_cache=self.judgements._fr_cache())
        reason_de = (ui_item.get("explanation") or {}).get("de") or ""
        question_de = (ui_item.get("question") or {}).get("de") or ""
        rule_de = (ui_item.get("rule") or {}).get("de") or ""
        cache_path = self.judgements.fr_cache_path or Path("data/processed/triage/fr_cache.json")
        fr = relevance.translate_reason_fr(
            self._client_factory(),
            reason_de=reason_de,
            question_de=question_de,
            rule_de=rule_de,
            cache_path=cache_path,
        )
        out: dict[str, Any] = {"explanation": {"fr": fr["reason_fr"] or reason_de}}
        if question_de:
            out["question"] = {"fr": fr["question_fr"] or question_de}
        if rule_de:
            out["rule"] = {"fr": fr["rule_fr"] or rule_de}
        return out

    def stop(self, timeout: float = 2.0) -> None:
        with self._lock:
            if self._active_cancel is not None:
                self._active_cancel.set()
            threads = list(self._threads)
        for thread in threads:
            thread.join(timeout=timeout)
