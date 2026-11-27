"""End-to-end summary flow with a fake Apertus client (no network)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hack_apertus_coffee_exe.triage.llm import GatewayConfig
from hack_apertus_coffee_exe.triage.reference_corpus import ReferenceCase

from hack_apertus_coffee_exe.summary import prompts
from hack_apertus_coffee_exe.summary.examples import ExampleSelector
from hack_apertus_coffee_exe.summary.generate import Summarizer
from hack_apertus_coffee_exe.summary.judgement import find_judgement
from hack_apertus_coffee_exe.summary.llm import SummaryLLM, parse_json_object
from hack_apertus_coffee_exe.summary.run import run_batch, summarize_case
from hack_apertus_coffee_exe.summary.store import SummaryStore

J = find_judgement("8C_901/2026", Path("src/hack_apertus_coffee_exe/triage/mock_crawl"))

FACTS = {
    "rechtsgebiet": "Unfallversicherung – unfallähnliche Körperschädigung",
    "form": "fall",
    "kernerwaegungen": [{"aussage": "Kreisärztliche Einschätzung ungenügend", "erwaegung": "3"}],
    "gesetzesartikel": ["Art. 6 Abs. 2 UVG", "Art. 44 ATSG"],
}
DE_BAD = "Die Liste in Art. 6 Abs. 2 UVG ist abschliessend (E. 9).\n\nDie Beschwerde wird teilweise gutgeheissen."
DE_OK = "Die Liste in Art. 6 Abs. 2 UVG ist abschliessend (E. 2).\n\nDie Beschwerde wird teilweise gutgeheissen und die Sache an die Suva zurückgewiesen."
FR_OK = "La liste de l'art. 6 al. 2 LAA est exhaustive (consid. 2).\n\nLe recours est partiellement admis et la cause renvoyée à la Suva."


class FakeCompletions:
    """Answers facts, DE and FR prompts; the first DE answer cites a wrong consideration."""

    def __init__(self):
        self.calls = []

    def create(self, model, messages, temperature, max_tokens):
        system, user = messages[0]["content"], messages[1]["content"]
        self.calls.append((system, user))
        if "Antworte AUSSCHLIESSLICH mit gültigem JSON" in system:
            content = "```json\n" + json.dumps(FACTS, ensure_ascii=False) + "\n```"
        elif "en français" in system:
            content = FR_OK
        else:
            content = DE_OK if "korrigiere sie" in user else DE_BAD
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _ref(case_id, de, fr):
    return ReferenceCase(case_id=case_id, chamber="8C", title=f"BGer-Urteil {case_id}", text_de=de, text_fr=fr,
                         bger_url="", author="", source_file="")


REFS = [
    _ref("8C_177/2025", "Die Liste der unfallähnlichen Körperschädigungen in Art. 6 Abs. 2 UVG ist abschliessend. Sehnen Gutachten Suva.",
         "La liste des lésions corporelles assimilées à un accident de l'art. 6 al. 2 LAA est exhaustive."),
    _ref("8C_7/2025", "Valideneinkommen nach LSE bei Konkurs des Arbeitgebers.", "Revenu sans invalidité selon l'ESS en cas de faillite."),
    _ref("8C_901/2026", "Eigene Suva-Zusammenfassung des Falls (darf nicht verwendet werden).", "Résumé propre."),
    _ref("8C_455/2025", "cavete collegae: Fristenstillstand Art. 46 BGG.", "cavete collegae: suspension des délais."),
]


@pytest.fixture
def summarizer():
    fake = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    llm = SummaryLLM(config=GatewayConfig(api_key="test", base_url="http://localhost", model="apertus-test"), client=fake)
    return Summarizer(llm, ExampleSelector(references=REFS), n_examples=2)


def test_parse_json_object_tolerates_fences():
    assert parse_json_object('Hier:\n```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        parse_json_object("kein json")


def test_examples_exclude_own_case_and_prefer_triage_matches():
    selector = ExampleSelector(references=REFS)
    picked = [r.case_id for r in selector.select(J, k=2)]
    assert "8C_901/2026" not in picked
    assert picked[0] == "8C_177/2025"  # most similar by TF-IDF
    preferred = [r.case_id for r in selector.select(J, k=2, preferred_ids=["8C_455/2025"])]
    assert preferred[0] == "8C_455/2025"


def test_summarize_retries_failing_language_once(summarizer):
    result = summarizer.summarize(J)
    completions = summarizer.llm._client.chat.completions
    assert len(completions.calls) == 4  # facts, DE, FR, DE retry
    assert result.attempts == {"de": 2, "fr": 1}
    assert result.summary_de == DE_OK
    assert result.coherence_ok, result.check.to_dicts()
    assert result.title_fr == "Arrêt du TF 8C_901/2026 du 14 octobre 2026"
    assert result.outcome == "teilweise gutgeheissen (Rückweisung)"
    assert "8C_901/2026" not in result.examples
    assert result.prompt_version.startswith(prompts.TEMPLATE_VERSION)
    # Style profile, glossary and examples are in the writer prompt; the FR prompt is French.
    de_system, de_user = completions.calls[1]
    assert "Stilprofil" in de_system and "Glossar" in de_system
    assert "8C_177/2025" in de_user and "Art. 44 ATSG" in de_user
    assert "Profil de style" in completions.calls[2][0]


def test_store_is_used_instead_of_regenerating(summarizer):
    store = SummaryStore(":memory:")
    first = summarize_case(J, store, lambda: summarizer)
    assert first["de"].text == DE_OK and first["fr"].coherence_ok
    calls = len(summarizer.llm._client.chat.completions.calls)

    again = summarize_case(J, store, lambda: pytest.fail("must not call the LLM"))
    assert again["de"].id == first["de"].id
    summarize_case(J, store, lambda: summarizer, force=True)
    assert len(summarizer.llm._client.chat.completions.calls) > calls
    assert len(store.history(J.case_id, "de")) == 2


def test_batch_only_green(summarizer):
    store = SummaryStore(":memory:")
    rows = [
        {"case_id": "8C_901/2026", "ampel": "gruen", "similar_cases": [{"case_id": "8C_177/2025"}]},
        {"case_id": "9C_903/2026", "ampel": "rot"},
        {"case_id": "8C_999/2026", "ampel": "gruen"},  # no crawl record -> skipped
    ]
    done = run_batch(rows, [J], store, lambda: summarizer)
    assert done == ["8C_901/2026"]
    assert store.case_ids() == ["8C_901/2026"]
