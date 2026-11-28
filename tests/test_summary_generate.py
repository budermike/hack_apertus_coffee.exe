"""End-to-end summary flow with a fake Apertus client (no network)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hack_apertus_coffee_exe.triage.llm import GatewayConfig
from hack_apertus_coffee_exe.triage.reference_corpus import ReferenceCase

from hack_apertus_coffee_exe.summary import prompts
from hack_apertus_coffee_exe.summary.examples import ExampleSelector
from hack_apertus_coffee_exe.summary.generate import SummarizerV1 as Summarizer
from hack_apertus_coffee_exe.summary.judgement import find_judgement
from hack_apertus_coffee_exe.summary.llm import SummaryLLM, parse_json_object
from hack_apertus_coffee_exe.summary.run import run_batch, summarize_case
from hack_apertus_coffee_exe.summary import store

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
    conn = store.connect(":memory:")
    first = summarize_case(J, conn, lambda: summarizer)
    assert first["de"].text == DE_OK and first["fr"].coherence_ok
    assert first["de"].title.startswith("BGer-Urteil 8C_901/2026")
    assert store.get_case(conn, J.case_id).url == J.url
    calls = len(summarizer.llm._client.chat.completions.calls)

    again = summarize_case(J, conn, lambda: pytest.fail("must not call the LLM"))
    assert again == first
    assert len(summarizer.llm._client.chat.completions.calls) == calls
    assert len(store.get_history(conn, J.case_id, "de")) == 1


def test_batch_only_green(summarizer):
    conn = store.connect(":memory:")
    rows = [
        {"case_id": "8C_901/2026", "ampel": "gruen", "similar_cases": [{"case_id": "8C_177/2025"}]},
        {"case_id": "9C_903/2026", "ampel": "rot"},
        {"case_id": "8C_999/2026", "ampel": "gruen"},  # no crawl record -> skipped
    ]
    done = run_batch(rows, [J], conn, lambda: summarizer)
    assert done == ["8C_901/2026"]
    assert store.list_case_ids(conn) == ["8C_901/2026"]


def test_invented_consideration_numbers_are_removed_from_the_facts(summarizer):
    FACTS["kernerwaegungen"].append({"aussage": "erfunden", "erwaegung": "4.2"})
    try:
        facts = summarizer.extract_facts(J)
    finally:
        FACTS["kernerwaegungen"].pop()
    assert [k["erwaegung"] for k in facts["kernerwaegungen"]] == ["3", ""]


# --- Summarizer (default pipeline, former v3) -------------------------------------------

from datetime import date  # noqa: E402

from hack_apertus_coffee_exe.summary import examples as ex_mod, generate as gen_mod, judgement as j_mod  # noqa: E402
from hack_apertus_coffee_exe.summary.judgement import Judgement  # noqa: E402

PARTY_JUDGEMENT = """Verfahrensbeteiligte
Suva, Rechtsabteilung, 6002 Luzern,
Beschwerdeführerin,
gegen
A.________,
Beschwerdegegner.

Sachverhalt:
A. Der Versicherte stürzte von einer Leiter.

Erwägungen:
1. Die Beschwerde ist zulässig.
2. Streitig ist der natürliche Kausalzusammenhang nach Art. 6 Abs. 1 UVG. Die Vorinstanz stützte sich auf ein Gutachten.
3. Das Gutachten beruht auf einem falschen Unfallhergang; der Kausalzusammenhang ist nicht überwiegend wahrscheinlich.

Demnach erkennt das Bundesgericht:
1. Die Beschwerde wird gutgeheissen. Der Einspracheentscheid der Suva wird bestätigt.
2. Die Gerichtskosten werden dem Beschwerdegegner auferlegt.
"""


def _party_judgement() -> Judgement:
    return Judgement(case_id="8C_9/2025", text=PARTY_JUDGEMENT, language="de", decision_date=date(2025, 6, 1))


def _style_ref(case_id: str, title: str) -> ReferenceCase:
    return ReferenceCase(case_id=case_id, chamber="8C", title=title, text_de=f"Regel {case_id}.", text_fr=f"Règle {case_id}.",
                         bger_url="", author="Frei Thomas (FRT), Rechtsanwalt", source_file="")


def test_appellant_and_outcome_block_name_who_appealed_and_who_won():
    assert j_mod.appellant(PARTY_JUDGEMENT) == "versicherer"
    block = j_mod.outcome_block(_party_judgement(), "de")
    assert "Versicherer" in block and "obsiegt" in block and "Gerichtskosten" not in block


def test_style_examples_skip_own_case_and_prefer_target_language_originals():
    refs = [_style_ref("8C_9/2025", "BGer-Urteil 8C_9/2025"), _style_ref("8C_1/2025", "Arrêt du TF 8C_1/2025"),
            _style_ref("8C_2/2025", "BGer-Urteil 8C_2/2025")]
    chosen = ex_mod.StyleExamples(refs).select(_party_judgement(), "de", k=2)
    assert [r.case_id for r in chosen] == ["8C_2/2025", "8C_1/2025"]


class FakeLLM:
    """Answers the analysis (JSON) and every write call; records the prompts."""

    model = "apertus-v1-5-70b"

    def __init__(self, text: str) -> None:
        self.text, self.users = text, []
        analysis = {"kernfrage": "Kausalzusammenhang?", "kernregel": "Ein Gutachten mit falschem Hergang ist nicht beweiskräftig.",
                    "erwaegungen": ["3", "9.9"], "einstieg": "regel", "sachverhalt_kurz": "Sturz.", "ergebnis_nennen": True}
        message = type("M", (), {"content": json.dumps(analysis)})
        response = type("R", (), {"choices": [type("C", (), {"message": message})]})
        self._client = type("Cl", (), {"chat": type("Ch", (), {"completions": type("Co", (), {
            "create": staticmethod(lambda **kw: response)})})})

    def chat(self, system, user, max_tokens=None):
        self.users.append(user)
        return self.text


def test_summarize_returns_summary_result_and_drops_unknown_considerations(tmp_path):
    words = " ".join(["Wort"] * 160)
    llm = FakeLLM(f"Ein Gutachten mit falschem Hergang ist nicht beweiskräftig. {words}\n\nDie Beschwerde der Suva wurde gutgeheissen.")
    refs = [_style_ref("8C_2/2025", "BGer-Urteil 8C_2/2025")]
    result = gen_mod.Summarizer(llm, ex_mod.StyleExamples(refs)).summarize(_party_judgement())
    assert result.facts["erwaegungen"] == ["3"]          # 9.9 does not exist in the judgement
    assert result.summary_de.startswith("Ein Gutachten")
    assert result.prompt_version.startswith("v3-") and result.model == "apertus-v1-5-70b"
    assert "obsiegt" in llm.users[0] and "ANDERER Fall" in llm.users[0]


def test_faults_ask_for_rewrite_when_too_short_or_one_paragraph():
    s = gen_mod.Summarizer(FakeLLM(""), ex_mod.StyleExamples([_style_ref("8C_2/2025", "BGer-Urteil 8C_2/2025")]))
    assert any("Zu kurz" in n for n in s.faults("Kurz.", "de", _party_judgement()))
    assert any("Absatz" in n for n in s.faults(" ".join(["Wort"] * 120), "de", _party_judgement()))
