"""Judgement list for the UI: triage results in the shape of ``src/presentation/api.js``.

Read-only. Source is a triage results file written by
``python -m hack_apertus_coffee_exe.triage.cli score --output <file>``; nothing is
scored or stored here. Whether a judgement already has a summary is passed in by
the caller (the summaries DB belongs to ``summary.store``).

UI item (``GET /api/judgements``)::

    {id, caseNumber, date, url, lang, area, subject: {de, fr}, explanation: {de, fr},
     score, adjustment, summarized, outcome, similarCases, ampel, rank}

``adjustment`` is always 0 until lawyer feedback (#40) exists. ``outcome`` is only
set when the crawl folder is given. ``question``, ``rule`` and ``criteria`` need
Apertus output that triage does not produce yet, so they are left out (the UI hides
those sections). ``ampel`` and ``rank`` are extra fields the UI may ignore.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Iterable

from ..summary.judgement import load_judgements, operative_outcome
from ..triage.config import TriageConfig, load_config

logger = logging.getLogger(__name__)


def ui_id(case_id: str) -> str:
    """``8C_7/2025`` -> ``8C_7_2025`` (URL-safe id used by the UI)."""
    return case_id.replace("/", "_")


def ui_case_number(case_id: str) -> str:
    """``8C_7/2025`` -> ``8C 7/2025`` (display form used by the UI)."""
    return case_id.replace("_", " ", 1)


# Legal area codes of the UI (AREA_NAMES in src/presentation/i18n.js), matched on the
# subject of the judgement in DE / FR / IT. The earliest match in the subject wins:
# "Assurance-accidents (rente d'invalidité)" is UV, not IV.
_AREAS = {
    "UV": r"unfallversicherung|assurance[- ]accidents?|assicurazione contro gli infortuni|\bUVG\b|\bLAA\b",
    "IV": r"invalidenversicherung|assurance[- ]invalidité|assicurazione per l'invalidità|\bIVG\b|\bLAI\b",
    "MV": r"militärversicherung|assurance militaire|assicurazione militare",
    "ALV": r"arbeitslosenversicherung|assurance[- ]chômage|assicurazione contro la disoccupazione",
    "EL": r"ergänzungsleistung|prestations? complémentaires?|prestazioni complementari",
    "KV": r"krankenversicherung|assurance[- ]maladie|assicurazione malattie",
    "AHV": r"\bAHV\b|\bAVS\b|alters- und hinterlassenen|assurance[- ]vieillesse",
    "BV": r"berufliche vorsorge|prévoyance professionnelle|previdenza professionale",
    "FZ": r"familienzulage|allocations? familiales?|assegni familiari",
    "EO": r"erwerbsersatz|perte de gain|perdita di guadagno",
    "MIET": r"\bmiete\b|mietrecht|\bbail\b|locazione",
    "SOZ": r"sozialversicherung|assurances? sociales?|assicurazioni sociali",
}
_AREA_RES = {code: re.compile(pattern, re.I) for code, pattern in _AREAS.items()}

# Outcome codes of the UI (OUTCOME_NAMES in i18n.js) for summary.judgement.operative_outcome.
_OUTCOMES = {
    "abgewiesen": "abgewiesen",
    "gutgeheissen": "gutgeheissen",
    "teilweise gutgeheissen": "teilweise",
    "nicht eingetreten": "nichteintreten",
    "abgeschrieben": "andere",
    None: "unklar",
}


def area_code(subject: str | None) -> str | None:
    """UI area code for a subject such as "Unfallversicherung (Kausalzusammenhang)", None if unknown."""
    if not subject:
        return None
    hits = {code: m.start() for code, rx in _AREA_RES.items() if (m := rx.search(subject))}
    return min(hits, key=hits.get) if hits else None


def _date(row: dict[str, Any]) -> str | None:
    meta = row.get("metadata") or {}
    return row.get("judgement_date") or meta.get("decision_date") or meta.get("date")


def _score(row: dict[str, Any]) -> float:
    for key in ("rank_score", "final_score", "relevance_score"):
        if isinstance(row.get(key), (int, float)):
            return round(float(row[key]), 1)
    return 0.0


def to_ui_item(row: dict[str, Any], summarized: bool = False, outcome: str | None = None) -> dict[str, Any]:
    """One triage result -> one UI list item.

    ``subject`` ("Gegenstand" / "Objet") exists only in the language of the judgement,
    so DE and FR show the same text. The triage reason is German only; FR shows the
    same text until triage writes both.
    """
    meta = row.get("metadata") or {}
    case_id = row["case_id"]
    subject = meta.get("subject") or meta.get("title") or case_id
    reason = row.get("relevance_reason") or row.get("reason") or ""
    item = {
        "id": ui_id(case_id),
        "caseNumber": ui_case_number(case_id),
        "date": _date(row),
        "url": meta.get("url"),
        "lang": row.get("language") or meta.get("language"),
        "area": area_code(meta.get("subject")),
        "subject": {"de": subject, "fr": subject},
        "explanation": {"de": reason, "fr": reason},
        "score": _score(row),
        "adjustment": 0,
        "summarized": summarized,
        "similarCases": [
            {"caseNumber": ui_case_number(c["case_id"]), "subject": c.get("title") or c["case_id"],
             "similarity": float(c.get("similarity") or 0.0)}
            for c in row.get("similar_cases") or [] if isinstance(c, dict) and c.get("case_id")
        ],
        "ampel": row.get("ampel"),
        "rank": row.get("rank"),
    }
    if outcome is not None:
        item["outcome"] = outcome
    return item


class JudgementList:
    """Serves triage results for a date range; reloads the file when it changes."""

    def __init__(
        self,
        triage_path: str | Path,
        config: TriageConfig | None = None,
        summarized_ids: Callable[[], Iterable[str]] = lambda: (),
        crawl_dir: str | Path | None = None,
    ) -> None:
        self.triage_path = Path(triage_path)
        self.config = config or load_config()
        self.summarized_ids = summarized_ids
        self.crawl_dir = Path(crawl_dir) if crawl_dir else None
        self._rows: list[dict[str, Any]] = []
        self._mtime: float | None = None
        self._outcomes: dict[str, str] | None = None

    def outcomes(self) -> dict[str, str]:
        """UI outcome code per case id, read once from the crawl folder (empty without one)."""
        if self._outcomes is None:
            self._outcomes = {}
            if self.crawl_dir is not None:
                for judgement in load_judgements(self.crawl_dir):
                    self._outcomes[ui_id(judgement.case_id)] = _OUTCOMES.get(
                        operative_outcome(judgement.text).result, "unklar"
                    )
                logger.info("Read the outcome of %d judgements from %s", len(self._outcomes), self.crawl_dir)
        return self._outcomes

    def rows(self) -> list[dict[str, Any]]:
        mtime = self.triage_path.stat().st_mtime
        if mtime != self._mtime:
            data = json.loads(self.triage_path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError(f"{self.triage_path}: expected a JSON list of triage results")
            self._rows, self._mtime = data, mtime
            logger.info("Loaded %d triage results from %s", len(data), self.triage_path)
        return self._rows

    def items(self, date_from: str | None = None, date_to: str | None = None) -> list[dict[str, Any]]:
        """UI items with ``date_from <= date <= date_to`` (ISO dates, both optional), best score first."""
        done = {ui_id(c) for c in self.summarized_ids()}
        outcomes = self.outcomes() if self.crawl_dir is not None else {}
        out = []
        for row in self.rows():
            date = _date(row) or ""
            if date_from and date < date_from:
                continue
            if date_to and date > date_to:
                continue
            key = ui_id(row["case_id"])
            out.append(to_ui_item(row, summarized=key in done, outcome=outcomes.get(key)))
        return sorted(out, key=lambda item: item["score"], reverse=True)

    def thresholds(self) -> dict[str, float]:
        """Traffic-light thresholds from ``configs/triage.json`` (UI: high / medium)."""
        return {"high": self.config.green_threshold, "medium": self.config.yellow_threshold}


def record_feedback(case_number: str, body: dict[str, Any]) -> dict[str, Any]:
    """Placeholder for lawyer feedback (#40): accepted and logged, not stored or used yet."""
    logger.info("Feedback for %s (not stored yet, see #40): %s", case_number, body)
    return {"stored": False, "note": "feedback is not stored yet (#40)"}


def list_response(judgements: JudgementList | None, query: dict[str, list[str]]) -> tuple[int, Any]:
    """``GET /api/judgements?from=…&to=…`` as (HTTP status, body)."""
    if judgements is None:
        return 503, {"error": "no triage results loaded: start the server with --triage <file>"}
    first = lambda key: (query.get(key) or [None])[0]  # noqa: E731
    return 200, judgements.items(first("from"), first("to"))


def config_response(judgements: JudgementList | None) -> tuple[int, Any]:
    """``GET /api/config``: settings the UI needs (traffic-light thresholds)."""
    if judgements is not None:
        return 200, {"thresholds": judgements.thresholds()}
    config = load_config()
    return 200, {"thresholds": {"high": config.green_threshold, "medium": config.yellow_threshold}}
