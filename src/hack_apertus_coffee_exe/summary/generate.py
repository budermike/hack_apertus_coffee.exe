"""Summary generation with Apertus (issues #15, #16, #65).

``Summarizer`` (default, Apertus 70B via ``SUMMARY_MODEL``, prompts in ``prompts/``):
1. core question and the 1-3 decisive considerations (structured JSON),
2. DE and FR text written from the wording of those considerations, with a rule-based block on
   who appealed and who won and Suva examples marked as other cases,
3. one rewrite on faults (length, one paragraph, wrong outcome/remand/article, invented considerations).

``SummarizerV1`` (older, prompts in ``prompts/v1/``): facts JSON -> DE -> FR -> check and retry.
Both offer ``align_edit`` (#65, ``align.py``): carry a lawyer's edit of one language over to the other
(content only, no translation), and the older ``align_language`` (rewrite one language on an instruction).
Comparison and choice: ``notebooks/summary/02_modelle_prompts.ipynb``.
"""

from __future__ import annotations

import json
import re

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .align import Alignment, align_edit
from .check import CheckReport, check_summaries
from .examples import ExampleSelector, StyleExamples
from .judgement import (
    Judgement,
    consideration_numbers,
    consideration_passages,
    excerpt,
    operative_outcome,
    outcome_block,
    title_de,
    title_fr,
)
from .llm import SummaryLLM, parse_json_object
from . import prompts
from ..triage.reference_corpus import ReferenceCase

LANGUAGES = ("de", "fr")


def _num_key(num: str) -> tuple[int, ...]:
    return tuple(int(p) for p in num.split(".") if p.isdigit())


def _drop_unknown_considerations(facts: dict[str, Any], known: set[str]) -> None:
    """Remove consideration numbers the facts step invented, so the writer cannot copy them."""
    if not known:
        return
    for item in facts.get("kernerwaegungen", []) or []:
        if isinstance(item, dict) and str(item.get("erwaegung", "")).strip().rstrip(".") not in known:
            item["erwaegung"] = ""


@dataclass
class SummaryResult:
    case_id: str
    title_de: str
    title_fr: str
    summary_de: str
    summary_fr: str
    facts: dict[str, Any]
    outcome: str
    examples: list[str]
    model: str
    prompt_version: str
    check: CheckReport
    attempts: dict[str, int] = field(default_factory=dict)

    @property
    def coherence_ok(self) -> bool:
        return self.check.ok

    def text(self, language: str) -> str:
        return self.summary_de if language == "de" else self.summary_fr

    def to_dict(self) -> dict[str, Any]:
        """Everything a store or the UI needs, JSON-serialisable.

        ``coherence_notes`` holds the remaining check findings (empty when ok).
        """
        return {
            "case_id": self.case_id,
            "title_de": self.title_de,
            "title_fr": self.title_fr,
            "summary_de": self.summary_de,
            "summary_fr": self.summary_fr,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "coherence_ok": self.coherence_ok,
            "coherence_notes": self.check.to_dicts(),
            "outcome": self.outcome,
            "facts": self.facts,
            "examples": self.examples,
            "attempts": self.attempts,
        }


class _AlignLanguage:
    """``align_edit`` and ``align_language`` (#65) for both pipelines; needs ``self.llm`` and ``self.prompts_dir``."""

    def align_language(
        self,
        *,
        target_lang: str,
        target_text: str,
        source_lang: str | None = None,
        source_text: str | None = None,
        instruction: str | None = None,
        facts: dict[str, Any] | None = None,
    ) -> str:
        """Align or refine one summary language using Apertus v1.5 (#65)."""
        if target_lang not in LANGUAGES:
            raise ValueError(f"language must be one of {LANGUAGES}, got {target_lang!r}")
        user = prompts.align_user(
            target_language=target_lang,
            target_text=target_text,
            source_language=source_lang,
            source_text=source_text,
            instruction=instruction,
            facts=facts,
        )
        return self.llm.chat(prompts.align_system(target_lang, self.prompts_dir), user).strip()

    def align_edit(self, **kwargs: Any) -> Alignment:
        """Carry a lawyer's edit of one language over to the other one: content only, no
        translation, the judgement as reference, notes from a closing check (``align.align_edit``)."""
        return align_edit(self.llm, prompts_dir=self.prompts_dir, **kwargs)


