"""Judgement list for the UI: triage results in the shape of ``src/presentation/api.js``.

Read-only. Serves pre-scored triage JSON files (including pre-computed
``relevance_scores_*.json`` when ``tables_dir`` is provided), applies ``FeedbackLearner``
(#29), and resolves per-case outcomes, single-judge flags, and similar-case subjects lazily
in O(1) without scanning thousands of crawl files upfront.

UI item (``GET /api/judgements``)::

    {id, caseNumber, date, url, lang, area, subject: {de, fr}, explanation: {de, fr},
     score, baseScore, adjustment, userLevel, summarized, outcome, court: {de, fr}, signals,
     topics, question: {de, fr}, rule: {de, fr}, criteria: [{id, at, quote, status}],
     similarCases, ampel, rank, scored}

Lawyer feedback (#64): ``userLevel`` is the lawyer's own decision on this judgement, and it is
final for it: ``score`` is then at least the green threshold ("high") or below the yellow one
("low"), ``ampel`` follows (``feedback_learning.apply_decision``). With a
``learner`` (``triage.feedback_learning``, the way the server runs) the decisions are training
examples: ``score`` is the relevance after learning from all of them, ``baseScore`` the stored
one, and ``adjustment`` is always 0 (nothing is added to a single score any more). Without a
learner ``adjustment`` is the stored manual correction, as before.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

from ..summary.judgement import load_judgements, operative_outcome
from ..triage import calibration
from ..triage import relevance as relevance_scoring
from ..triage.config import TriageConfig, load_config
from ..triage.feedback_learning import apply_decision
from ..triage.date_filter import DateRange

logger = logging.getLogger(__name__)

_QUARTER_SCORE_FILES: tuple[tuple[str, str], ...] = (
    ("2025-Q4", "relevance_scores_q4_2025.json"),
    ("2026-Q1", "relevance_scores_q1_2026.json"),
    ("2026-Q2", "relevance_scores_q2_2026.json"),
)


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
_VALID_REL_OUTCOMES = {"abgewiesen", "gutgeheissen", "teilweise", "nichteintreten", "andere", "unklar"}


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
    return relevance_scoring.fr_cache_key(reason_de, question_de, rule_de)


def relevance_details(
    meta: dict[str, Any],
    fr_entry: dict[str, str] | None = None,
    locate: Callable[[str, str], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """``question``, ``rule`` and ``criteria`` of the UI from ``metadata.relevance`` (#62, #66).

    A quote Apertus did not copy word for word (``status`` other than ``exakt``) gets
    ``passage``: the wording of the judgement it renders, with its mark, looked up in the
    stored text by ``locate(quote, at)`` (``relevance.locate_passage``). Without ``passage``
    the judgement has no passage close to the quote.
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
        entry = {
            "id": criterion_id,
            "at": at if isinstance(at, str) else "",
            "quote": quote,
            "status": criterion.get("beleg_status"),  # "exakt" | "ungefähr" | "fehlt"
        }
        if entry["status"] != "exakt" and locate is not None:
            passage = locate(quote, entry["at"])
            if passage:
                entry["passage"] = passage
        criteria.append(entry)
    if criteria:
        details["criteria"] = criteria
    for src, dst in (("principle", "principle"), ("uv_use", "uvUse")):
        val = relevance.get(src)
        if isinstance(val, int) and not isinstance(val, bool):
            details[dst] = val
    if isinstance(relevance.get("single_judge"), bool):
        details["singleJudge"] = relevance["single_judge"]
    return details


_RULE_MARK = "Merksatz:"


def _reason_without_rule(reason: str, meta: dict[str, Any]) -> str:
    """The reason without the practical rule that the relevance scoring appends to it.

    «Warum relevant» and «Merksatz für die Praxis» are two sections of the UI: the rule is
    shown in its own, so it is cut off here (compared without regard to spaces).
    """
    relevance = meta.get("relevance")
    rule = relevance.get("rule") if isinstance(relevance, dict) else None
    cleaned_rule = " ".join(rule.split()) if isinstance(rule, str) else ""
    head, mark, tail = reason.rpartition(_RULE_MARK)
    if cleaned_rule and mark and " ".join(tail.split()) == cleaned_rule:
        return head.rstrip()
    return reason


def to_ui_item(
    row: dict[str, Any],
    summarized: bool = False,
    outcome: str | None = None,
    *,
    feedback: dict[str, Any] | None = None,
    single_judge: bool | None = None,
    selected_ids: set[str] | None = None,
    fr_cache: dict[str, dict[str, str]] | None = None,
    learned: Any | None = None,
    config: TriageConfig | None = None,
    subject_lookup: Callable[[str], str | None] | None = None,
    locate: Callable[[str, str], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """One triage result -> one UI list item."""
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
    raw_adj = feedback.get("adjustment") if isinstance(feedback, dict) and learned is None else 0
    adjustment = (
        0 if not isinstance(raw_adj, (int, float)) or isinstance(raw_adj, bool) or raw_adj == 0
        else round(float(raw_adj), 1)
    )
    base_score = _score(row)
    learned_score = learned.score(row) if learned is not None and learned.active else None
    learned_ampel = learned.ampel(row) if learned_score is not None else None
    similar_cases = []
    for c in row.get("similar_cases") or []:
        if not (isinstance(c, dict) and c.get("case_id")):
            continue
        cid = c["case_id"]
        sim_subject = c.get("subject") or c.get("title")
        if not sim_subject and subject_lookup is not None:
            sim_subject = subject_lookup(cid)
        entry: dict[str, Any] = {
            "caseNumber": ui_case_number(cid),
            "subject": sim_subject or cid,
            "similarity": float(c.get("similarity") or 0.0),
        }
        if selected_ids is not None:
            entry["suvaSelected"] = cid in selected_ids
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
        "score": base_score if learned_score is None else round(float(learned_score), 1),
        "baseScore": base_score,
        "adjustment": adjustment,
        "summarized": summarized,
        "signals": _strings(row.get("keyword_hits")),
        "topics": _strings(row.get("key_topics")),
        "similarCases": similar_cases,
        "ampel": learned_ampel or row.get("ampel"),
        "rank": row.get("rank"),
    }
    case_ids = meta.get("case_ids")
    if isinstance(case_ids, list) and len(case_ids) > 1:
        item["caseIds"] = [str(x) for x in case_ids if isinstance(x, str)]
    if isinstance(feedback, dict) and feedback.get("userLevel") in ("high", "low"):
        item["userLevel"] = feedback["userLevel"]
        # The decision is final for this judgement: relevant = at least green, not relevant = red.
        # Learning (above) moves the judgements that resemble it; baseScore keeps the stored value.
        green, yellow = (config.green_threshold, config.yellow_threshold) if config else (60.0, 40.0)
        item["score"] = round(apply_decision(item["score"], feedback["userLevel"] == "high", green, yellow), 1)
        item["ampel"] = "gruen" if item["score"] >= green else "rot"
    if isinstance(rel, dict) and "p" in rel:
        # False: Apertus gave this judgement no relevance value (area outside the scoring, or a
        # failed call). A lawyer's decision on it stands, but there is nothing to learn from.
        item["scored"] = isinstance(rel["p"], (int, float)) and not isinstance(rel["p"], bool)
    item.update(relevance_details(meta, fr_entry=fr_entry, locate=locate))
    if "singleJudge" not in item and single_judge is not None:
        item["singleJudge"] = single_judge
    if outcome is not None:
        item["outcome"] = outcome
    court = meta.get("department") or meta.get("division")
    if court:
        item["court"] = {"de": court, "fr": court}
    return item


def reusable_row(row: dict[str, Any]) -> bool:
    """True if a stored triage result can be shown again without asking Apertus (#96).

    That is every row with a relevance value, and rows outside the areas Apertus scores
    (they never reach Apertus). A row whose Apertus call failed (no value in a scored area)
    and rows without relevance data (older ``triage.cli score`` output) are scored again.
    """
    from ..triage.relevance import LIST_AREAS

    rel = (row.get("metadata") or {}).get("relevance")
    if not isinstance(rel, dict):
        return False
    p = rel.get("p")
    if isinstance(p, (int, float)) and not isinstance(p, bool):
        return True
    return rel.get("area") not in LIST_AREAS


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
        learner: Any | None = None,
        tables_dir: str | Path | None = None,
        extra_crawl_dir: str | Path | None = None,
        calibration_path: str | Path | None = None,
    ) -> None:
        self.learner = learner  # triage.feedback_learning.FeedbackLearner, or None: no learning
        self.calibration_path = Path(calibration_path) if calibration_path else None  # why the thresholds sit where they do
        self.triage_path = Path(triage_path)
        self.config = config or load_config()
        self.summarized_ids = summarized_ids
        self.crawl_dir = Path(crawl_dir) if crawl_dir else None
        self.extra_crawl_dir = Path(extra_crawl_dir) if extra_crawl_dir else None
        self.feedback_map = feedback_map
        self.selected_ids: set[str] | None = set(selected_ids) if selected_ids is not None else None
        self.extra_triage_dir = Path(extra_triage_dir) if extra_triage_dir else None
        self.fr_cache_path = Path(fr_cache_path) if fr_cache_path else None
        self.tables_dir = Path(tables_dir) if tables_dir else None
        self._rows: list[dict[str, Any]] = []
        self._primary_rows: list[dict[str, Any]] = []
        self._covered_quarters: list[tuple[str, str]] = []
        self._mtime: int | None = None
        self._extra_sig: tuple[tuple[str, int, int], ...] | None = None
        self._outcomes: dict[str, str] | None = None
        self._single_judges: dict[str, bool] = {}
        self._file_map: dict[str, Path] | None = None
        self._header_cache: dict[str, tuple[str | None, str | None, str | None]] = {}
        self._text_meta_cache: dict[str, tuple[str | None, bool | None]] = {}
        self._subjects: dict[str, str] = {}
        self._segments_cache: dict[str, list[dict[str, str]] | None] = {}
        self._passage_cache: dict[tuple[str, str, str], dict[str, Any] | None] = {}
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        """Reload triage rows and filename index on next read (keeps immutable per-case text cache)."""
        with self._lock:
            self._mtime = None
            self._extra_sig = None
            self._file_map = None
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
        """UI outcome code per case id across the crawl folder (``items()`` uses lazy O(1) lookup)."""
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
                            try:
                                for judgement in load_judgements(self.crawl_dir):
                                    key = ui_id(judgement.case_id)
                                    loaded[key] = _OUTCOMES.get(
                                        operative_outcome(judgement.text).result, "unklar"
                                    )
                                    single_loaded[key] = _is_single_judge(judgement.text)
                                logger.info("Read the outcome of %d judgements from %s", len(loaded), self.crawl_dir)
                            except (OSError, ValueError) as exc:
                                logger.warning("Could not load outcomes from %s: %s", self.crawl_dir, exc)
                    self._single_judges = single_loaded
                    self._outcomes = loaded
        return self._outcomes

    def single_judges(self) -> dict[str, bool]:
        """Single-judge flag per UI id, populated alongside ``outcomes()``."""
        self.outcomes()
        return self._single_judges

    def _ensure_file_map(self) -> dict[str, Path]:
        if self._file_map is not None:
            return self._file_map
        with self._lock:
            if self._file_map is not None:
                return self._file_map
            fmap: dict[str, Path] = {}
            for root in (self.crawl_dir, self.extra_crawl_dir):
                if root is None or not root.exists():
                    continue
                if root.is_file():
                    fmap[root.stem] = root
                    continue
                for path in root.rglob("*.json"):
                    if path.name.startswith((".", "_")):
                        continue
                    stem = path.stem
                    safe_key = (
                        stem[11:]
                        if len(stem) > 11 and stem[4] == "-" and stem[7] == "-" and stem[10] == "_"
                        else stem
                    )
                    fmap[safe_key] = path
            self._file_map = fmap
            return fmap

    def _lookup_header(self, key: str) -> tuple[str | None, str | None, str | None]:
        """Lazy O(1) lookup of ``(subject, url, department)`` from JSON header without full-text parsing."""
        cached = self._header_cache.get(key)
        if cached is not None:
            return cached
        if self.crawl_dir is None and self.extra_crawl_dir is None:
            return (None, None, None)
        path = self._ensure_file_map().get(key)
        subject: str | None = None
        url: str | None = None
        department: str | None = None
        if path is not None and path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    if raw.get("subject"):
                        subject = str(raw["subject"])
                    if raw.get("url"):
                        url = str(raw["url"])
                    if raw.get("department") or raw.get("division"):
                        department = str(raw.get("department") or raw.get("division"))
            except Exception:
                pass
        res = (subject, url, department)
        self._header_cache[key] = res
        return res

    def _lookup_text_meta(self, key: str) -> tuple[str | None, bool | None]:
        """Lazy O(1) lookup of ``(outcome, single_judge)`` for a single UI id."""
        cached = self._text_meta_cache.get(key)
        if cached is not None:
            return cached
        if self._outcomes is not None:
            res = (self._outcomes.get(key), self._single_judges.get(key))
            self._text_meta_cache[key] = res
            return res
        if self.crawl_dir is None and self.extra_crawl_dir is None:
            return (None, None)

        path = self._ensure_file_map().get(key)
        outcome: str | None = None
        single_judge: bool | None = None
        if path is not None and path.exists():
            try:
                for judgement in load_judgements(path):
                    if ui_id(judgement.case_id) == key or any(ui_id(c) == key for c in judgement.case_ids):
                        outcome = _OUTCOMES.get(operative_outcome(judgement.text).result, "unklar")
                        single_judge = _is_single_judge(judgement.text)
                        break
            except Exception:
                pass
        res = (outcome, single_judge)
        self._text_meta_cache[key] = res
        return res

    def segments_for(self, key: str) -> list[dict[str, str]] | None:
        """Citable paragraphs of the stored judgement text (``relevance.segment``), None if it is not stored."""
        if key in self._segments_cache:
            return self._segments_cache[key]
        segs: list[dict[str, str]] | None = None
        path = self._ensure_file_map().get(key) if self.crawl_dir or self.extra_crawl_dir else None
        if path is not None and path.exists():
            try:
                for judgement in load_judgements(path):
                    if ui_id(judgement.case_id) == key or any(ui_id(c) == key for c in judgement.case_ids):
                        segs = relevance_scoring.segment(judgement.text)["segs"]
                        break
            except Exception:  # an unreadable file: the quotes are shown without the passage
                segs = None
        if segs is not None:  # a judgement that is not stored yet may be crawled later
            self._segments_cache[key] = segs
        return segs

    def passage_for(self, key: str, quote: str, at: str) -> dict[str, Any] | None:
        """``relevance.locate_passage`` for one quote of the judgement ``key``; found once, then kept."""
        cache_key = (key, quote, at)
        if cache_key not in self._passage_cache:
            segs = self.segments_for(key)
            if segs is None:  # the text is not stored (yet): asked again next time
                return None
            self._passage_cache[cache_key] = relevance_scoring.locate_passage(quote, at, segs)
        return self._passage_cache[cache_key]

    def subject_for(self, case_id: str) -> str | None:
        """Fast subject lookup for ``similarCases`` without scanning all files."""
        subj = self._subjects.get(case_id)
        if subj:
            return subj
        crawled_subj, _, _ = self._lookup_header(ui_id(case_id))
        if crawled_subj:
            self._subjects[case_id] = crawled_subj
            return crawled_subj
        return None

    def _extra_files(self) -> list[Path]:
        if self.extra_triage_dir is None or not self.extra_triage_dir.exists():
            return []
        return sorted(
            p for p in self.extra_triage_dir.glob("*.json")
            if not p.name.startswith(("_", ".")) and p.name != "covered_days.json"
        )

    def _load_tables_quarters(self, by_id: dict[str, dict[str, Any]]) -> list[tuple[str, str]]:
        """Load pre-scored ``relevance_scores_*.json`` quarters in ~12 ms without scanning crawl files."""
        if self.tables_dir is None or not self.tables_dir.is_dir():
            return []
        cal_path = (
            self.calibration_path
            if self.calibration_path is not None and self.calibration_path.is_file()
            else self.tables_dir / "relevance_calibration_q2_2026.json"
        )
        cal: dict[str, Any] = {"w": [4.0, 2.5, 0.8, -0.8], "t": 0.1484}
        if cal_path.is_file():
            try:
                loaded_cal = json.loads(cal_path.read_text(encoding="utf-8"))
                if isinstance(loaded_cal, dict) and "w" in loaded_cal and "t" in loaded_cal:
                    cal = loaded_cal
            except (OSError, ValueError):
                pass

        from ..triage import relevance_period

        covered: list[tuple[str, str]] = []
        for period_name, filename in _QUARTER_SCORE_FILES:
            score_file = self.tables_dir / filename
            if not score_file.is_file():
                continue
            try:
                records = json.loads(score_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(records, list) or not records:
                continue
            rng = DateRange.from_period(period_name)
            covered.append((rng.since.isoformat(), rng.until.isoformat()))

            missing_records = []
            missing_by_id: dict[str, dict[str, Any]] = {}
            for rec in records:
                if not isinstance(rec, dict) or not rec.get("case_id"):
                    continue
                cid = rec["case_id"]
                if rec.get("subject"):
                    self._subjects[cid] = str(rec["subject"])
                for p in rec.get("precedents") or []:
                    if isinstance(p, dict) and p.get("case_id") and p.get("subject"):
                        self._subjects[p["case_id"]] = str(p["subject"])
                existing = by_id.get(cid)
                if existing is not None:
                    meta = existing.setdefault("metadata", {})
                    rel = meta.get("relevance")
                    if isinstance(rel, dict):
                        if "single_judge" not in rel and isinstance(rec.get("single_judge"), bool):
                            rel["single_judge"] = rec["single_judge"]
                        if not rel.get("outcome") and rec.get("outcome"):
                            rel["outcome"] = rec["outcome"]
                else:
                    missing_records.append(rec)
                    missing_by_id[cid] = rec

            if missing_records:
                converted = relevance_period.to_results(
                    missing_records,
                    cal,
                    list_factor=self.config.list_factor,
                    config=self.config,
                )
                for tr in converted:
                    row_dict = tr.to_dict()
                    src_rec = missing_by_id.get(row_dict["case_id"])
                    if src_rec and isinstance(src_rec.get("single_judge"), bool):
                        row_dict.setdefault("metadata", {}).setdefault("relevance", {})["single_judge"] = src_rec["single_judge"]
                    by_id[row_dict["case_id"]] = row_dict
        return covered

    def rows(self) -> list[dict[str, Any]]:
        with self._lock:
            mtime = self.triage_path.stat().st_mtime_ns
            extra_files = self._extra_files()
            extra_sig = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in extra_files)
            if mtime != self._mtime or extra_sig != self._extra_sig:
                data = json.loads(self.triage_path.read_text(encoding="utf-8"))
                if not isinstance(data, list):
                    raise ValueError(f"{self.triage_path}: expected a JSON list of triage results")
                primary = [r for r in data if isinstance(r, dict) and r.get("case_id")]
                by_id: dict[str, dict[str, Any]] = {}
                for r in primary:
                    by_id[r["case_id"]] = r
                    subj = (r.get("metadata") or {}).get("subject")
                    if isinstance(subj, str) and subj:
                        self._subjects[r["case_id"]] = subj

                covered = self._load_tables_quarters(by_id)
                # The primary file and the pre-scored quarters are the evaluated runs: a period
                # file may add judgements and re-score failed ones, never replace a valid row (#96).
                protected = {cid for cid, r in by_id.items() if reusable_row(r)}
                for extra_path in extra_files:
                    try:
                        extra = json.loads(extra_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError) as exc:
                        logger.warning("Skipping unreadable triage file %s: %s", extra_path, exc)
                        continue
                    if isinstance(extra, list):
                        for row in extra:
                            if not (isinstance(row, dict) and row.get("case_id")) or row["case_id"] in protected:
                                continue
                            known = by_id.get(row["case_id"])
                            if known is not None and reusable_row(known) and not reusable_row(row):
                                continue  # a failed call never hides a valid score, whatever the file order
                            by_id[row["case_id"]] = row
                            subj = (row.get("metadata") or {}).get("subject")
                            if isinstance(subj, str) and subj:
                                self._subjects[row["case_id"]] = subj
                self._primary_rows = primary
                self._covered_quarters = covered
                self._rows = list(by_id.values())
                self._mtime, self._extra_sig = mtime, extra_sig
                logger.info("Loaded %d triage results (primary: %d)", len(self._rows), len(primary))
            return self._rows

    def available_range(self) -> dict[str, str] | None:
        """Min/max ISO dates across the primary triage file (single added cases do not inflate coverage)."""
        self.rows()
        dates = [d for row in self._primary_rows if (d := _date(row))]
        return {"from": min(dates), "to": max(dates)} if dates else None

    def covered_ranges(self) -> list[tuple[str, str]]:
        """All pre-scored date intervals loaded in memory (pre-scored quarters + primary triage)."""
        self.rows()
        ranges = list(self._covered_quarters)
        avail = self.available_range()
        if avail:
            pair = (avail["from"], avail["to"])
            if pair not in ranges:
                ranges.append(pair)
        return ranges

    def covers(self, date_from: str | None, date_to: str | None) -> bool:
        """True if ``[date_from, date_to]`` lies within the merged pre-scored date intervals."""
        if not date_from or not date_to:
            self.rows()
            return bool(self._rows)
        ranges = self.covered_ranges()
        if not ranges:
            return False
        try:
            req_s, req_e = date.fromisoformat(date_from), date.fromisoformat(date_to)
        except ValueError:
            return False
        parsed = sorted((date.fromisoformat(s), date.fromisoformat(e)) for s, e in ranges)
        merged: list[tuple[date, date]] = []
        for s, e in parsed:
            if merged and s <= merged[-1][1] + timedelta(days=1):
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        return any(s <= req_s and req_e <= e for s, e in merged)

    def learned(self, feedback: dict[str, dict[str, Any]] | None = None) -> Any | None:
        """What was learned from the lawyers' decisions on all loaded judgements (``None`` without a learner)."""
        if self.learner is None:
            return None
        feedback = self.feedback_map() if feedback is None else feedback
        decisions = {
            key: entry["userLevel"] == "high"
            for key, entry in feedback.items()
            if isinstance(entry, dict) and entry.get("userLevel") in ("high", "low")
        }
        return self.learner.learned(decisions, self.rows())

    def items(self, date_from: str | None = None, date_to: str | None = None) -> list[dict[str, Any]]:
        """UI items with ``date_from <= date <= date_to`` (ISO dates, both optional), best score first."""
        done = {ui_id(c) for c in self.summarized_ids()}
        feedback = self.feedback_map()
        learned = self.learned(feedback)
        fr_cache = self._fr_cache()
        has_crawl = self.crawl_dir is not None or self.extra_crawl_dir is not None
        out = []
        for row in self.rows():
            d = _date(row) or ""
            if date_from and d < date_from:
                continue
            if date_to and d > date_to:
                continue
            key = ui_id(row["case_id"])
            meta = row.get("metadata") or {}
            rel = meta.get("relevance") if isinstance(meta.get("relevance"), dict) else {}

            rel_outcome = rel.get("outcome")
            outcome = rel_outcome if isinstance(rel_outcome, str) and rel_outcome in _VALID_REL_OUTCOMES else None
            single_judge = rel.get("single_judge") if isinstance(rel.get("single_judge"), bool) else None

            row_for_ui = row
            if has_crawl and (outcome is None or single_judge is None):
                c_out, c_sj = self._lookup_text_meta(key)
                if outcome is None and c_out is not None:
                    outcome = c_out
                if single_judge is None and c_sj is not None:
                    single_judge = c_sj
            if has_crawl and (not meta.get("url") or (not meta.get("department") and not meta.get("division"))):
                _, c_url, c_dept = self._lookup_header(key)
                if (c_url and not meta.get("url")) or (c_dept and not meta.get("department") and not meta.get("division")):
                    enriched_meta = dict(meta)
                    if c_url and not enriched_meta.get("url"):
                        enriched_meta["url"] = c_url
                    if c_dept and not enriched_meta.get("department") and not enriched_meta.get("division"):
                        enriched_meta["department"] = c_dept
                    row_for_ui = {**row, "metadata": enriched_meta}

            fb = feedback.get(key)
            if fb is None:
                for cid in meta.get("case_ids") or ():
                    fb = feedback.get(ui_id(str(cid)))
                    if fb is not None:
                        break
            is_done = key in done or any(ui_id(str(cid)) in done for cid in (meta.get("case_ids") or ()))
            out.append(
                to_ui_item(
                    row_for_ui,
                    summarized=is_done,
                    outcome=outcome,
                    feedback=fb,
                    single_judge=single_judge,
                    selected_ids=self.selected_ids,
                    fr_cache=fr_cache,
                    learned=learned,
                    config=self.config,
                    subject_lookup=self.subject_for if has_crawl or self._subjects else None,
                    locate=(lambda quote, at, key=key: self.passage_for(key, quote, at)) if has_crawl else None,
                )
            )
        return sorted(out, key=lambda item: item["score"], reverse=True)

    def thresholds(self) -> dict[str, float]:
        """Traffic-light thresholds from ``configs/triage.json`` (UI: high / medium)."""
        return {"high": self.config.green_threshold, "medium": self.config.yellow_threshold}

    def threshold_basis(self) -> dict[str, list[str]] | None:
        """Why the thresholds sit where they do, per UI language (``calibration.describe``); ``None`` without a calibration file."""
        if self.calibration_path is None or not self.calibration_path.exists():
            return None
        try:
            cal = json.loads(self.calibration_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("calibration file %s could not be read", self.calibration_path)
            return None
        if not isinstance(cal, dict):
            return None
        green, yellow = self.config.green_threshold, self.config.yellow_threshold
        texts = {lang: calibration.describe(cal, lang, green, yellow) for lang in ("de", "fr")}
        return texts if texts["de"] else None


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
        basis = judgements.threshold_basis() if hasattr(judgements, "threshold_basis") else None
        if isinstance(basis, dict) and basis:  # plain-language reasons for the thresholds, from the calibration (#75)
            body["thresholdBasis"] = basis
        avail = judgements.available_range() if hasattr(judgements, "available_range") else None
        if isinstance(avail, dict):
            body["availableRange"] = avail
        learned = judgements.learned() if hasattr(judgements, "learned") else None
        learning = learned.summary() if learned is not None else None
        if isinstance(learning, dict):
            body["learning"] = learning
        return 200, body
    config = load_config()
    return 200, {"thresholds": {"high": config.green_threshold, "medium": config.yellow_threshold}}
