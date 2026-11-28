"""Pick Suva reference summaries as few-shot examples (issue #15).

``StyleExamples`` (used by ``generate.Summarizer``): 3 examples per language, similar cases first,
originals in the target language preferred. ``ExampleSelector``: the older selection for ``SummarizerV1``.

Order of preference:
1. the ``similar_cases`` that triage already found for this judgement,
2. the most similar summaries by the triage TF-IDF cosine similarity.
The judgement's own Suva summary is never used (no leakage into eval).
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import numpy as np

from ..triage.embeddings import TfidfEmbedder, build_reference_index, score_against_corpus
from ..triage.reference_corpus import ReferenceCase, load_reference_corpus

from .judgement import Judgement, same_case


class ExampleSelector:
    def __init__(
        self,
        references: Sequence[ReferenceCase] | None = None,
        judgements_dir: str | Path = "judgements",
    ) -> None:
        refs = list(references) if references is not None else load_reference_corpus(judgements_dir)
        # Only pairs with both languages can serve as examples for DE and FR.
        self.references = [r for r in refs if r.text_de.strip() and r.text_fr.strip()]
        self.embedder = TfidfEmbedder().fit([r.text_for_language(None) for r in self.references])
        self._vecs: dict[str, np.ndarray] = {}

    def _ref_vecs(self, language: str) -> np.ndarray:
        key = language if language in ("de", "fr") else "all"
        if key not in self._vecs:
            _, vecs = build_reference_index(self.references, self.embedder, None if key == "all" else key)
            self._vecs[key] = vecs
        return self._vecs[key]

    def select(
        self,
        judgement: Judgement,
        k: int = 2,
        preferred_ids: Sequence[str] = (),
        exclude_ids: Sequence[str] = (),
    ) -> list[ReferenceCase]:
        blocked = [judgement.case_id, *exclude_ids]
        allowed = [i for i, r in enumerate(self.references) if not any(same_case(r.case_id, b) for b in blocked)]
        chosen: list[ReferenceCase] = []

        for pid in preferred_ids:
            for i in allowed:
                ref = self.references[i]
                if same_case(ref.case_id, pid) and ref not in chosen:
                    chosen.append(ref)
            if len(chosen) >= k:
                return chosen[:k]

        if not allowed or not judgement.text.strip():
            return chosen
        cases = [self.references[i] for i in allowed]
        vecs = self._ref_vecs(judgement.language)[allowed]
        score = score_against_corpus(
            judgement.text, cases, self.embedder, vecs, language=judgement.language, top_k=k + len(chosen)
        )
        by_id = {c.case_id: c for c in cases}
        for match in score.top_matches:
            ref = by_id.get(match.case_id)
            if ref and ref not in chosen:
                chosen.append(ref)
            if len(chosen) >= k:
                break
        return chosen


def original_language(ref: ReferenceCase) -> str:
    """Language of the judgement (= title language); assumed to be the language of the original summary."""
    low = ref.title.lower()
    return "fr" if low.startswith("arrêt") else "it" if low.startswith("sentenza") else "de"


class StyleExamples:
    """3 Suva examples per language: similar cases first, originals in the target language preferred."""

    def __init__(self, references: Sequence[ReferenceCase] | None = None) -> None:
        refs = list(references) if references is not None else load_reference_corpus("judgements")
        self.refs = [r for r in refs if r.text_de.strip() and r.text_fr.strip()]
        self.emb = TfidfEmbedder().fit([r.text_for_language(None) for r in self.refs])
        self.vecs = self.emb.embed([r.text_for_language(None) for r in self.refs])

    def select(self, judgement: Judgement, language: str, k: int = 3) -> list[ReferenceCase]:
        allowed = [i for i, r in enumerate(self.refs) if not same_case(r.case_id, judgement.case_id)]
        sims = self.vecs[allowed] @ self.emb.embed([judgement.text[:20000]])[0]
        bonus = np.array([0.15 if original_language(self.refs[i]) == language else 0.0 for i in allowed])
        order = np.argsort(-(sims + bonus), kind="stable")
        return [self.refs[allowed[i]] for i in order[:k]]