class SummarizerV1(_AlignLanguage):
    """Older pipeline (facts JSON -> DE -> FR, prompts in ``prompts/v1/``), kept for comparison
    (``notebooks/summary/``) and as ``run.summarizer_factory(pipeline="v1")``."""

    def __init__(
        self,
        llm: SummaryLLM,
        selector: ExampleSelector | None = None,
        n_examples: int = 2,
        max_judgement_chars: int = 40000,
        prompts_dir: Path = prompts.PROMPTS_DIR_V1,
    ) -> None:
        self.llm = llm
        self.selector = selector or ExampleSelector()
        self.n_examples = n_examples
        self.max_judgement_chars = max_judgement_chars
        self.prompts_dir = prompts_dir

    def extract_facts(self, judgement: Judgement) -> dict[str, Any]:
        raw = self.llm.chat(
            prompts.FACTS_SYSTEM,
            prompts.facts_user(judgement.case_id, excerpt(judgement.text, self.max_judgement_chars)),
        )
        try:
            facts = parse_json_object(raw)
        except ValueError:
            # Keep going: the writer still gets the passages and the outcome.
            facts = {"hinweis": "Fakten konnten nicht als JSON gelesen werden", "rohtext": raw[:4000]}
        _drop_unknown_considerations(facts, consideration_numbers(judgement.text))
        facts["case_id"] = judgement.case_id
        if judgement.decision_date:
            facts["urteilsdatum"] = judgement.decision_date.isoformat()
        return facts

    def write(
        self,
        judgement: Judgement,
        language: str,
        facts: dict[str, Any],
        examples: Sequence,
        fix_notes: Sequence[str] = (),
    ) -> str:
        outcome = operative_outcome(judgement.text)
        known = sorted(consideration_numbers(judgement.text), key=_num_key)
        cited = [str(k.get("erwaegung", "")).strip() for k in facts.get("kernerwaegungen", []) if isinstance(k, dict)]
        passages = consideration_passages(judgement.text, [c for c in cited if c in known])
        user = prompts.write_user(
            language=language,
            title=title_de(judgement) if language == "de" else title_fr(judgement),
            facts=facts,
            outcome_text=outcome.describe_de() if language == "de" else outcome.describe_fr(),
            considerations=known,
            passages=passages,
            examples=examples,
            fix_notes=fix_notes,
        )
        return self.llm.chat(prompts.write_system(language, self.prompts_dir), user)

    def summarize(
        self,
        judgement: Judgement,
        similar_case_ids: Sequence[str] = (),
        exclude_ids: Sequence[str] = (),
    ) -> SummaryResult:
        examples = self.selector.select(
            judgement, k=self.n_examples, preferred_ids=similar_case_ids, exclude_ids=exclude_ids
        )
        facts = self.extract_facts(judgement)
        outcome = operative_outcome(judgement.text)
        texts = {lang: self.write(judgement, lang, facts, examples) for lang in LANGUAGES}
        attempts = {lang: 1 for lang in LANGUAGES}

        report = check_summaries(texts["de"], texts["fr"], judgement, outcome)
        for lang in LANGUAGES:
            errors = report.errors_for(lang)
            if errors:
                texts[lang] = self.write(judgement, lang, facts, examples, fix_notes=[e.detail for e in errors])
                attempts[lang] += 1
        if attempts["de"] > 1 or attempts["fr"] > 1:
            report = check_summaries(texts["de"], texts["fr"], judgement, outcome)

        return SummaryResult(
            case_id=judgement.case_id,
            title_de=title_de(judgement),
            title_fr=title_fr(judgement),
            summary_de=texts["de"],
            summary_fr=texts["fr"],
            facts=facts,
            outcome=outcome.describe_de(),
            examples=[e.case_id for e in examples],
            model=self.llm.model,
            prompt_version=prompts.prompt_version(self.prompts_dir),
            check=report,
            attempts=attempts,
        )


