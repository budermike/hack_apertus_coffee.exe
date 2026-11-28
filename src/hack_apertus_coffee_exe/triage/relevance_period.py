"""Score a whole period with the two-pass relevance scoring and turn it into the triage output.

    python -m hack_apertus_coffee_exe.triage.cli relevance --period 2026-Q2

1. Loads the crawl records of the period and of the labelled periods before it.
2. Scores them with Apertus (``relevance.py``; cached, a second run costs nothing).
3. Fits the calibration on the labelled periods *before* the scored period only
   (no leakage) and writes ranking, traffic light and the Suva list.

The raw scores go to ``results/tables/relevance_scores_<period>.json`` so the
evaluation (``eval/relevance_eval.py``) runs without gateway and without cache.
"""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
from typing import Any, Callable, Sequence

from . import calibration
from .config import TriageConfig
from .date_filter import DateRange
from .pipeline import CrawlItem, TriageResult, load_crawl_items
from .reference_corpus import load_reference_corpus
from .relevance import (
    prompt_suffix,
    LIST_AREAS,
    ApertusRelevanceClient,
    Precedent,
    build_pool,
    is_candidate,
    not_asked,
    score_items,
)

DEFAULT_LABEL_PERIODS = ("2025-Q4", "2026-Q1")  # quarters for which Suva's selection is known
DEFAULT_TABLES_DIR = "results/tables"


class IncompleteRun(RuntimeError):
    """Apertus could not be asked for some judgements; ``run`` wrote nothing."""


def load_period_items(raw_dir: str | Path, period: DateRange) -> list[CrawlItem]:
    """Crawl records with a decision date inside ``period`` (reads only the month folders needed)."""
    items: list[CrawlItem] = []
    year, month = period.since.year, period.since.month
    while (year, month) <= (period.until.year, period.until.month):
        folder = Path(raw_dir) / f"{year:04d}-{month:02d}"
        if folder.is_dir():
            items += load_crawl_items(folder)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    since, until = period.since.isoformat(), period.until.isoformat()
    return [item for item in items if since <= str(item.metadata.get("decision_date") or "") <= until]


def load_selected_ids(judgements_dir: str | Path) -> set[str]:
    """Case numbers Suva selected (label list and human summaries); the same source as the Vector-DB uses."""
    from hack_apertus_coffee_exe.crawl.vectordb import load_reference_case_ids

    return load_reference_case_ids(judgements_dir)


def load_summaries_de(judgements_dir: str | Path) -> dict[str, str]:
    base = Path(judgements_dir)
    if not list(base.glob("Export_*.html")):
        return {}
    return {case.case_id: case.text_de for case in load_reference_corpus(base) if case.text_de}


def precedent_pool(
    raw_dir: str | Path,
    label_periods: Sequence[str],
    selected_ids: set[str],
    summaries: dict[str, str],
    feedback_overrides: dict[str, bool] | None = None,
) -> list[Precedent]:
    """Judgements with a known Suva decision: every candidate of the labelled periods, plus older selected ones."""
    ranges = [DateRange.from_period(p) for p in label_periods]
    items: list[CrawlItem] = []
    for period in ranges:
        items += [item for item in load_period_items(raw_dir, period) if is_candidate(item)]
    first = min(r.since for r in ranges).isoformat() if ranges else "9999"
    pos = {cid for cid, relevant in (feedback_overrides or {}).items() if relevant}
    neg = {cid for cid, relevant in (feedback_overrides or {}).items() if not relevant}
    effective_selected = (set(selected_ids) | pos) - neg
    seen = {cid for item in items for cid in (item.metadata.get("case_ids") or [item.case_id])}
    extra_feedback = set(feedback_overrides or ()) - seen
    for folder in sorted(Path(raw_dir).glob("20??-??")):
        if folder.name < first[:7] or extra_feedback:
            loaded = load_crawl_items(folder)
            for item in loaded:
                cids = set(item.metadata.get("case_ids") or [item.case_id])
                if folder.name < first[:7] and (cids & effective_selected) and str(item.metadata.get("decision_date") or "") < first:
                    items.append(item)
                    extra_feedback -= cids
                elif (cids & extra_feedback) and is_candidate(item):
                    items.append(item)
                    extra_feedback -= cids
    if neg:
        for item in items:
            cids = set(item.metadata.get("case_ids") or [item.case_id])
            if cids & neg:
                effective_selected -= cids
    return build_pool(items, effective_selected, summaries, feedback_ids=set(feedback_overrides or ()))


def _load_feedback_overrides() -> dict[str, bool]:
    """Latest lawyer relevance decisions from the summaries DB (#64), fail-safe if no DB exists."""
    try:
        from ..summary import store

        path = store.db_path_from_env()
        if not path.exists():
            return {}
        with closing(store.connect(path)) as conn:
            return store.feedback_examples(conn)
    except Exception:
        return {}


