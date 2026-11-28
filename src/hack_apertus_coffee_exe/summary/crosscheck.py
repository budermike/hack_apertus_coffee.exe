"""Adversarial DE <-> FR check of a summary pair, and repair of the wrong language.

DE and FR are written independently from the judgement (``generate.Summarizer``), so they can
disagree on content: the outcome, who appealed or won, an amount, a date, the legal rule. The
rule-based ``check.check_summaries`` only compares the cited articles and considerations. This
module checks whether both texts *say the same thing*:

1. ``number_conflicts`` (rule-based, reproducible): percentages, amounts and dates that
   appear in one language but not in the other.
2. ``review`` (Apertus, two passes): a reviewer whose only job is to find contradictions and
   omissions between the two texts. The second pass presents the texts in the other order, so
   a finding does not depend on which text came first; a finding of both passes is
   ``confirmed``. Structured output (JSON schema), temperature 0.
3. ``referee`` (Apertus): for each confirmed serious finding, which version matches the
   judgement (the decisive considerations and the operative part)? The judgement is the
   reference, not one of the two languages.

``crosscheck_and_repair`` rewrites the wrong language once with the finding as a note and
checks again. What is still in conflict is returned as an ``error`` issue (``de_fr_conflict``),
so ``coherence_ok`` is false and the UI shows it to the lawyer. A failing checker never blocks
a summary: it becomes a ``warning`` issue.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

from .check import Issue

logger = logging.getLogger(__name__)

TOPICS = ("ergebnis", "parteien", "zahl", "datum", "rechtsregel", "sachverhalt", "begruendung", "andere")
KINDS = ("widerspruch", "nur_de", "nur_fr")

REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["abweichungen"],
    "properties": {
        "abweichungen": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["art", "thema", "text_1", "text_2", "gewicht"],
                "properties": {
                    "art": {"type": "string", "enum": ["widerspruch", "nur_text_1", "nur_text_2"]},
                    "thema": {"type": "string", "enum": list(TOPICS)},
                    "text_1": {"type": "string"},
                    "text_2": {"type": "string"},
                    "gewicht": {"type": "string", "enum": ["hoch", "niedrig"]},
                },
            },
        }
    },
}

REFEREE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["richtig", "beleg"],
    "properties": {
        "richtig": {"type": "string", "enum": ["de", "fr", "beide", "keine"]},
        "beleg": {"type": "string"},
    },
}

REVIEW_SYSTEM = """Du prüfst zwei Fassungen derselben Urteilszusammenfassung des Bundesgerichts, eine deutsche und eine französische.
Sie müssen inhaltlich dasselbe sagen. Deine einzige Aufgabe ist, Abweichungen zu FINDEN. Sei streng und misstrauisch.

Abweichung = eine Aussage, die in der einen Fassung anders lautet als in der anderen (widerspruch), oder eine inhaltliche Aussage, die nur in einer Fassung steht (nur_text_1 / nur_text_2).
Prüfe besonders: Ergebnis (gutgeheissen / abgewiesen / teilweise / Rückweisung / nicht eingetreten), wer Beschwerde führte und wer gewann, Beträge, Prozente, Daten, Fristen, die Rechtsregel, die Begründung.

