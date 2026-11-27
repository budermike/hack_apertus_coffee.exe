"""Two-step summary generation with Apertus (issue #15) plus check and retry (#16).

1. Facts: judgement -> facts JSON (one call).
2. Writing: facts + Suva examples + style profile + glossary -> DE text and,
   separately, FR text (one call each; FR is written, not translated).
3. Check: rule-based check; a language with errors is regenerated once with the
   errors as feedback. Remaining issues are returned with the result (for the UI).

Storing results is not part of this module; see ``SummaryResult.to_dict()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .check import CheckReport, check_summaries
from .examples import ExampleSelector
from .judgement import (
    Judgement,
    consideration_numbers,
    consideration_passages,
    excerpt,
    operative_outcome,
    title_de,
    title_fr,
)
from .llm import SummaryLLM, parse_json_object
from . import prompts

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


class Summarizer:
    def __init__(
        self,
        llm: SummaryLLM,
        selector: ExampleSelector | None = None,
        n_examples: int = 2,
        max_judgement_chars: int = 40000,
        prompts_dir: Path = prompts.PROMPTS_DIR,
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