def public_record(record: dict[str, Any]) -> dict[str, Any]:
    """A score record without Suva's decisions on other judgements (safe for the public repository)."""
    out = dict(record)
    out["precedents"] = [{"case_id": p["case_id"], "similarity": p["similarity"]} for p in record.get("precedents", [])]
    return out


def file_suffix(prompt: str = "v1", feedback: bool = False) -> str:
    """File-name suffix of a run: ``""`` for v1, ``_v2``, plus ``_fb`` with lawyer feedback (#64)."""
    return prompt_suffix(prompt) + ("_fb" if feedback else "")


def scores_path(tables_dir: str | Path, period: DateRange, prompt: str = "v1", feedback: bool = False) -> Path:
    """``relevance_scores_<period>.json``; other runs get their suffix (``_v2``, ``_fb``)."""
    return Path(tables_dir) / f"relevance_scores_{period.label()}{file_suffix(prompt, feedback)}.json"


def score_period(
    period: DateRange,
    client: ApertusRelevanceClient,
    pool: Sequence[Precedent],
    raw_dir: str | Path = "data/raw",
    workers: int = 8,
    progress: Callable[[int, int], None] | None = None,
    prompt: str = "v1",
) -> tuple[list[dict[str, Any]], dict[str, CrawlItem]]:
    """Score every candidate of ``period``; returns the records and the crawl items by case number."""
    items = load_period_items(raw_dir, period)
    records = score_items(items, pool, client, workers=workers, progress=progress, prompt=prompt)
    return records, {item.case_id: item for item in items}


def _reason(record: dict[str, Any]) -> str:
    if record.get("error"):
        return f"Apertus-Bewertung fehlgeschlagen ({record['error']}). Bitte von Hand prüfen."
    if record["area"] not in LIST_AREAS:
        return f"Sachgebiet {record['area']}: kommt nicht auf die Suva-Liste und wird nicht bewertet."
    triage, deep = record["triage"], record.get("deep") or {}
    rule = f" Merksatz: {deep['d_merksatz']}" if deep.get("d_merksatz") else ""
    return triage["c_begruendung"] + rule


def to_results(
    records: Sequence[dict[str, Any]],
    cal: dict[str, Any],
    items: dict[str, CrawlItem] | None = None,
    list_factor: float = 1.0,
    config: TriageConfig | None = None,
) -> list[TriageResult]:
    """Ranked ``TriageResult`` list on the project's traffic-light scale.

    ``final_score`` is the relevance value mapped so that the list threshold is
    ``config.green_threshold`` and half of it ``config.yellow_threshold``; the
    colours therefore agree with ``TriageConfig.ampel_for`` and with the UI.
    """
    config = config or TriageConfig()
    t_list = calibration.list_threshold(cal, list_factor)
    t_yellow = calibration.yellow_threshold(cal, list_factor)
    scored = []
    for record in records:
        p = calibration.relevance(record, cal)
        ampel = calibration.ampel_for(p, cal, list_factor)
        score = calibration.display_score(p, t_list, config.green_threshold, config.yellow_threshold, t_yellow)
        if record.get("error"):  # a failed call must be looked at, never silently red
            ampel, score = calibration.AMPEL_YELLOW, config.yellow_threshold
        scored.append((score, p, record, ampel))
    scored.sort(key=lambda row: (-row[0], -((row[2].get("deep") or {}).get("e_score", 0)), row[2]["case_id"]))
    results = []
    for rank, (score, p, record, ampel) in enumerate(scored, start=1):
        triage, deep = record.get("triage") or {}, record.get("deep") or {}
        item = (items or {}).get(record["case_id"])
        metadata = dict(item.metadata) if item else {k: record[k] for k in ("case_ids", "decision_date", "subject")}
        metadata["relevance"] = {
            "p": p, "threshold": round(t_list, 4), "area": record["area"], "outcome": record.get("outcome"),
            "question": triage.get("a_rechtsfrage"), "criteria": triage.get("b_kriterien", []),
            "principle": deep.get("a_grundsatz"), "uv_use": deep.get("b_nutzen_uv"), "rule": deep.get("d_merksatz"),
            "deep_score": deep.get("e_score"),
        }
        if triage.get("c_begruendung_fr"):  # prompt v2: reason in French too (#67)
            metadata["relevance"]["reason_fr"] = triage["c_begruendung_fr"]
        results.append(
            TriageResult(
                case_id=record["case_id"], final_score=score, cosine_score=0.0, relevant=ampel == calibration.AMPEL_GREEN,
                relevance_reason=_reason(record), cosine_max_similarity=0.0, cosine_topk_similarity=0.0,
                apertus_score=float(triage["d_score"]) if triage else None, keyword_hits=[],
                key_topics=[c["a_id"] for c in triage.get("b_kriterien", [])],
                similar_cases=[{"case_id": x["case_id"], "similarity": x["similarity"]} for x in record.get("precedents", [])],
                metadata=metadata, language=record.get("language") or "de", embedder="none (relevance scoring)",
                llm_model=record.get("model") if triage else None, judgement_date=record.get("decision_date") or None,
                rank=rank, ampel=ampel, rank_score=score,
            )
        )
    return results


