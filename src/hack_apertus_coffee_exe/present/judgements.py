"""Judgement list for the UI: triage results in the shape of ``src/presentation/api.js``.

Read-only. Source is a triage results file written by
``python -m hack_apertus_coffee_exe.triage.cli score --output <file>``; nothing is
scored or stored here. Whether a judgement already has a summary is passed in by
the caller (the summaries DB belongs to ``summary.store``).

UI item (``GET /api/judgements``)::

    {id, caseNumber, date, url, lang, area, subject: {de, fr}, explanation: {de, fr},
     score, adjustment, summarized, outcome, court: {de, fr}, signals, topics,
     question: {de, fr}, rule: {de, fr}, criteria: [{id, at, quote, status}],
     similarCases, ampel, rank}

``adjustment`` is always 0 until lawyer feedback (#64) exists. ``outcome`` is only
set when the crawl folder is given. ``court`` is the chamber from the crawl metadata
(left out if unknown). ``question``, ``rule`` and ``criteria`` (with quotes) come from
the relevance scoring (``triage.cli relevance``, ``metadata.relevance``, #62 / #66).
Results of ``triage.cli score`` do not have them; then they are left out and the UI
shows ``signals`` (triage ``keyword_hits``) and ``topics`` (Apertus ``key_topics``)
under "Kriterien und Belege" instead. ``ampel`` and ``rank`` are extra fields the UI
may ignore.
"""

from __future__ import annotations

import json
import logging
import re
import threading
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
    "EL": r"ergänzungsleistung|prestations? complémentaires?|prestazion[ei] complementar[ei]",
    "KV": r"krankenversicherung|assurance[- ]maladie|assicurazione (?:contro le )?malattie",
    "AHV": r"\bAHV\b|\bAVS\b|alters- und hinterlassenen|assurance[- ]vieillesse|assicurazione per la vecchiaia",
    "BV": r"berufliche vorsorge|prévoyance professionnelle|previdenza professionale",
    "FZ": r"familienzulage|allocations? familiales?|assegn[oi] familiar[ei]",
    "EO": r"erwerbsersatz|perte de gain|perdita di guadagno",
    "MIET": r"\bmiete\b|mietrecht|\bbail\b|locazione",
    "SOZ": r"sozialversicherung|assurances? sociales?|assicurazion[ei] social[ei]",
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


def _area_for_row(meta: dict[str, Any]) -> str | None:
    """UI area code from subject first, falling back to a known code from ``metadata.relevance.area``."""
    code = area_code(meta.get("subject"))
    if code is not None:
        return code
    relevance = meta.get("relevance")
    rel_area = relevance.get("area") if isinstance(relevance, dict) else None
    return rel_area if isinstance(rel_area, str) and rel_area in _AREAS else None


def _date(row: dict[str, Any]) -> str | None:
    meta = row.get("metadata") or {}
    return row.get("judgement_date") or meta.get("decision_date") or meta.get("date")


def _score(row: dict[str, Any]) -> float:
    for key in ("rank_score", "final_score", "relevance_score"):
        if isinstance(row.get(key), (int, float)):
            return round(float(row[key]), 1)
    return 0.0


def _strings(value: Any) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _plain_quote(quote: str) -> str:
    """Quote without «…» around it: the UI adds the marks itself. Marks inside the quote stay."""
    quote = quote.strip()
    inner = quote[1:-1]
    if quote.startswith("«") and quote.endswith("»") and "«" not in inner and "»" not in inner:
        return inner.strip()
    return quote


def relevance_details(meta: dict[str, Any]) -> dict[str, Any]:
    """``question``, ``rule`` and ``criteria`` of the UI from ``metadata.relevance`` (#62, #66).

    Apertus writes the legal question and the practical rule in German only, like the
    reason, so DE and FR carry the same text. What is missing or malformed is left out:
    only judgements that the relevance scoring sent to Apertus have these details.
    """
    relevance = meta.get("relevance")
    if not isinstance(relevance, dict):
        return {}
    details: dict[str, Any] = {}
    for key in ("question", "rule"):
        text = relevance.get(key)
        if isinstance(text, str) and text.strip():
            details[key] = {"de": text.strip(), "fr": text.strip()}
    criteria = []
    listed = relevance.get("criteria")
    for criterion in listed if isinstance(listed, list) else []:
        if not isinstance(criterion, dict):
            continue
        criterion_id, quote, at = criterion.get("a_id"), criterion.get("c_zitat"), criterion.get("b_stelle")
        if not (isinstance(criterion_id, str) and criterion_id and isinstance(quote, str)):
            continue
        quote = _plain_quote(quote)
        if not quote:
            continue
        criteria.append({
            "id": criterion_id,
            "at": at if isinstance(at, str) else "",
            "quote": quote,
            "status": criterion.get("beleg_status"),  # "exakt" | "ungefähr" | "fehlt"
        })
    if criteria:
        details["criteria"] = criteria
    return details


