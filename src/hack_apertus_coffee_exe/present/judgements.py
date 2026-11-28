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

import hashlib
import json
import logging
import math
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
    """``8C_7/2025`` -> ``8C_7/2025`` (bger.ch spelling with underscore, #73)."""
    return case_id


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

_SINGLE_JUDGE_RE = re.compile(r"Einzelrichter|juge unique|giudice unico", re.I)
_COMPOSITION_END_RE = re.compile(
    r"^(?:Verfahrensbeteiligte|Participants|Partecipanti|Gegenstand|Objet|Oggetto|"
    r"Sachverhalt|Faits|Fatti|Ritenuto in fatto|Erwägung|Considérant|Considerando)\b",
    re.I,
)
_RUBRUM_LAW_RE = re.compile(r"(erwägung|considérant|considerando|diritto|^vu\b|^visto\b)", re.I)
_RUBRUM_DISP_RE = re.compile(r"^(Demnach (erkennt|verfügt|beschliesst)|Par ces motifs|Per questi motivi)", re.I)


def _is_single_judge(text: str) -> bool:
    """True if single-judge composition appears in the Besetzung header (before party/subject lines)."""
    for raw_line in (text or "").split("\n"):
        para = " ".join(raw_line.split())
        if not para:
            continue
        if _COMPOSITION_END_RE.match(para) or _RUBRUM_DISP_RE.match(para):
            break
        if len(para) < 70 and para.endswith(":") and _RUBRUM_LAW_RE.search(para):
            break
        if _SINGLE_JUDGE_RE.search(para):
            return True
    return False

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