Keine Abweichung sind: andere Wortwahl, Satzstellung, Stil, Übersetzung von Fachbegriffen (UVG = LAA, Erw. = consid., Einspracheentscheid = décision sur opposition), Kürzungen ohne Inhaltsverlust.
gewicht «hoch»: Ergebnis, Parteien, Zahl, Datum, Rechtsregel, oder eine Aussage, die eine Juristin anders verstehen liesse. Sonst «niedrig».
In text_1 und text_2 zitierst du die betroffene Stelle wörtlich, höchstens 20 Wörter (leer, wenn sie in der Fassung fehlt).
Nenne höchstens 8 Abweichungen, die wichtigsten zuerst. Melde nichts zu Stil, Wortwahl oder Satzbau.
Findest du nichts, gib eine leere Liste zurück. Erfinde keine Abweichungen."""

MAX_FINDINGS = 8  # per pass; the prompt asks for the same
MAX_QUOTE_CHARS = 200

REFEREE_SYSTEM = """Du bist Schiedsrichter. Zwei Fassungen einer Urteilszusammenfassung (deutsch und französisch) widersprechen sich in einem Punkt, oder ein Punkt steht nur in einer Fassung.
Entscheide allein anhand des Urteils (Erwägungen und Dispositiv unten), welche Fassung in diesem Punkt richtig ist:
- «de»: die deutsche Fassung ist richtig (bei einer Aussage, die nur deutsch steht: sie gehört in beide Fassungen),
- «fr»: die französische Fassung ist richtig,
- «beide»: beide sind richtig, es ist kein echter Widerspruch,
- «keine»: beide sind falsch, oder die Aussage, die nur in einer Fassung steht, ist durch das Urteil nicht gedeckt.
In «beleg» schreibst du in einem Satz auf Deutsch, was das Urteil dazu sagt."""


@dataclass
class Finding:
    """One disagreement between DE and FR."""

    kind: str  # "widerspruch", "nur_de", "nur_fr"
    topic: str
    de: str
    fr: str
    serious: bool
    confirmed: bool = False
    source: str = "review"  # "review" or "numbers"
    verdict: str | None = None  # referee: "de", "fr", "beide", "keine"
    evidence: str = ""

    def describe(self) -> str:
        what = {"widerspruch": "DE und FR widersprechen sich", "nur_de": "nur DE sagt", "nur_fr": "nur FR sagt"}[self.kind]
        quote = f"DE «{self.de}»" if self.de else ""
        quote += (" / " if quote and self.fr else "") + (f"FR «{self.fr}»" if self.fr else "")
        return f"{what} ({self.topic}): {quote}" if quote else f"{what} ({self.topic})"


# ---------------------------------------------------------------- 1. numbers (rule-based)

_MONTHS = {
    "januar": 1, "janvier": 1, "februar": 2, "février": 2, "fevrier": 2, "märz": 3, "mars": 3, "april": 4, "avril": 4,
    "mai": 5, "juni": 6, "juin": 6, "juli": 7, "juillet": 7, "august": 8, "août": 8, "aout": 8,
    "september": 9, "septembre": 9, "oktober": 10, "octobre": 10, "november": 11, "novembre": 11,
    "dezember": 12, "décembre": 12, "decembre": 12,
}
_DATE_RE = re.compile(r"\b(\d{1,2})(?:\.|er)?\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})\b", re.I)
_NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_PERCENT_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s?(?:%|Prozent|pour\s?cent)", re.I)
_AMOUNT_RE = re.compile(
    r"(?:(?:Fr\.|CHF|francs?)\s?(\d[\d'’ .]*\d|\d))|(?:\b(\d[\d'’ .]*\d|\d)\s?(?:Fr\.|CHF|francs?)(?!\w))", re.I
)


def _norm_number(raw: str) -> str:
    return re.sub(r"[’' .]", "", raw).replace(",", ".").rstrip(".")


def numbers(text: str) -> dict[str, set[str]]:
    """Dates (as ISO), percentages and amounts mentioned in a summary, language-independent."""
    dates = {f"{int(y):04d}-{_MONTHS[m.lower()]:02d}-{int(d):02d}" for d, m, y in _DATE_RE.findall(text)}
    dates |= {f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for d, m, y in _NUMERIC_DATE_RE.findall(text)}
    percents = {_norm_number(p) for p in _PERCENT_RE.findall(text)}
    amounts = {_norm_number(a or b) for a, b in _AMOUNT_RE.findall(text)}
    return {"datum": dates, "prozent": percents, "betrag": amounts}


def number_conflicts(summary_de: str, summary_fr: str) -> list[Finding]:
    """Dates, percentages and amounts on which DE and FR differ.

    Different values in both languages (DE 10 %, FR 15 %) are a serious contradiction. A value
    only one language mentions is an omission and only a warning: FR may leave out a detail.
    """
    de, fr = numbers(summary_de), numbers(summary_fr)
    out = []
    for key in ("datum", "prozent", "betrag"):
        topic = "datum" if key == "datum" else "zahl"
        only_de, only_fr = sorted(de[key] - fr[key]), sorted(fr[key] - de[key])
        if only_de and only_fr:
            out.append(Finding("widerspruch", topic, f"{key} {', '.join(only_de)}", f"{key} {', '.join(only_fr)}",
                               serious=True, confirmed=True, source="numbers"))
            continue
        for value in only_de:
            out.append(Finding("nur_de", topic, f"{key} {value}", "", serious=False, confirmed=True, source="numbers"))
        for value in only_fr:
            out.append(Finding("nur_fr", topic, "", f"{key} {value}", serious=False, confirmed=True, source="numbers"))
    return out


# ---------------------------------------------------------------- 2. adversarial review (Apertus)

JsonCall = Callable[[str, str, dict[str, Any], str], dict[str, Any]]


def gateway_json_call(llm: Any, max_tokens: int = 1500) -> JsonCall:
    """Structured Apertus call through the gateway client of a ``SummaryLLM`` (temperature 0).

    An answer cut off at the token limit is not valid JSON: it is asked once more with twice the
    budget before the error is raised (the caller turns it into a warning).
    """

    def call(system: str, user: str, schema: dict[str, Any], name: str) -> dict[str, Any]:
        fmt = {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}
        error: Exception | None = None
        for budget in (max_tokens, 2 * max_tokens):
            response = llm._client.chat.completions.create(
                model=llm.model, temperature=0.0, response_format=fmt, max_tokens=budget,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            try:
                return json.loads(response.choices[0].message.content)
            except json.JSONDecodeError as exc:
                error = exc
                logger.info("cross-check %s: answer cut off at %d tokens, asking again with %d", name, budget, 2 * budget)
        raise error  # type: ignore[misc]

    return call


def _review_pass(call: JsonCall, first: tuple[str, str], second: tuple[str, str]) -> list[Finding]:
    (lang_1, text_1), (lang_2, text_2) = first, second
    user = f"=== Text 1 ({lang_1.upper()}) ===\n{text_1}\n\n=== Text 2 ({lang_2.upper()}) ===\n{text_2}\n\nListe alle Abweichungen."
    data = call(REVIEW_SYSTEM, user, REVIEW_SCHEMA, "abweichungen")
    out = []
    for item in (data.get("abweichungen") or [])[:MAX_FINDINGS]:
        kind = {"widerspruch": "widerspruch", "nur_text_1": f"nur_{lang_1}", "nur_text_2": f"nur_{lang_2}"}.get(item.get("art"))
        if kind is None:
            continue
        quotes = {lang_1: str(item.get("text_1") or "").strip()[:MAX_QUOTE_CHARS],
                  lang_2: str(item.get("text_2") or "").strip()[:MAX_QUOTE_CHARS]}
        topic = item.get("thema") if item.get("thema") in TOPICS else "andere"
        out.append(Finding(kind, topic, quotes["de"], quotes["fr"], serious=item.get("gewicht") == "hoch"))
    return out


def review(call: JsonCall, summary_de: str, summary_fr: str) -> list[Finding]:
    """Two adversarial passes (DE first, then FR first); a finding of both is ``confirmed``.

    If one pass fails, the other one still counts, but nothing is confirmed (so nothing is
    rewritten, findings are only warnings). If both fail, the error is raised.
    """
    passes = []
    for order in ((("de", summary_de), ("fr", summary_fr)), (("fr", summary_fr), ("de", summary_de))):
        try:
            passes.append(_review_pass(call, *order))
        except Exception as exc:
            logger.warning("DE/FR review pass failed: %s", exc)
            passes.append(None)
    if passes[0] is None and passes[1] is None:
        raise RuntimeError("both review passes failed")
    if passes[0] is None or passes[1] is None:
        return [f for f in (passes[0] or passes[1])]
    first, second = passes
    keys_second = {(f.kind, f.topic) for f in second}
    keys_first = {(f.kind, f.topic) for f in first}
    for f in first:
        f.confirmed = (f.kind, f.topic) in keys_second
    merged = list(first) + [f for f in second if (f.kind, f.topic) not in keys_first]
    return merged


# ---------------------------------------------------------------- 3. referee (Apertus)


def referee(call: JsonCall, finding: Finding, summary_de: str, summary_fr: str, evidence: str) -> Finding:
    """Which version matches the judgement on this point? Sets ``verdict`` and ``evidence``."""
    user = (f"Punkt: {finding.describe()}\n\n=== Deutsche Fassung ===\n{summary_de}\n\n=== Französische Fassung ===\n{summary_fr}\n\n"
            f"=== Urteil (massgebende Erwägungen und Dispositiv) ===\n{evidence}")
    data = call(REFEREE_SYSTEM, user, REFEREE_SCHEMA, "schiedsrichter")
    finding.verdict = data.get("richtig") if data.get("richtig") in ("de", "fr", "beide", "keine") else None
    finding.evidence = str(data.get("beleg") or "").strip()
    return finding


def repair_notes(finding: Finding, language: str) -> str:
    """Instruction for rewriting ``language`` so it agrees with the judgement on this point."""
    rule = f" Laut Urteil: {finding.evidence}" if finding.evidence else ""
    if language == "de":
        return f"Abweichung von der französischen Fassung – {finding.describe()}.{rule} Korrigiere die deutsche Fassung in diesem Punkt."
    return (f"Écart par rapport à la version allemande – {finding.describe()}.{rule} "
            "Corrige la version française sur ce point.")


def to_fix(finding: Finding) -> set[str]:
    """Languages to rewrite after the referee's verdict."""
    if finding.verdict == "de":
        return {"fr"}
    if finding.verdict == "fr":
        return {"de"}
    if finding.verdict == "keine":
        # a statement only one side makes and the judgement does not cover: remove it there
        return {"de"} if finding.kind == "nur_de" else {"fr"} if finding.kind == "nur_fr" else {"de", "fr"}
    return set()