def _reason_without_rule(reason: str, meta: dict[str, Any]) -> str:
    """The reason without the practical rule that the relevance scoring appends to it.

    ``relevance_period`` writes "<reason> Merksatz: <rule>" so that the ranking CSV is
    readable on its own. The UI shows the rule in its own section (``rule``), so the list
    item must not carry it twice.
    """
    relevance = meta.get("relevance")
    rule = relevance.get("rule") if isinstance(relevance, dict) else None
    cleaned_rule = rule.strip() if isinstance(rule, str) else ""
    appended = f" Merksatz: {cleaned_rule}" if cleaned_rule else None
    stripped = reason.rstrip()
    return stripped[: -len(appended)] if appended and stripped.endswith(appended) else reason


def to_ui_item(row: dict[str, Any], summarized: bool = False, outcome: str | None = None) -> dict[str, Any]:
    """One triage result -> one UI list item.

    ``subject`` ("Gegenstand" / "Objet") exists only in the language of the judgement,
    so DE and FR show the same text. The triage reason is German only; FR shows the
    same text until triage writes both.
    """
    meta = row.get("metadata") or {}
    case_id = row["case_id"]
    subject = meta.get("subject") or meta.get("title") or case_id
    reason = _reason_without_rule(row.get("relevance_reason") or row.get("reason") or "", meta)
    item = {
        "id": ui_id(case_id),
        "caseNumber": ui_case_number(case_id),
        "date": _date(row),
        "url": meta.get("url"),
        "lang": row.get("language") or meta.get("language"),
        "area": _area_for_row(meta),
        "subject": {"de": subject, "fr": subject},
        "explanation": {"de": reason, "fr": reason},
        "score": _score(row),
        "adjustment": 0,
        "summarized": summarized,
        "signals": _strings(row.get("keyword_hits")),
        "topics": _strings(row.get("key_topics")),
        "similarCases": [
            {"caseNumber": ui_case_number(c["case_id"]), "subject": c.get("title") or c["case_id"],
             "similarity": float(c.get("similarity") or 0.0)}
            for c in row.get("similar_cases") or [] if isinstance(c, dict) and c.get("case_id")
        ],
        "ampel": row.get("ampel"),
        "rank": row.get("rank"),
    }
    item.update(relevance_details(meta))
    if outcome is not None:
        item["outcome"] = outcome
    court = meta.get("department") or meta.get("division")
    if court:  # one language only, like the subject
        item["court"] = {"de": court, "fr": court}
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
        self._lock = threading.Lock()

    def outcomes(self) -> dict[str, str]:
        """UI outcome code per case id, read once from the crawl folder (empty without one)."""
        if self._outcomes is None:
            with self._lock:
                if self._outcomes is None:
                    loaded: dict[str, str] = {}
                    if self.crawl_dir is not None:
                        if not self.crawl_dir.exists() or (
                            self.crawl_dir.is_dir() and not any(self.crawl_dir.rglob("*.json"))
                        ):
                            logger.warning("Crawl folder missing or empty, leaving outcomes empty: %s", self.crawl_dir)
                        else:
                            for judgement in load_judgements(self.crawl_dir):
                                loaded[ui_id(judgement.case_id)] = _OUTCOMES.get(
                                    operative_outcome(judgement.text).result, "unklar"
                                )
                            logger.info("Read the outcome of %d judgements from %s", len(loaded), self.crawl_dir)
                    self._outcomes = loaded
        return self._outcomes

    def rows(self) -> list[dict[str, Any]]:
        with self._lock:
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
    """Placeholder for lawyer feedback (#64): accepted and logged, not stored or used yet."""
    logger.info("Feedback for %s (not stored yet, see #64): %s", case_number, body)
    return {"stored": False, "note": "feedback is not stored yet (#64)"}


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
