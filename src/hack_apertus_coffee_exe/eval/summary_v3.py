"""Experiments around the summary pipeline (``summary/generate.py``, ``Summarizer`` = v3), measured in
``notebooks/summary/02_modelle_prompts``: ``BudgetLLM`` (token budget for thinking models) and
``SummarizerV4`` (writes the judgement's language first, renders the other one from it).

v4 helped 8B Thinking (3.2 vs 2.6 with v3) but not 70B (3.7 vs 4.2), so it stays an experiment.
"""

from __future__ import annotations

from typing import Any, Sequence

from hack_apertus_coffee_exe.summary.judgement import Judgement, title_de, title_fr
from hack_apertus_coffee_exe.summary.generate import SummaryResult
from hack_apertus_coffee_exe.summary.examples import StyleExamples  # noqa: F401
from hack_apertus_coffee_exe.summary.generate import LENGTH, Summarizer
from hack_apertus_coffee_exe.summary.judgement import appellant, operative_text, outcome_block  # noqa: F401
from hack_apertus_coffee_exe.summary.prompts import _read as _read_cached

SummarizerV3 = Summarizer  # name used in the notebooks


def _read(name: str, prompts_dir) -> str:
    return _read_cached(name, str(prompts_dir))
from hack_apertus_coffee_exe.triage.reference_corpus import ReferenceCase


# --- v4: learned from v1-v3 --------------------------------------------------------------------


class BudgetLLM:
    """SummaryLLM wrapper: thinking models get ``token_factor`` times the token budget
    (they spend part of it on hidden reasoning and otherwise stop mid-answer)."""

    def __init__(self, llm: Any, token_factor: float | None = None) -> None:
        self._llm = llm
        self.token_factor = token_factor if token_factor is not None else (4.0 if "thinking" in llm.model else 1.0)

    @property
    def model(self) -> str:
        return self._llm.model

    @property
    def _client(self) -> Any:
        return self._llm._client

    def chat(self, system: str, user: str, max_tokens: int | None = None) -> str:
        return self._llm.chat(system, user, max_tokens=int((max_tokens or self._llm.max_tokens) * self.token_factor))


class SummarizerV4(SummarizerV3):
    """v3 plus what v1-v3 taught us (``notebooks/summary/02_modelle_prompts``):

    - 8B is much better in the judgement's language (3.7 vs 2.8 with a good core question), and the
      Suva texts themselves look like original + translation. So v4 writes the summary in the
      judgement's language first and then renders the other language *from that summary*
      (same content, idiomatic, glossary), instead of summarising foreign-language considerations.
    - paragraph patterns in the style profile (8B wrote one paragraph almost always).
    """

    def render(self, judgement: Judgement, source_text: str, source_lang: str, language: str,
               examples: Sequence[ReferenceCase], notes: Sequence[str] = ()) -> str:
        system = _read(f"write_system_{language}.md", self.prompts_dir).format(
            style=_read(f"style_{language}.md", self.prompts_dir), glossary=_read("glossary.md", self.prompts_dir))
        lo, hi = LENGTH[language]
        if language == "de":
            shots = "\n\n".join(f"--- Stilbeispiel {i + 1} (ANDERER Fall, Inhalt nicht übernehmen) ---\n{ex.text_de}"
                                for i, ex in enumerate(examples))
            user = "\n\n".join([
                "So schreibt die Suva auf Deutsch:\n\n" + shots,
                f"=== {title_de(judgement)} ===",
                "Verbindliche Angaben zu Parteien und Ergebnis (nie umkehren):\n" + outcome_block(judgement, "de"),
                f"Französische Suva-Zusammenfassung dieses Urteils:\n{source_text}",
                "Erstelle die deutsche Fassung: dieselben Aussagen in derselben Reihenfolge und Absatzgliederung, "
                "nichts weglassen, nichts hinzufügen, aber in natürlichem Schweizer Juristendeutsch nach Stilprofil und Glossar "
                f"(keine Wort-für-Wort-Übersetzung). Etwa {lo}–{hi} Wörter.",
                *([("Korrigiere diese Mängel:\n" + "\n".join(f"- {n}" for n in notes))] if notes else []),
            ])
        else:
            shots = "\n\n".join(f"--- Exemple de style {i + 1} (AUTRE affaire, ne pas reprendre le contenu) ---\n{ex.text_fr}"
                                for i, ex in enumerate(examples))
            user = "\n\n".join([
                "Voici comment écrit la Suva en français :\n\n" + shots,
                f"=== {title_fr(judgement)} ===",
                "Indications contraignantes sur les parties et l'issue (ne jamais inverser) :\n" + outcome_block(judgement, "fr"),
                f"Résumé Suva en allemand de cet arrêt :\n{source_text}",
                "Rédige la version française : les mêmes énoncés dans le même ordre et la même structure de paragraphes, "
                "sans rien omettre ni ajouter, mais en français juridique romand naturel selon le profil de style et le glossaire "
                f"(pas de traduction mot à mot). Environ {lo}–{hi} mots.",
                *([("Corrige ces défauts :\n" + "\n".join(f"- {n}" for n in notes))] if notes else []),
            ])
        return self.llm.chat(system, user, max_tokens=1200)

    def summarize(self, judgement: Judgement, similar_case_ids: Sequence[str] = (),
                  exclude_ids: Sequence[str] = ()) -> SummaryResult:
        analysis = self.analyse(judgement)
        first = judgement.language if judgement.language in ("de", "fr") else "de"
        second = "fr" if first == "de" else "de"
        texts, attempts, used = {}, {}, {}
        examples = {lang: self.examples.select(judgement, lang) for lang in (first, second)}
        used = {lang: [e.case_id for e in ex] for lang, ex in examples.items()}

        text = self.write(judgement, first, analysis, examples[first])
        attempts[first] = 1
        notes = self.faults(text, first, judgement)
        if notes:
            text = self.write(judgement, first, analysis, examples[first], notes)
            attempts[first] = 2
        texts[first] = text.strip()

        other = self.render(judgement, texts[first], first, second, examples[second])
        attempts[second] = 1
        notes = self.faults(other, second, judgement)
        if notes:
            other = self.render(judgement, texts[first], first, second, examples[second], notes)
            attempts[second] = 2
        texts[second] = other.strip()
        result = self.result(judgement, texts, analysis, used[first] + [e for e in used[second] if e not in used[first]], attempts)
        result.prompt_version = result.prompt_version.replace("v3-", "v4-")
        return result
