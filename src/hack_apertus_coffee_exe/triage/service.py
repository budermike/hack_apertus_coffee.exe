"""Triage contract (#48): the functions other modules and the backend call.

Other modules use only these, never the internals (``pipeline``, ``relevance``,
``relevance_period``). Reading functions never call the LLM; only
:func:`score_period` does.

- Stage 1 = Vector-DB similarity (``VectorDB.score_candidates``), Stage 2 = two-pass
  Apertus relevance with calibration (#62/#63). Decision in ``docs/DECISIONS.md``.
- Traffic-light thresholds (``green_threshold`` / ``yellow_threshold`` in
  ``configs/triage.json``) are derived from the data as described in #75.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
import sqlite3
from typing import Any


def _iso(value: date | str) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def period_name(date_from: date | str, date_to: date | str) -> str:
    """``2026-Q2``, ``2026-06`` or ``2026`` for a whole quarter, month or year; else ValueError.

    The relevance scoring calibrates per named period, so only whole periods are scored.
    """
    from .date_filter import DateRange

    start, end = _iso(date_from), _iso(date_to)
    year = start[:4]
    candidates = [f"{year}-Q{q}" for q in range(1, 5)] + [start[:7], year]
    for name in candidates:
        rng = DateRange.from_period(name)
        if rng.since.isoformat() == start and rng.until.isoformat() == end:
            return name
    raise ValueError(f"score_period needs a whole quarter, month or year, got {start} .. {end}")


def score_period(date_from: date | str, date_to: date | str, *, model: str | None = None) -> dict[str, Any]:
    """Crawl what is missing, then score every judgement of the period for Suva relevance.

    1. ``crawl.ensure_period`` (crawl + Vector-DB + Summaries-DB cases for missing days)
    2. ``relevance_period.run`` (#63): two Apertus passes, calibrated on the labelled
       periods in ``configs/triage.json`` (``label_periods``, ``list_factor``)

    Calls the LLM (Apertus ``model``, default from the relevance module). Returns
    ``{"period", "ensure", "candidates", "calibration", "results", "apertus_calls"}``;
    ``results`` are triage result dicts (rank, score, ampel, reason, metadata.relevance).
    Storing them in the SQLite table ``triage`` is follow-up work (#50) together with
    :func:`get_cases`.
    """
    from ..config import load_paths
    from ..crawl.ensure import ensure_period
    from . import relevance, relevance_period
    from .config import load_config
    from .llm import GatewayConfig

    name = period_name(date_from, date_to)
    paths = load_paths()
    config = load_config()
    status = ensure_period(date_from, date_to)
    client = relevance.ApertusRelevanceClient(
        config=GatewayConfig.from_env(model=model or relevance.DEFAULT_MODEL), cache_dir=config.cache_dir
    )
    out = relevance_period.run(
        name,
        label_periods=config.label_periods,
        raw_dir=paths.raw_dir,
        judgements_dir=paths.judgements_dir,
        client=client,
        list_factor=config.list_factor,
        config=config,
    )
    return {
        "period": name,
        "ensure": asdict(status),
        "candidates": len(out["records"]),
        "calibration": out["calibration"],
        "results": [r.to_dict() for r in out["results"]],
        "apertus_calls": client.calls,
    }


def get_cases(
    conn: sqlite3.Connection, date_from: date | str, date_to: date | str, *, ampel: str | None = None
) -> list[dict[str, Any]]:
    """Judgements of the period with displayed score, rank, ampel and ``summarized``.

    Reads the SQLite table ``triage`` (connection from ``summary.store.connect()``);
    never calls the LLM. Used by ``GET /api/judgements``. ``ampel`` filters by
    traffic light (``gruen`` / ``gelb`` / ``rot``).
    """
    raise NotImplementedError("get_cases: needs the SQLite table 'triage' written by score_period (#50)")


def apply_feedback(
    conn: sqlite3.Connection,
    case_id: str,
    ampel: str,
    *,
    comment: str | None = None,
    user: str,
) -> dict[str, Any]:
    """Store a lawyer's traffic-light correction and return the new score and rank.

    Writes a ``feedback`` row and a manual override (the model score is kept), marks
    the judgement as positive / negative reference in the Vector-DB
    (``VectorDB.set_reference_flags``). The new score follows the thresholds from #75.
    Never calls the LLM. Used by ``POST /api/judgements/{id}/feedback``.
    """
    raise NotImplementedError("apply_feedback: lawyer feedback (#64), thresholds from #75")