LENGTH = {"de": (130, 240), "fr": (160, 290)}  # words, around the median of the Suva texts (25.-75. percentile)

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "kernfrage": {"type": "string"},
        "kernregel": {"type": "string"},
        "erwaegungen": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
        "einstieg": {"type": "string", "enum": ["regel", "fall"]},
        "sachverhalt_kurz": {"type": "string"},
        "ergebnis_nennen": {"type": "boolean"},
    },
    "required": ["kernfrage", "kernregel", "erwaegungen", "einstieg", "sachverhalt_kurz", "ergebnis_nennen"],
    "additionalProperties": False,
}


class Summarizer(_AlignLanguage):
    """DE + FR summary of one judgement (default pipeline, chosen in ``notebooks/summary/02_modelle_prompts``).

    1. The model names the core question and the 1-3 considerations that make the judgement
       noteworthy for Suva (structured JSON, ``prompts/analyse_system.md``).
    2. It writes each language only from the wording of those considerations, with a block on
       who appealed and who won (``judgement.outcome_block``) and 3 Suva examples marked as
       *other cases, style only* (``examples.StyleExamples``).
    3. One rewrite when the text is far too short, one paragraph only, starts with
       «In diesem Urteil», or the check finds a wrong outcome, remand or article.
    4. Length: if one language is more than 30 % longer than the other (characters,
       ``check.MAX_LENGTH_RATIO``), one rewrite (``length_fix``): the longer one is shortened,
       unless the shorter one is below its length target, then that one is lengthened.
    5. DE/FR cross-check (``crosscheck``): an adversarial Apertus reviewer and a number
       comparison look for points where DE and FR disagree; the judgement decides which one is
       right and the other is rewritten once. What still disagrees is an error in the check
       report. On by default; off with ``crosscheck=False`` or ``SUMMARY_CROSSCHECK=0``.

    With Apertus 70B every criterion of the 70B judge was >= 4.2 on 20 development cases;
    the older ``SummarizerV1`` scored 4.0 with 70B and 2.3 with 8B.
    """
    def __init__(self, llm: Any, examples: StyleExamples | None = None, prompts_dir: Path = prompts.PROMPTS_DIR,
                 max_judgement_chars: int = 40000, analysis_llm: Any | None = None,
                 crosscheck: bool | None = None) -> None:
        self.llm = llm  # summary.llm.SummaryLLM (default Apertus 70B, see run.summarizer_factory): writes the texts
        # optional other model for step 1 only (core question), e.g. Apertus 70B; default = same model
        self.analysis_llm = analysis_llm or llm
        self.examples = examples or StyleExamples()
        self.prompts_dir = Path(prompts_dir)
        self.max_chars = max_judgement_chars
        if crosscheck is None:
            import os

            crosscheck = os.environ.get("SUMMARY_CROSSCHECK", "1").strip().lower() not in ("0", "false", "no", "off")
        self.crosscheck = crosscheck

    # --- step 1: core question -------------------------------------------------------------

    def analyse(self, judgement: Judgement) -> dict[str, Any]:
        known = sorted(consideration_numbers(judgement.text), key=lambda n: [int(x) for x in n.split(".")])
        user = (f"Urteil {judgement.case_id}\n\nVorhandene Erwägungen: {', '.join(known) or 'keine Nummern'}\n\n"
                f"Urteilstext:\n{excerpt(judgement.text, self.max_chars)}")
        client = self.analysis_llm._client
        fmt = {"type": "json_schema", "json_schema": {"name": "analyse", "strict": True, "schema": ANALYSIS_SCHEMA}}
        data, error = None, None
        for temperature in (0.0, 0.3):  # a greedy decoding that loops gets one retry with a little temperature
            response = client.chat.completions.create(
                model=self.analysis_llm.model, temperature=temperature, response_format=fmt,
                max_tokens=int(900 * getattr(self.analysis_llm, "token_factor", 1)),
                messages=[{"role": "system", "content": prompts._read("analyse_system.md", str(self.prompts_dir))},
                          {"role": "user", "content": user}])
            try:
                data = json.loads(response.choices[0].message.content)
                break
            except json.JSONDecodeError as exc:
                error = exc
        if data is None:
            raise error
        data["erwaegungen"] = [e.strip().rstrip(".") for e in data["erwaegungen"] if e.strip().rstrip(".") in known] or known[-2:]
        return data

    # --- step 2: write -----------------------------------------------------------------------

    def write_user(self, judgement: Judgement, language: str, analysis: dict[str, Any],
                   examples: Sequence[ReferenceCase], notes: Sequence[str] = ()) -> str:
        lo, hi = LENGTH[language]
        passages = consideration_passages(judgement.text, analysis["erwaegungen"], max_chars=9000)
        if language == "de":
            shots = "\n\n".join(f"--- Stilbeispiel {i + 1} (ANDERER Fall, Inhalt nicht übernehmen) ---\n{ex.text_de}"
                                for i, ex in enumerate(examples))
            parts = [
                "So schreibt die Suva. Übernimm Aufbau, Länge, Ton und Wendungen, aber KEINEN Inhalt:\n\n" + shots,
                f"=== Zu zusammenfassendes Urteil: {title_de(judgement)} ===",
                f"Kernfrage: {analysis['kernfrage']}\nKernregel: {analysis['kernregel']}",
                f"Sachverhalt (nur falls nötig, höchstens 1–2 Sätze): {analysis['sachverhalt_kurz']}",
                f"Wortlaut der massgebenden Erwägungen ({', '.join(analysis['erwaegungen'])}):\n{passages}",
                "Verbindliche Angaben zu Parteien und Ergebnis (nie umkehren):\n" + outcome_block(judgement, "de") + "\n"
                + ("Nenne das Ergebnis knapp im letzten Satz." if analysis["ergebnis_nennen"]
                   else "Nenne das Ergebnis nicht, ende mit der Regel oder ihrer Anwendung."),
                f"Einstieg: {'direkt mit der Rechtsregel' if analysis['einstieg'] == 'regel' else 'mit der versicherten Person und dem Ereignis'}.",
                f"Länge: {lo}–{hi} Wörter in 2–3 Absätzen. Nur Angaben aus den Erwägungen oben.",
                *([("Korrigiere diese Mängel der letzten Fassung:\n" + "\n".join(f"- {n}" for n in notes))] if notes else []),
                "Schreibe jetzt die Zusammenfassung auf Deutsch.",
            ]
        else:
            shots = "\n\n".join(f"--- Exemple de style {i + 1} (AUTRE affaire, ne pas reprendre le contenu) ---\n{ex.text_fr}"
                                for i, ex in enumerate(examples))
            parts = [
                "Voici comment écrit la Suva. Reprends la structure, la longueur, le ton et les tournures, mais AUCUN contenu :\n\n" + shots,
                f"=== Arrêt à résumer : {title_fr(judgement)} ===",
                f"Question centrale (en allemand) : {analysis['kernfrage']}\nRègle centrale (en allemand) : {analysis['kernregel']}",
                f"Faits (seulement si nécessaire, 1–2 phrases au plus ; en allemand) : {analysis['sachverhalt_kurz']}",
                f"Texte des considérants déterminants ({', '.join(analysis['erwaegungen'])}) :\n{passages}",
                "Indications contraignantes sur les parties et l'issue (ne jamais inverser) :\n" + outcome_block(judgement, "fr") + "\n"
                + ("Mentionne brièvement l'issue dans la dernière phrase." if analysis["ergebnis_nennen"]
                   else "Ne mentionne pas l'issue ; termine par la règle ou son application."),
                f"Entrée : {'directement par la règle de droit' if analysis['einstieg'] == 'regel' else 'par la personne assurée et l’événement'}.",
                f"Longueur : {lo}–{hi} mots en 2–3 paragraphes. Uniquement des éléments tirés des considérants ci-dessus.",
                *([("Corrige ces défauts de la dernière version :\n" + "\n".join(f"- {n}" for n in notes))] if notes else []),
                "Rédige maintenant le résumé en français.",
            ]
        return "\n\n".join(parts)

    def write(self, judgement: Judgement, language: str, analysis: dict[str, Any],
              examples: Sequence[ReferenceCase], notes: Sequence[str] = ()) -> str:
        system = prompts._read(f"write_system_{language}.md", str(self.prompts_dir)).format(
            style=prompts._read(f"style_{language}.md", str(self.prompts_dir)), glossary=prompts._read("glossary.md", str(self.prompts_dir)))
        return self.llm.chat(system, self.write_user(judgement, language, analysis, examples, notes), max_tokens=1200)

    def faults(self, text: str, language: str, judgement: Judgement) -> list[str]:
        from hack_apertus_coffee_exe.summary.check import cited_considerations

        lo, _ = LENGTH[language]
        notes = []
        words = len(text.split())
        if words < 0.75 * lo:
            notes.append(f"Zu kurz ({words} Wörter): schreibe {LENGTH[language][0]}–{LENGTH[language][1]} Wörter in 2–3 Absätzen."
                         if language == "de" else
                         f"Trop court ({words} mots) : écris {LENGTH[language][0]}–{LENGTH[language][1]} mots en 2–3 paragraphes.")
        invented = cited_considerations(text) - consideration_numbers(judgement.text)
        if invented:
            notes.append(("Erwägung(en) existieren nicht: " if language == "de" else "Considérant(s) inexistant(s) : ")
                         + ", ".join(sorted(invented)))
        from hack_apertus_coffee_exe.summary.check import check_language

        for issue in check_language(text, language, judgement, operative_outcome(judgement.text)):
            if issue.severity == "error" and issue.kind in ("outcome", "remand", "article"):
                notes.append(issue.detail)
        paragraphs = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
        if len(paragraphs) == 1 and words >= 90:
            notes.append("Nur ein Absatz: gliedere den Text in 2–3 Absätze, getrennt durch eine Leerzeile."
                         if language == "de" else "Un seul paragraphe : structure le texte en 2–3 paragraphes séparés par une ligne vide.")
        if text.lstrip().lower().startswith(("in diesem urteil", "dans cet arrêt", "dans cette affaire")):
            notes.append("Nicht mit «In diesem Urteil» beginnen." if language == "de" else "Ne pas commencer par « Dans cet arrêt ».")
        return notes

    @staticmethod
    def length_fix(texts: dict[str, str]) -> dict[str, list[str]]:
        """Which language to rewrite, with what note, when DE and FR differ in length by more than 30 %.

        The longer text is shortened, unless the shorter one is below its word target
        (``LENGTH``, Suva's median band): then the shorter one probably left content out and is
        lengthened instead. Empty when the lengths agree.
        """
        from .check import MAX_LENGTH_RATIO, length_ratio

        ratio = length_ratio(texts["de"], texts["fr"])
        if not ratio or ratio[0] <= MAX_LENGTH_RATIO:
            return {}
        longer = ratio[1]
        shorter = "fr" if longer == "de" else "de"
        chars = {lang: len(texts[lang].strip()) for lang in LANGUAGES}
        words = {lang: len(texts[lang].split()) for lang in LANGUAGES}
        pct = round((ratio[0] - 1) * 100)
        if words[shorter] < LENGTH[shorter][0]:
            target = int(chars[longer] / MAX_LENGTH_RATIO * 1.05)
            note = (f"Die deutsche Fassung ist deutlich kürzer als die französische ({chars['de']} gegenüber {chars['fr']} Zeichen) "
                    f"und vermutlich unvollständig. Ergänze sie auf mindestens etwa {target} Zeichen "
                    f"({LENGTH['de'][0]}–{LENGTH['de'][1]} Wörter), nur mit Angaben aus den Erwägungen."
                    if shorter == "de" else
                    f"La version française est nettement plus courte que l'allemande ({chars['fr']} contre {chars['de']} caractères) "
                    f"et probablement incomplète. Complète-la jusqu'à au moins {target} caractères environ "
                    f"({LENGTH['fr'][0]}–{LENGTH['fr'][1]} mots), uniquement avec des éléments tirés des considérants.")
            return {shorter: [note]}
        target = int(chars[shorter] * MAX_LENGTH_RATIO * 0.95)
        note = (f"Die deutsche Fassung ist {pct} % länger als die französische ({chars['de']} gegenüber {chars['fr']} Zeichen). "
                f"Kürze sie auf höchstens etwa {target} Zeichen, ohne Aussagen wegzulassen, die auch in der französischen Fassung stehen."
                if longer == "de" else
                f"La version française est {pct} % plus longue que l'allemande ({chars['fr']} contre {chars['de']} caractères). "
                f"Raccourcis-la à environ {target} caractères au plus, sans supprimer ce qui figure aussi dans la version allemande.")
        return {longer: [note]}

    def summarize(self, judgement: Judgement, similar_case_ids: Sequence[str] = (),
                  exclude_ids: Sequence[str] = ()) -> SummaryResult:
        """DE + FR summary. ``similar_case_ids`` / ``exclude_ids`` are accepted for the
        ``generate.Summarizer`` interface; examples come from ``StyleExamples``."""
        analysis = self.analyse(judgement)
        texts, attempts, used, shots = {}, {}, [], {}
        for language in ("de", "fr"):
            examples = self.examples.select(judgement, language)
            shots[language] = examples
            used += [e.case_id for e in examples if e.case_id not in used]
            text = self.write(judgement, language, analysis, examples)
            attempts[language] = 1
            notes = self.faults(text, language, judgement)
            if notes:
                text = self.write(judgement, language, analysis, examples, notes)
                attempts[language] = 2
            texts[language] = text.strip()
        for language, notes in self.length_fix(texts).items():
            attempts[language] = attempts.get(language, 1) + 1
            texts[language] = self.write(judgement, language, analysis, shots[language], notes).strip()
        extra: list = []
        if self.crosscheck:
            from . import crosscheck

            def rewrite(language: str, notes: list[str]) -> str:
                attempts[language] = attempts.get(language, 1) + 1
                return self.write(judgement, language, analysis, shots[language], notes)

            evidence = (consideration_passages(judgement.text, analysis["erwaegungen"], max_chars=6000)
                        + "\n\nDispositiv / Parteien:\n" + outcome_block(judgement, "de"))
            texts, extra, trace = crosscheck.crosscheck_and_repair(
                texts, crosscheck.gateway_json_call(self.analysis_llm), evidence, rewrite)
            analysis = {**analysis, "crosscheck": trace}
        return self.result(judgement, texts, analysis, used, attempts, extra)

    def result(self, judgement: Judgement, texts: dict[str, str], analysis: dict[str, Any],
               examples: list[str], attempts: dict[str, int], extra_issues: Sequence[Any] = ()) -> SummaryResult:
        outcome = operative_outcome(judgement.text)
        check = check_summaries(texts["de"], texts["fr"], judgement, outcome)
        check.issues += list(extra_issues)
        return SummaryResult(
            case_id=judgement.case_id,
            title_de=title_de(judgement),
            title_fr=title_fr(judgement),
            summary_de=texts["de"],
            summary_fr=texts["fr"],
            facts=analysis,
            outcome=outcome.describe_de(),
            examples=examples,
            model=self.llm.model,
            prompt_version=prompts.prompt_files_version(self.prompts_dir),
            check=check,
            attempts=attempts,
        )