def run(
    period_name: str,
    label_periods: Sequence[str] = DEFAULT_LABEL_PERIODS,
    raw_dir: str | Path = "data/raw",
    judgements_dir: str | Path = "judgements",
    tables_dir: str | Path = DEFAULT_TABLES_DIR,
    client: ApertusRelevanceClient | None = None,
    list_factor: float = 1.0,
    workers: int = 8,
    log: Callable[[str], None] = print,
    config: TriageConfig | None = None,
    allow_failed: bool = False,
    from_scores: bool = False,
    feedback: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Score ``period_name`` and the labelled periods before it; returns records, calibration and results.

    The score files in ``tables_dir`` are tracked in git and feed the evaluation, and the
    caller writes the Suva list from the results. So when Apertus could not be asked for a
    judgement (no key, no connection, time-out), nothing is written and ``IncompleteRun`` is
    raised: a list from such a run is shorter and its calibration differs. Run again; with
    a cache only the missing judgements are asked. ``allow_failed`` writes everything anyway
    and shows these judgements in yellow.

    ``from_scores`` reads the stored score files of ``tables_dir`` instead of asking
    Apertus (no gateway, no key): for changing thresholds or the calibration only.
    The score files are then left as they are.

    Lawyer feedback (#64) is used only with ``config.feedback_learn`` (``--feedback``):
    ``feedback`` (``{case_id: relevant}``), or, if ``None``, the decisions in
    ``SUMMARY_DB_PATH``. Such a run writes its files with the suffix ``_fb``, so the
    committed results never depend on someone's local database. The calibration keeps
    Suva's own labels.
    """
    period = DateRange.from_period(period_name)
    cfg = config or TriageConfig()
    prompt, learn = cfg.relevance_prompt, cfg.feedback_learn
    client = client or ApertusRelevanceClient(cache_dir="data/processed/triage")
    selected = load_selected_ids(judgements_dir)
    overrides: dict[str, bool] = {}
    if learn:
        overrides = dict(_load_feedback_overrides() if feedback is None else feedback)
        log(f"Lawyer feedback: {sum(overrides.values())} relevant, {len(overrides) - sum(overrides.values())} not relevant")
    pool = precedent_pool(
        raw_dir,
        label_periods,
        selected,
        load_summaries_de(judgements_dir),
        feedback_overrides=overrides,
    )
    earlier = [p for p in label_periods if DateRange.from_period(p).until < period.since]
    scored = []
    for name in [*earlier, period_name]:
        rng = DateRange.from_period(name)
        if from_scores:
            path = scores_path(tables_dir, rng, prompt, learn)
            if not path.exists():
                raise FileNotFoundError(f"{path} is missing: score {name} with Apertus first (without --from-scores)")
            records = json.loads(path.read_text(encoding="utf-8"))
            items = {item.case_id: item for item in load_period_items(raw_dir, rng)}
        else:
            records, items = score_period(
                rng, client, pool, raw_dir, workers, lambda a, b, n=name: log(f"  {n}: {a}/{b}"), prompt=prompt
            )
        scored.append((name, rng, records, items))

    missing = {name: [r for r in records if not_asked(r)] for name, _, records, _ in scored}
    if any(missing.values()) and not allow_failed:
        counts = ", ".join(f"{name}: {len(rows)}" for name, rows in missing.items() if rows)
        first = next(row for rows in missing.values() for row in rows)
        raise IncompleteRun(
            f"Apertus could not be asked for some judgements ({counts}). Nothing was written. "
            "Run again; with the cache only these judgements are asked. "
            "--allow-failed (allow_failed=True) writes the results anyway and shows them in yellow. "
            f"First error ({first['case_id']}): {first['error']}"
        )

    train: list[dict[str, Any]] = []
    for name, rng, records, items in scored:
        path = scores_path(tables_dir, rng, prompt, learn)
        if not from_scores:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps([public_record(r) for r in records], ensure_ascii=False, indent=1), encoding="utf-8")
        failed = sum(bool(r.get("error")) for r in records)
        source = "read from" if from_scores else "->"
        log(f"{name}: {len(records)} candidates, {sum(r['triage'] is not None for r in records)} scored by Apertus, {failed} failed {source} {path}")
        if name != period_name:
            train += records
    out: dict[str, Any] = {"period": period, "records": records, "items": items, "calibration": None, "results": []}
    if train:
        cal = calibration.calibrate(train, selected, cfg.target_recall, cfg.yellow_recall)
        cal["periods"] = list(earlier)
        out["calibration"] = cal
        out["results"] = to_results(records, cal, items, list_factor, config)
    return out