def _fr_cache_key(reason_de: str, question_de: str = "", rule_de: str = "") -> str:
    payload = json.dumps([reason_de.strip(), question_de.strip(), rule_de.strip()], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def relevance_details(meta: dict[str, Any], fr_entry: dict[str, str] | None = None) -> dict[str, Any]:
    """``question``, ``rule`` and ``criteria`` of the UI from ``metadata.relevance`` (#62, #66).

    Uses French fields from ``metadata.relevance`` or ``fr_entry`` (#67) when present, falling
    back to German so the UI shows what is available.
    """
    relevance = meta.get("relevance")
    if not isinstance(relevance, dict):
        return {}
    details: dict[str, Any] = {}
    for key in ("question", "rule"):
        text = relevance.get(key)
        if isinstance(text, str) and text.strip():
            de_text = text.strip()
            fr_raw = (fr_entry or {}).get(f"{key}_fr") or relevance.get(f"{key}_fr")
            fr_text = fr_raw.strip() if isinstance(fr_raw, str) and fr_raw.strip() else de_text
            details[key] = {"de": de_text, "fr": fr_text}
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
    for src, dst in (("principle", "principle"), ("uv_use", "uvUse")):
        val = relevance.get(src)
        if isinstance(val, int) and not isinstance(val, bool):
            details[dst] = val
    if isinstance(relevance.get("single_judge"), bool):
        details["singleJudge"] = relevance["single_judge"]
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


def to_ui_item(
    row: dict[str, Any],
    summarized: bool = False,
    outcome: str | None = None,
    *,
    feedback: dict[str, Any] | None = None,
    single_judge: bool | None = None,
    selected_ids: set[str] | None = None,
    fr_cache: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """One triage result -> one UI list item.

    ``subject`` ("Gegenstand" / "Objet") exists only in the language of the judgement,
    so DE and FR show the same text. The triage explanation/question/rule use French
    translations from metadata or ``fr_cache`` (#67) when available.
    """
    meta = row.get("metadata") or {}
    rel = meta.get("relevance") if isinstance(meta.get("relevance"), dict) else {}
    case_id = row["case_id"]
    subject = meta.get("subject") or meta.get("title") or case_id
    reason = _reason_without_rule(row.get("relevance_reason") or row.get("reason") or "", meta)
    question_de = rel.get("question") if isinstance(rel.get("question"), str) else ""
    rule_de = rel.get("rule") if isinstance(rel.get("rule"), str) else ""
    fr_entry = (fr_cache or {}).get(_fr_cache_key(reason, question_de, rule_de))
    reason_fr_raw = (
        row.get("relevance_reason_fr")
        or rel.get("reason_fr")
        or (fr_entry.get("reason_fr") if isinstance(fr_entry, dict) else None)
    )
    reason_fr = reason_fr_raw.strip() if isinstance(reason_fr_raw, str) and reason_fr_raw.strip() else reason
    raw_adj = feedback.get("adjustment") if isinstance(feedback, dict) else 0
    adjustment = (
        0 if not isinstance(raw_adj, (int, float)) or isinstance(raw_adj, bool) or raw_adj == 0
        else round(float(raw_adj), 1)
    )
    similar_cases = []
    for c in row.get("similar_cases") or []:
        if not (isinstance(c, dict) and c.get("case_id")):
            continue
        entry: dict[str, Any] = {
            "caseNumber": ui_case_number(c["case_id"]),
            "subject": c.get("title") or c["case_id"],
            "similarity": float(c.get("similarity") or 0.0),
        }
        if selected_ids is not None:
            entry["suvaSelected"] = c["case_id"] in selected_ids
        similar_cases.append(entry)
    item: dict[str, Any] = {
        "id": ui_id(case_id),
        "caseNumber": ui_case_number(case_id),
        "date": _date(row),
        "url": meta.get("url"),
        "lang": row.get("language") or meta.get("language"),
        "area": _area_for_row(meta),
        "subject": {"de": subject, "fr": subject},
        "explanation": {"de": reason, "fr": reason_fr},
        "score": _score(row),
        "adjustment": adjustment,
        "summarized": summarized,
        "signals": _strings(row.get("keyword_hits")),
        "topics": _strings(row.get("key_topics")),
        "similarCases": similar_cases,
        "ampel": row.get("ampel"),
        "rank": row.get("rank"),
    }
    if isinstance(feedback, dict) and feedback.get("userLevel") in ("high", "low"):
        item["userLevel"] = feedback["userLevel"]
    item.update(relevance_details(meta, fr_entry=fr_entry))
    if "singleJudge" not in item and single_judge is not None:
        item["singleJudge"] = single_judge
    if outcome is not None:
        item["outcome"] = outcome
    court = meta.get("department") or meta.get("division")
    if court:  # one language only, like the subject
        item["court"] = {"de": court, "fr": court}
    return item


class JudgementList:
    """Serves triage results for a date range; reloads files when they change."""

    def __init__(
        self,
        triage_path: str | Path,
        config: TriageConfig | None = None,
        summarized_ids: Callable[[], Iterable[str]] = lambda: (),
        crawl_dir: str | Path | None = None,
        feedback_map: Callable[[], dict[str, dict[str, Any]]] = lambda: {},
        selected_ids: Iterable[str] | None = None,
        extra_triage_dir: str | Path | None = None,
        fr_cache_path: str | Path | None = None,
    ) -> None:
        self.triage_path = Path(triage_path)
        self.config = config or load_config()
        self.summarized_ids = summarized_ids
        self.crawl_dir = Path(crawl_dir) if crawl_dir else None
        self.feedback_map = feedback_map
        self.selected_ids: set[str] | None = set(selected_ids) if selected_ids is not None else None
        self.extra_triage_dir = Path(extra_triage_dir) if extra_triage_dir else None
        self.fr_cache_path = Path(fr_cache_path) if fr_cache_path else None
        self._rows: list[dict[str, Any]] = []
        self._primary_rows: list[dict[str, Any]] = []
        self._mtime: float | None = None
        self._extra_mtime: float = -1.0
        self._outcomes: dict[str, str] | None = None
        self._single_judges: dict[str, bool] = {}
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        """Force reload of triage rows and crawl outcomes on the next read."""
        with self._lock:
            self._mtime = None
            self._extra_mtime = -1.0
            self._outcomes = None

    def _fr_cache(self) -> dict[str, dict[str, str]]:
        if self.fr_cache_path is None or not self.fr_cache_path.exists():
            return {}
        try:
            data = json.loads(self.fr_cache_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def outcomes(self) -> dict[str, str]:
        """UI outcome code per case id, read once from the crawl folder (empty without one)."""
        if self._outcomes is None:
            with self._lock:
                if self._outcomes is None:
                    loaded: dict[str, str] = {}
                    single_loaded: dict[str, bool] = {}
                    if self.crawl_dir is not None:
                        if not self.crawl_dir.exists() or (
                            self.crawl_dir.is_dir()
                            and not any(
                                p.is_file() and not p.name.startswith("_")
                                for p in self.crawl_dir.rglob("*.json")
                            )
                        ):
                            logger.warning("Crawl folder missing or empty, leaving outcomes empty: %s", self.crawl_dir)
                        else:
                            for judgement in load_judgements(self.crawl_dir):
                                key = ui_id(judgement.case_id)
                                loaded[key] = _OUTCOMES.get(
                                    operative_outcome(judgement.text).result, "unklar"
                                )
                                single_loaded[key] = _is_single_judge(judgement.text)
                            logger.info("Read the outcome of %d judgements from %s", len(loaded), self.crawl_dir)
                    self._single_judges = single_loaded
                    self._outcomes = loaded
        return self._outcomes

    def single_judges(self) -> dict[str, bool]:
        """Single-judge flag per UI id, populated once alongside ``outcomes()``."""
        self.outcomes()
        return self._single_judges

    def _extra_files(self) -> list[Path]:
        if self.extra_triage_dir is None or not self.extra_triage_dir.exists():
            return []
        return sorted(p for p in self.extra_triage_dir.glob("*.json") if not p.name.startswith("_"))

    def rows(self) -> list[dict[str, Any]]:
        with self._lock:
            mtime = self.triage_path.stat().st_mtime
            extra_files = self._extra_files()
            extra_mtime = max((p.stat().st_mtime for p in extra_files), default=0.0)
            if mtime != self._mtime or extra_mtime != self._extra_mtime:
                data = json.loads(self.triage_path.read_text(encoding="utf-8"))
                if not isinstance(data, list):
                    raise ValueError(f"{self.triage_path}: expected a JSON list of triage results")
                by_id: dict[str, dict[str, Any]] = {r["case_id"]: r for r in data if isinstance(r, dict) and r.get("case_id")}
                for extra_path in extra_files:
                    extra = json.loads(extra_path.read_text(encoding="utf-8"))
                    if isinstance(extra, list):
                        for row in extra:
                            if isinstance(row, dict) and row.get("case_id"):
                                by_id[row["case_id"]] = row
                self._primary_rows = [r for r in data if isinstance(r, dict) and r.get("case_id")]
                self._rows = list(by_id.values())
                self._mtime, self._extra_mtime = mtime, extra_mtime
                logger.info("Loaded %d triage results from %s", len(self._rows), self.triage_path)
            return self._rows

    def available_range(self) -> dict[str, str] | None:
        """Min/max ISO dates across the primary triage file (single added cases do not inflate coverage)."""
        self.rows()
        dates = [d for row in self._primary_rows if (d := _date(row))]
        return {"from": min(dates), "to": max(dates)} if dates else None

    def covers(self, date_from: str | None, date_to: str | None) -> bool:
        """True if ``[date_from, date_to]`` lies within the primary triaged date span."""
        if not date_from or not date_to:
            self.rows()
            return bool(self._primary_rows)
        avail = self.available_range()
        return bool(avail and avail["from"] <= date_from and date_to <= avail["to"])

    def items(self, date_from: str | None = None, date_to: str | None = None) -> list[dict[str, Any]]:
        """UI items with ``date_from <= date <= date_to`` (ISO dates, both optional), best score first."""
        done = {ui_id(c) for c in self.summarized_ids()}
        outcomes = self.outcomes() if self.crawl_dir is not None else {}
        single_judges = self.single_judges() if self.crawl_dir is not None else {}
        feedback = self.feedback_map()
        fr_cache = self._fr_cache()
        out = []
        for row in self.rows():
            date = _date(row) or ""
            if date_from and date < date_from:
                continue
            if date_to and date > date_to:
                continue
            key = ui_id(row["case_id"])
            out.append(
                to_ui_item(
                    row,
                    summarized=key in done,
                    outcome=outcomes.get(key),
                    feedback=feedback.get(key),
                    single_judge=single_judges.get(key),
                    selected_ids=self.selected_ids,
                    fr_cache=fr_cache,
                )
            )
        return sorted(out, key=lambda item: item["score"], reverse=True)

    def thresholds(self) -> dict[str, float]:
        """Traffic-light thresholds from ``configs/triage.json`` (UI: high / medium)."""
        return {"high": self.config.green_threshold, "medium": self.config.yellow_threshold}


def parse_feedback_body(body: dict[str, Any]) -> dict[str, Any]:
    """Validate and coerce ``POST /api/judgements/{id}/feedback`` payload; raises ValueError on invalid types."""
    if not isinstance(body, dict):
        raise ValueError("feedback body must be a JSON object")
    decision = body.get("decision")
    if decision not in (None, "high", "low"):
        raise ValueError(f"decision must be 'high', 'low', or null, got {decision!r}")
    level = body.get("level")
    if level is None:
        level = "medium" if decision is None else decision
    if level not in ("high", "medium", "low"):
        raise ValueError(f"level must be 'high', 'medium', or 'low', got {level!r}")

    raw_adj = body.get("adjustment", 0.0)
    if raw_adj is None:
        adjustment = 0.0
    elif isinstance(raw_adj, bool):
        raise ValueError("adjustment must be a finite number, not bool")
    else:
        try:
            adjustment = float(raw_adj)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid adjustment: {raw_adj!r}") from exc
        if not math.isfinite(adjustment):
            raise ValueError(f"adjustment must be finite: {raw_adj!r}")

    raw_dir = body.get("direction", 0)
    if raw_dir is None:
        direction = 0
    elif isinstance(raw_dir, bool) or isinstance(raw_dir, float):
        raise ValueError(f"invalid direction: {raw_dir!r}")
    else:
        try:
            direction = int(raw_dir)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid direction: {raw_dir!r}") from exc

    comment = body.get("comment")
    if comment is not None and not isinstance(comment, str):
        raise ValueError("comment must be a string or null")
    user = body.get("user")
    if user is not None and not isinstance(user, str):
        raise ValueError("user must be a string or null")

    return {
        "decision": decision,
        "level": level,
        "adjustment": adjustment,
        "direction": direction,
        "comment": comment,
        "user": user or "ui",
    }


def record_feedback(
    case_number: str, body: dict[str, Any], db_path: str | Path | None = None
) -> dict[str, Any]:
    """Store lawyer feedback (#64) in SQLite when ``db_path`` is provided, else log placeholder."""
    if db_path is None:
        logger.info("Feedback for %s (not stored yet, see #64): %s", case_number, body)
        return {"stored": False, "note": "feedback is not stored yet (#64)"}
    from contextlib import closing
    from ..summary import store

    parsed = parse_feedback_body(body)
    with closing(store.connect(db_path)) as conn:
        return store.save_feedback(conn, case_number, **parsed)


def list_response(judgements: JudgementList | None, query: dict[str, list[str]]) -> tuple[int, Any]:
    """``GET /api/judgements?from=…&to=…`` as (HTTP status, body)."""
    if judgements is None:
        return 503, {"error": "no triage results loaded: start the server with --triage <file>"}
    first = lambda key: (query.get(key) or [None])[0]  # noqa: E731
    return 200, judgements.items(first("from"), first("to"))


def config_response(judgements: JudgementList | None) -> tuple[int, Any]:
    """``GET /api/config``: settings the UI needs (traffic-light thresholds & available range)."""
    if judgements is not None:
        body: dict[str, Any] = {"thresholds": judgements.thresholds()}
        avail = judgements.available_range() if hasattr(judgements, "available_range") else None
        if isinstance(avail, dict):
            body["availableRange"] = avail
        return 200, body
    config = load_config()
    return 200, {"thresholds": {"high": config.green_threshold, "medium": config.yellow_threshold}}
