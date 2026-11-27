"""Judgement input for the summary stage: loading, titles, excerpts, outcome.

Input records follow ``docs/SCHEMA.md`` (crawl output in ``data/raw/<YYYY-MM>/``).
Loading reuses ``hack_apertus_coffee_exe.triage.pipeline.load_crawl_items`` so both stages read the same
shapes (crawl schema and the triage mock files).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re

from ..triage.date_filter import parse_date_string
from ..triage.pipeline import load_crawl_items

MONTHS_DE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
             "September", "Oktober", "November", "Dezember"]
MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
             "septembre", "octobre", "novembre", "décembre"]

# Section headings of BGer judgements in DE / FR / IT.
_CONSIDERATIONS_RE = re.compile(
    r"^\s*(Erwägungen|Erwägung|Considérant en droit|Considérants|Considerando in diritto|Considerandi)\s*:?\s*$",
    re.MULTILINE | re.IGNORECASE,
)
# "Demnach erkennt das Bundesgericht:", single-judge variants such as
# "Demnach erkennt die Präsidentin:" / "Demnach verfügt der Einzelrichter:",
# and the FR / IT equivalents ("Par ces motifs, …", "Per questi motivi, …").
_OPERATIVE_RE = re.compile(
    r"^\s*(?:(?:Demnach\s+)?(?:erkennt|verfügt)\b[^\n]{0,60}:|Par ces motifs\b|Per questi motivi\b)[^\n]*$",
    re.MULTILINE | re.IGNORECASE,
)
_NUMBERED_LINE_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?(?=\s|$)", re.MULTILINE)


@dataclass(frozen=True)
class Judgement:
    case_id: str
    text: str
    language: str
    decision_date: date | None = None
    url: str | None = None

    @property
    def safe_id(self) -> str:
        return self.case_id.replace("/", "_")


def _date_from_metadata(metadata: dict) -> date | None:
    for key in ("decision_date", "date", "title"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            parsed = parse_date_string(value)
            if parsed:
                return parsed
    return None


def load_judgements(input_path: str | Path) -> list[Judgement]:
    """Load all judgements from a crawl file or directory."""
    return [
        Judgement(
            case_id=item.case_id,
            text=item.text,
            language=item.language or "de",
            decision_date=_date_from_metadata(item.metadata),
            url=item.metadata.get("url"),
        )
        for item in load_crawl_items(input_path)
    ]


def same_case(a: str, b: str) -> bool:
    """``8C_7/2025`` == ``8C_7_2025``."""
    return a.replace("/", "_").strip().upper() == b.replace("/", "_").strip().upper()


def find_judgement(case_id: str, input_path: str | Path) -> Judgement:
    for judgement in load_judgements(input_path):
        if same_case(judgement.case_id, case_id):
            return judgement
    raise KeyError(f"Judgement {case_id} not found in {input_path}")


def title_de(j: Judgement) -> str:
    if not j.decision_date:
        return f"BGer-Urteil {j.case_id}"
    d = j.decision_date
    return f"BGer-Urteil {j.case_id} vom {d.day}. {MONTHS_DE[d.month - 1]} {d.year}"


def title_fr(j: Judgement) -> str:
    if not j.decision_date:
        return f"Arrêt du TF {j.case_id}"
    d = j.decision_date
    day = "1er" if d.day == 1 else str(d.day)
    return f"Arrêt du TF {j.case_id} du {day} {MONTHS_FR[d.month - 1]} {d.year}"


def consideration_numbers(text: str) -> set[str]:
    """Numbers of the considerations (``4``, ``4.4``) that exist in the judgement."""
    start = _CONSIDERATIONS_RE.search(text)
    end = _operative_match(text)
    body = text[start.end() if start else 0 : end.start() if end else len(text)]
    return {m.group(1) for m in _NUMBERED_LINE_RE.finditer(body)}


def excerpt(text: str, max_chars: int = 40000) -> str:
    """Fit a judgement into the prompt budget.

    Keeps the header and facts (at most a quarter of the budget), as much of the
    considerations as fits, and the operative part (decision) at the end.
    """
    clean = text.strip()
    if len(clean) <= max_chars:
        return clean
    cut = "\n[… gekürzt …]\n"
    cons = _CONSIDERATIONS_RE.search(clean)
    oper = _operative_match(clean)
    cons_at = cons.start() if cons else int(len(clean) * 0.2)
    oper_at = oper.start() if oper and oper.start() > cons_at else len(clean)

    tail = clean[oper_at:][: max_chars // 10]
    head = clean[:cons_at][: max_chars // 4]
    budget = max(0, max_chars - len(head) - len(tail) - 2 * len(cut))
    middle = clean[cons_at:oper_at][:budget]
    return f"{head}{cut}{middle}{cut}{tail}".strip()


def consideration_passages(text: str, numbers: list[str], max_chars: int = 6000) -> str:
    """Return the paragraphs of the given considerations (for grounding the writer)."""
    lines = text.splitlines()
    out: list[str] = []
    used = 0
    for num in numbers:
        pattern = re.compile(rf"^\s*{re.escape(num)}\.?(?=\s|$)")
        for i, line in enumerate(lines):
            if not pattern.match(line):
                continue
            block = [line.strip()]
            for nxt in lines[i + 1 :]:
                if _NUMBERED_LINE_RE.match(nxt) or _OPERATIVE_RE.match(nxt):
                    break
                block.append(nxt.strip())
            passage = " ".join(b for b in block if b)
            room = max_chars - used
            if room <= 0:
                return "\n\n".join(out)
            out.append(passage[:room])
            used += len(out[-1])
            break
    return "\n\n".join(out)


# --- outcome of the operative part ---------------------------------------

OUTCOMES = ("teilweise gutgeheissen", "gutgeheissen", "abgewiesen", "nicht eingetreten", "abgeschrieben")

_OUTCOME_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("teilweise gutgeheissen", re.compile(
        r"teilweise gutgeheissen|teilweiser Gutheissung|partiellement admis|admis partiellement"
        r"|parzialmente accolt", re.I)),
    ("abgewiesen", re.compile(r"abgewiesen|\brejet|respint", re.I)),
    ("gutgeheissen", re.compile(r"gutgeheissen|In Gutheissung|\badmis\b|accolt", re.I)),
    ("nicht eingetreten", re.compile(
        r"nicht eingetreten|irrecevable|pas entré en matière|n'entre pas en matière|inammissibil", re.I)),
    ("abgeschrieben", re.compile(
        r"abgeschrieben|rayée du rôle|stralciat|pris acte du retrait|Kenntnis genommen", re.I)),
]
_REMAND_RE = re.compile(r"zurückgewiesen|renvoy|rinviat", re.I)
# Item 1 of joined or resumed cases only says so; the outcome follows in item 2.
_JOINED_RE = re.compile(
    r"vereinigt|gemeinsam behandelt|sont jointes|sont joints|jonction|procédure est reprise"
    r"|Verfahren wird wieder aufgenommen|riunit|congiunt", re.I)
_ITEM_RE = re.compile(r"^\s*\d+\.(?:\s|$)", re.MULTILINE)


@dataclass(frozen=True)
class Outcome:
    result: str | None  # one of OUTCOMES, None if not detected
    remand: bool

    def describe_de(self) -> str:
        if not self.result:
            return "nicht erkannt"
        return self.result + (" (Rückweisung)" if self.remand else "")

    def describe_fr(self) -> str:
        names = {
            "teilweise gutgeheissen": "recours partiellement admis",
            "gutgeheissen": "recours admis",
            "abgewiesen": "recours rejeté",
            "nicht eingetreten": "recours irrecevable",
            "abgeschrieben": "cause rayée du rôle",
        }
        if not self.result:
            return "non détecté"
        return names[self.result] + (" (renvoi)" if self.remand else "")


def _operative_match(text: str) -> re.Match[str] | None:
    """The operative part ("Demnach erkennt …") is the last such heading."""
    matches = list(_OPERATIVE_RE.finditer(text))
    return matches[-1] if matches else None


def operative_outcome(text: str) -> Outcome:
    """Read the outcome from the first items of the operative part ("Demnach erkennt …")."""
    oper = _operative_match(text)
    if not oper:
        return Outcome(result=None, remand=False)
    part = text[oper.end() :]
    starts = [m.start() for m in _ITEM_RE.finditer(part)] or [0]
    items = [part[a:b] for a, b in zip(starts, starts[1:] + [min(len(part), starts[-1] + 1500)])]
    for item in items[:3]:
        if _JOINED_RE.search(item) and not any(p.search(item) for _, p in _OUTCOME_PATTERNS):
            continue  # "Die Verfahren … werden vereinigt." -> outcome is in the next item
        for name, pattern in _OUTCOME_PATTERNS:
            if pattern.search(item):
                return Outcome(result=name, remand=bool(_REMAND_RE.search(item)))
        return Outcome(result=None, remand=bool(_REMAND_RE.search(item)))
    return Outcome(result=None, remand=False)
