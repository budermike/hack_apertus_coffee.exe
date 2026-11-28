"""Dynamic period crawling/triage (#69, #91) and single-judgement add (#70) for the UI.

Overlapping periods (#96): a period job sends only judgements to Apertus that have no
valid relevance value yet (``reusable_row``); its result file holds only those. Coverage
is the union of every loaded period and the primary triage file, with overlapping and
adjacent date ranges merged (``merge_intervals``).
"""

from __future__ import annotations

from datetime import date, timedelta
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any, Callable

from ..crawl import crawl as crawl_mod
from ..crawl.ensure import ProgressEvent, ensure_period
from ..summary.judgement import operative_outcome
from ..triage import calibration, relevance, relevance_period
from ..triage.config import TriageConfig, load_config
from ..triage.date_filter import DateRange
from ..triage.llm import GatewayConfig
from ..triage.pipeline import CrawlItem
from .judgements import JudgementList, _OUTCOMES, _is_single_judge, to_ui_item, ui_id

logger = logging.getLogger(__name__)


def merge_intervals(intervals: Any) -> list[list[str]]:
    """Merge ``[from, to]`` ISO date pairs that overlap or touch (one ends the day before the next starts).

    Malformed pairs are dropped. The result is sorted and never has two pairs that could be merged.
    """
    parsed: list[tuple[date, date]] = []
    for pair in intervals or []:
        try:
            start, end = date.fromisoformat(pair[0]), date.fromisoformat(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
        if start <= end:
            parsed.append((start, end))
    merged: list[list[date]] = []
    for start, end in sorted(parsed):
        if merged and start <= merged[-1][1] + timedelta(days=1):
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [[start.isoformat(), end.isoformat()] for start, end in merged]


def reusable_row(row: dict[str, Any]) -> bool:
    """True if a stored triage result can be shown again without asking Apertus.

    That is every row with a relevance value, and rows outside the areas Apertus scores
    (they never reach Apertus). A row whose Apertus call failed (no value in a scored area)
    and rows without relevance data (older ``triage.cli score`` output) are scored again.
    """
    rel = (row.get("metadata") or {}).get("relevance")
    if not isinstance(rel, dict):
        return False
    p = rel.get("p")
    if isinstance(p, (int, float)) and not isinstance(p, bool):
        return True
    return rel.get("area") not in relevance.LIST_AREAS


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


class PeriodTriageManager:
    """Runs background crawl+triage for uncovered past periods and single-case additions."""

    def __init__(
        self,
        *,
        raw_dir: str | Path,
        store_dir: str | Path,
        judgements: JudgementList | None,
        db_path: str | Path | None = None,
        backend: Any = None,
        config: TriageConfig | None = None,
        calibration_path: str | Path = "results/tables/relevance_calibration_q2_2026.json",
        judgements_dir: str | Path = "judgements",
        model: str | None = None,
        ensure_fn: Callable[..., Any] | None = None,
        fetch_case_fn: Callable[[str, str | Path], dict[str, Any]] | None = None,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.raw_dir = Path(raw_dir)
        self.store_dir = Path(store_dir)
        self.judgements = judgements
        self.db_path = db_path
        self.backend = backend
        self.config = config or (judgements.config if judgements is not None else load_config())
        self.calibration_path = Path(calibration_path)
        self.judgements_dir = Path(judgements_dir)
        self.model = model
        self._ensure_fn = ensure_fn or ensure_period
        self._fetch_case_fn = fetch_case_fn or (lambda cid, raw: crawl_mod.fetch_single_case(cid, raw))
        self._client_factory = client_factory or (
            lambda: relevance.ApertusRelevanceClient(
                config=GatewayConfig.from_env(model=self.model or relevance.DEFAULT_MODEL),
                cache_dir=self.config.cache_dir,
            )
        )
        self._lock = threading.Lock()
        self._covered_lock = threading.Lock()
        self._jobs: dict[tuple[str, str], dict[str, Any]] = {}
        self._threads: list[threading.Thread] = []
        self._stop = False

    def _covered_file(self) -> Path:
        return self.store_dir / "_covered.json"

    def _read_covered_unlocked(self) -> list[list[str]]:
        path = self._covered_file()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [pair for pair in data if isinstance(pair, list) and len(pair) == 2]
        except (OSError, ValueError):
            return []

    def _load_covered(self) -> list[list[str]]:
        with self._covered_lock:
            return self._read_covered_unlocked()

    def _mark_covered(self, date_from: str, date_to: str) -> None:
        with self._covered_lock:
            self.store_dir.mkdir(parents=True, exist_ok=True)
            covered = merge_intervals([*self._read_covered_unlocked(), [date_from, date_to]])
            tmp = self._covered_file().with_name(f"._covered.{os.getpid()}.{threading.get_ident()}.json.tmp")
            tmp.write_text(json.dumps(covered, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._covered_file())

    def covers(self, date_from: str, date_to: str) -> bool:
        """True if ``[date_from, date_to]`` lies in the union of the loaded periods and the primary triage file."""
        intervals = list(self._load_covered())
        avail = self.judgements.available_range() if self.judgements is not None else None
        if isinstance(avail, dict) and avail.get("from") and avail.get("to"):
            intervals.append([avail["from"], avail["to"]])
        return any(start <= date_from and date_to <= end for start, end in merge_intervals(intervals))

    def _load_calibration(self) -> dict[str, Any]:
        if self.calibration_path.exists():
            data = json.loads(self.calibration_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "w" in data and "t" in data:
                return data
        return {"w": [4.0, 2.5, 0.8, -0.8], "t": 0.0445}

    def _precedent_pool(self) -> list[relevance.Precedent]:
        try:
            selected = self.judgements.selected_ids if self.judgements and self.judgements.selected_ids is not None else set()
            if not selected and self.judgements_dir.exists():
                selected = relevance_period.load_selected_ids(self.judgements_dir)
            return relevance_period.precedent_pool(
                self.raw_dir,
                self.config.label_periods,
                selected,
                relevance_period.load_summaries_de(self.judgements_dir) if self.judgements_dir.exists() else {},
                feedback_overrides=relevance_period._load_feedback_overrides(),
            )
        except Exception as exc:
            logger.warning("Could not build precedent pool: %s", exc)
            return []

    def status(self, date_from: str | None, date_to: str | None) -> dict[str, Any]:
        since, until = _parse_range(date_from, date_to)
        df, dt = since.isoformat(), until.isoformat()
        with self._lock:
            job = self._jobs.get((df, dt))
            if job is not None:
                return dict(job)
        if self.covers(df, dt):
            return {"status": "done", "stage": "done", "from": df, "to": dt, "completed": 1, "total": 1, "message": ""}
        return {"status": "idle", "stage": "idle", "from": df, "to": dt, "completed": 0, "total": 0, "message": ""}

    def ensure_async(self, date_from: str | None, date_to: str | None) -> dict[str, Any]:
        since, until = _parse_range(date_from, date_to)
        df, dt = since.isoformat(), until.isoformat()
        if self.covers(df, dt):
            return {"status": "done", "stage": "done", "from": df, "to": dt, "completed": 1, "total": 1, "message": ""}
        with self._lock:
            existing = self._jobs.get((df, dt))
            if existing is not None and existing["status"] == "running":
                return dict(existing)
            job = {
                "status": "running",
                "stage": "crawling",
                "from": df,
                "to": dt,
                "completed": 0,
                "total": max(1, (until - since).days + 1),
                "message": f"Lade Urteile ({df} – {dt}) …",
                "error": None,
            }
            self._jobs[(df, dt)] = job
            t = threading.Thread(target=self._run_period_job, args=(since, until), name=f"period-{df}-{dt}", daemon=True)
            self._threads.append(t)
            t.start()
            return dict(job)

    def _run_period_job(self, since: date, until: date) -> None:
        key = (since.isoformat(), until.isoformat())

        def on_progress(ev: ProgressEvent) -> None:
            with self._lock:
                self._jobs[key].update({
                    "stage": ev.stage if ev.stage in ("checking", "crawling", "ingesting") else "crawling",
                    "completed": ev.completed_days,
                    "total": max(1, ev.total_days),
                    "message": ev.message,
                })

        try:
            ensure_res = self._ensure_fn(
                since,
                until,
                raw_dir=self.raw_dir,
                summary_db_path=self.db_path,
                on_progress=on_progress,
            )
            failed_days = (
                ensure_res.get("failed_days") if isinstance(ensure_res, dict)
                else getattr(ensure_res, "failed_days", None)
            )
            if failed_days:
                raise RuntimeError(f"Crawl/Ingest fehlgeschlagen für: {', '.join(failed_days[:5])}")
            items = relevance_period.load_period_items(self.raw_dir, DateRange(since, until))
            # Judgements that already have a valid relevance value (primary file or an earlier
            # period) are shown as they are and not sent to Apertus again (#96).
            known = {r["case_id"] for r in self.judgements.rows() if reusable_row(r)} if self.judgements is not None else set()
            in_period = len(items)
            items = [i for i in items if i.case_id not in known]
            reused = in_period - len(items)
            candidates = [i for i in items if relevance.is_candidate(i)]
            logger.info("Period %s..%s: %d judgement(s) already triaged, %d to score", key[0], key[1], reused, len(candidates))
            with self._lock:
                self._jobs[key].update({
                    "stage": "triaging",
                    "completed": 0,
                    "total": max(1, len(candidates)),
                    "message": f"Apertus bewertet Relevanz (0/{len(candidates)}) …",
                })
            if candidates:
                client = self._client_factory()
                pool = self._precedent_pool()

                def triage_progress(done: int, total: int) -> None:
                    with self._lock:
                        self._jobs[key].update({
                            "stage": "triaging",
                            "completed": done,
                            "total": max(1, total),
                            "message": f"Apertus bewertet Relevanz ({done}/{total}) …",
                        })

                records = relevance.score_items(items, pool, client, workers=4, progress=triage_progress)
                if any(r.get("error") for r in records):
                    err = next(r["error"] for r in records if r.get("error"))
                    raise RuntimeError(f"Apertus-Triage fehlgeschlagen: {err}")
                cal = self._load_calibration()
                results = relevance_period.to_results(
                    records,
                    cal,
                    {i.case_id: i for i in items},
                    list_factor=self.config.list_factor,
                    config=self.config,
                )
                payload = [r.to_dict() for r in results]
            else:
                payload = []
            self.store_dir.mkdir(parents=True, exist_ok=True)
            out_path = self.store_dir / f"{key[0]}_{key[1]}.json"
            tmp = out_path.with_name(f".{out_path.name}.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(out_path)
            self._mark_covered(*key)
            if self.judgements is not None:
                self.judgements.invalidate()
            with self._lock:
                self._jobs[key].update({
                    "status": "done",
                    "stage": "done",
                    "completed": self._jobs[key]["total"],
                    "message": f"{len(payload)} Urteile neu bewertet, {reused} bereits vorhanden.",
                })
        except Exception as exc:
            logger.exception("Period job failed for %s..%s", *key)
            with self._lock:
                self._jobs[key].update({
                    "status": "error",
                    "stage": "error",
                    "error": str(exc),
                    "message": str(exc),
                })

    def add_case(self, case_number: str, *, summarize: bool = True) -> dict[str, Any]:
        """Fetch, score via Apertus v1.5 and register one single judgement (#70)."""
        normalized = crawl_mod.normalize_case_id(case_number)
        record = self._fetch_case_fn(normalized, self.raw_dir)
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
        )

    def translate_reason_fr(self, case_number: str) -> dict[str, Any]:
        """Translate German explanation/question/rule into French via Apertus v1.5 (#67)."""
        if self.judgements is None:
            raise LookupError("no triage results loaded")
        target_id = ui_id(case_number)
        row = next((r for r in self.judgements.rows() if ui_id(r["case_id"]) == target_id), None)
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
        self._stop = True
        with self._lock:
            threads = list(self._threads)
        for thread in threads:
            thread.join(timeout=timeout)
