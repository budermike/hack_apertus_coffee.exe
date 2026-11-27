"""Consistency check after each generation (issue #16).

Rule-based, no extra model call, so the result is reproducible:
- every cited consideration (E. / Erw. / consid.) exists in the judgement,
- every cited article (of a known law) appears in the judgement,
- the closing paragraph does not contradict the operative part,
- DE and FR cite the same articles and considerations.

``error`` issues trigger one regeneration of that language; ``warning`` issues
are only shown in the UI.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re

from .judgement import Judgement, Outcome, consideration_numbers

# FR / IT abbreviations mapped to the DE abbreviation, so DE, FR and the
# judgement (in any language) can be compared.
LAW_ALIASES = {
    "UVG": "UVG", "LAA": "UVG", "LAINF": "UVG",
    "UVV": "UVV", "OLAA": "UVV", "OAINF": "UVV",
    "ATSG": "ATSG", "LPGA": "ATSG",
    "ATSV": "ATSV", "OPGA": "ATSV",
    "BGG": "BGG", "LTF": "BGG",
    "IVG": "IVG", "LAI": "IVG",
    "IVV": "IVV", "RAI": "IVV", "OAI": "IVV",
    "AHVG": "AHVG", "LAVS": "AHVG",
    "BVG": "BVG", "LPP": "BVG",
    "KVG": "KVG", "LAMAL": "KVG",
    "MVG": "MVG", "LAM": "MVG",
    "EMRK": "EMRK", "CEDH": "EMRK", "CEDU": "EMRK",
    "BV": "BV", "CST": "BV", "COST": "BV",
    "ZGB": "ZGB", "CC": "ZGB",
    "OR": "OR", "CO": "OR",
    "VWVG": "VwVG", "PA": "VwVG",
}

_ARTICLE_RE = re.compile(
    r"\b[Aa]rt(?:icolo|icle)?s?\.?\s*(\d+[a-z]?)"
    r"((?:\s*(?:Abs|al|cpv|lit|let|Bst|Ziff|ch|n|par|para)\.?\s*\d*[a-z]?)*)"
    r"\s+([A-Za-zÄÖÜäöü]{2,6})\.?\b"
)
_CITE_RE = re.compile(r"\b(?:(?:E|Erw|consid|cons)\.|Erwägung(?:en)?|considérants?)\s*([\d.,\s]+(?:(?:und|et|e|bis|-|–)\s*[\d.]+)*)")
_NUM_RE = re.compile(r"\d+(?:\.\d+)*")

# Outcome words in the closing paragraph of a summary.
_SUMMARY_OUTCOME = {
    "gutgeheissen": re.compile(r"gutgeheissen|hiess\b[^.]*\bgut\b|admis\b|admet\b", re.I),
    "abgewiesen": re.compile(r"abgewiesen|wies\b[^.]*\bab\b|rejet", re.I),
    "nicht eingetreten": re.compile(r"nicht ein(?:ge)?treten|trat\b[^.]*\bnicht ein|irrecevable|pas entré en matière", re.I),
}
_PARTIAL = re.compile(r"teilweise|partiellement", re.I)


@dataclass
class Issue:
    language: str  # "de", "fr" or "de/fr"
    severity: str  # "error" or "warning"
    kind: str
    detail: str


@dataclass
class CheckReport:
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def errors_for(self, language: str) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error" and i.language == language]

    def to_dicts(self) -> list[dict]:
        return [asdict(i) for i in self.issues]


def cited_articles(text: str) -> set[tuple[str, str]]:
    """``{("6", "UVG"), ("44", "ATSG")}``; articles of unknown laws are ignored."""
    found = set()
    for m in _ARTICLE_RE.finditer(text):
        law = LAW_ALIASES.get(m.group(3).upper())
        if law:
            found.add((m.group(1), law))
    return found


def cited_considerations(text: str) -> set[str]:
    nums: set[str] = set()
    for m in _CITE_RE.finditer(text):
        nums.update(n.rstrip(".") for n in _NUM_RE.findall(m.group(1)))
    return nums


def _closing_paragraph(text: str) -> str:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return paras[-1] if paras else ""


def check_language(summary: str, language: str, judgement: Judgement, outcome: Outcome) -> list[Issue]:
    issues: list[Issue] = []
    words = len(summary.split())
    if words == 0:
        return [Issue(language, "error", "empty", "Zusammenfassung ist leer.")]
    if words > 700:
        issues.append(Issue(language, "warning", "length", f"{words} Wörter (Suva-Beispiele: meist 100–350)."))
    if re.search(r"^\s*(#|[-*•]\s)", summary, re.MULTILINE):
        issues.append(Issue(language, "warning", "format", "Überschriften oder Aufzählungszeichen statt Fliesstext."))

    known = consideration_numbers(judgement.text)
    if known:
        for num in sorted(cited_considerations(summary) - known):
            issues.append(Issue(language, "error", "consideration", f"Erwägung {num} kommt im Urteil nicht vor."))

    in_judgement = cited_articles(judgement.text)
    for num, law in sorted(cited_articles(summary) - in_judgement):
        issues.append(Issue(language, "error", "article", f"Art. {num} {law} kommt im Urteil nicht vor."))

    if outcome.result in ("gutgeheissen", "abgewiesen", "nicht eingetreten", "teilweise gutgeheissen"):
        closing = _closing_paragraph(summary)
        actual = "gutgeheissen" if outcome.result == "teilweise gutgeheissen" else outcome.result
        mentioned = {name for name, rx in _SUMMARY_OUTCOME.items() if rx.search(closing)}
        if mentioned and actual not in mentioned:
            issues.append(Issue(
                language, "error", "outcome",
                f"Schlussabsatz nennt {', '.join(sorted(mentioned))}, Dispositiv: {outcome.describe_de()}.",
            ))
        elif outcome.result == "teilweise gutgeheissen" and actual in mentioned and not _PARTIAL.search(closing):
            issues.append(Issue(language, "warning", "outcome", "Dispositiv: teilweise gutgeheissen, Schlussabsatz sagt nicht «teilweise»."))
    return issues


def check_summaries(summary_de: str, summary_fr: str, judgement: Judgement, outcome: Outcome) -> CheckReport:
    report = CheckReport()
    report.issues += check_language(summary_de, "de", judgement, outcome)
    report.issues += check_language(summary_fr, "fr", judgement, outcome)

    art_de, art_fr = cited_articles(summary_de), cited_articles(summary_fr)
    if art_de != art_fr:
        diff = sorted(art_de ^ art_fr)
        report.issues.append(Issue("de/fr", "warning", "article_mismatch",
                                   "DE und FR zitieren verschiedene Artikel: " + ", ".join(f"Art. {n} {l}" for n, l in diff)))
    cons_de, cons_fr = cited_considerations(summary_de), cited_considerations(summary_fr)
    if cons_de != cons_fr:
        report.issues.append(Issue("de/fr", "warning", "consideration_mismatch",
                                   "DE und FR zitieren verschiedene Erwägungen: " + ", ".join(sorted(cons_de ^ cons_fr))))
    return report