def _actionable(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.serious and f.confirmed]


def crosscheck_and_repair(
    texts: dict[str, str],
    call: JsonCall,
    evidence: str,
    rewrite: Callable[[str, list[str]], str],
) -> tuple[dict[str, str], list[Issue], dict[str, Any]]:
    """Check DE against FR, let the judgement decide, rewrite the wrong language once, check again.

    ``rewrite(language, notes)`` writes that language again with the notes. Returns the texts,
    the issues for the check report and a small trace for the summary record.
    """
    trace: dict[str, Any] = {"rewritten": [], "findings": 0, "remaining": 0}
    try:
        findings = number_conflicts(texts["de"], texts["fr"]) + review(call, texts["de"], texts["fr"])
    except Exception as exc:  # the check must never block a summary
        logger.warning("DE/FR cross-check failed: %s", exc)
        return texts, [Issue("de/fr", "warning", "crosscheck_failed", f"DE/FR-Abgleich nicht möglich: {exc}")], trace
    trace["findings"] = len(findings)
    notes: dict[str, list[str]] = {"de": [], "fr": []}
    dismissed: set[tuple[str, str]] = set()  # points the referee found to be no real conflict
    for finding in _actionable(findings):
        try:
            referee(call, finding, texts["de"], texts["fr"], evidence)
        except Exception as exc:
            logger.warning("DE/FR referee failed: %s", exc)
            continue
        if finding.verdict == "beide":
            dismissed.add((finding.kind, finding.topic))
        for language in to_fix(finding):
            notes[language].append(repair_notes(finding, language))
    for language in ("de", "fr"):
        if notes[language]:
            texts = {**texts, language: rewrite(language, notes[language]).strip()}
            trace["rewritten"].append(language)

    remaining = findings
    if trace["rewritten"]:
        try:
            remaining = number_conflicts(texts["de"], texts["fr"]) + review(call, texts["de"], texts["fr"])
        except Exception as exc:
            logger.warning("DE/FR re-check failed: %s", exc)
            return texts, [Issue("de/fr", "warning", "crosscheck_failed", f"DE/FR-Abgleich nach der Korrektur nicht möglich: {exc}")], trace
    issues = []
    for finding in remaining:
        if (finding.kind, finding.topic) in dismissed:
            continue
        if not finding.serious and finding.source == "numbers":
            issues.append(Issue("de/fr", "warning", "de_fr_omission", finding.describe()))
        elif finding.serious and finding.confirmed:
            issues.append(Issue("de/fr", "error", "de_fr_conflict", finding.describe()))
        elif finding.serious:
            issues.append(Issue("de/fr", "warning", "de_fr_possible_conflict", finding.describe()))
    trace["remaining"] = sum(i.severity == "error" for i in issues)
    return texts, issues, trace
