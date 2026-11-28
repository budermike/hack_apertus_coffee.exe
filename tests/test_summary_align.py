"""Tests for carrying an edit over to the other language (no gateway; Apertus is faked)."""

import json

import pytest

from hack_apertus_coffee_exe.summary import align
from hack_apertus_coffee_exe.summary.judgement import Judgement

DE = ("Das Bundesgericht hält fest, dass Art. 6 Abs. 2 UVG eine Listenverletzung verlangt (E. 4.2). "
      "Die Suva muss den Beweis mit hinreichender Wahrscheinlichkeit erbringen.\n\n"
      "Das Bundesgericht wies die Beschwerde ab.")
FR = ("Le Tribunal fédéral retient que l'art. 6 al. 2 LAA exige une lésion figurant dans la liste (consid. 4.2). "
      "La Suva doit apporter la preuve avec une vraisemblance suffisante.\n\n"
      "Le recours est rejeté.")
JUDGEMENT = Judgement(
    case_id="8C_1/2026", case_ids=("8C_1/2026",), decision_date=None, language="de", url="",
    text="Erwägungen:\n4.2 Die Listenverletzung ist nachzuweisen.\n5. Kosten.\nDemnach erkennt das Bundesgericht:\n"
         "1. Die Beschwerde wird abgewiesen.",
)


class FakeLLM:
    """Answers each ``chat`` call with the next prepared answer (a dict becomes JSON)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def chat(self, system, user, **kwargs):
        self.calls.append((system, user, kwargs))
        answer = self.answers.pop(0)
        return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)


def change(alt="", neu="", inhaltlich=True, unklar=False, widerspricht=False, hinweis=""):
    return {"inhaltlich": inhaltlich, "alt": alt, "neu": neu, "unklar": unklar,
            "widerspricht_urteil": widerspricht, "hinweis": hinweis}


NO_DIFFERENCES = {"abweichungen": []}


def run(llm, de_after, **kwargs):
    return align.align_edit(llm, target_lang="fr", target_text=FR, source_lang="de", source_text=de_after,
                            source_before=DE, **kwargs)


def test_split_sentences_keeps_abbreviations_together_and_loses_nothing():
    parts = align.split_sentences(DE)
    assert [sentence[:20] for sentence, _ in parts] == [
        "Das Bundesgericht hä", "Die Suva muss den Be", "Das Bundesgericht wi"]
    assert parts[1][1] == "\n\n"
    assert "".join(sentence + gap for sentence, gap in parts) == DE
    assert len(align.split_sentences("Vgl. Art. 4 ATSG und E. 3.1. Am 12. März 2025 entschied B.________ anders.")) == 2


def test_source_changes_lists_only_what_was_edited():
    after = DE.replace("hinreichender", "überwiegender").replace("\n\nDas Bundesgericht wies die Beschwerde ab.", "")
    changes = align.source_changes(DE, after + " Die Kosten trägt die Suva.")
    assert [(c["removed"], c["added"]) for c in changes] == [
        ("hinreichender", "überwiegender"),
        ("Das Bundesgericht wies […] Beschwerde ab.", "Die Kosten trägt […] Suva."),
    ]
    assert changes[1]["before"] == "Das Bundesgericht wies die Beschwerde ab." and changes[1]["after"] == "Die Kosten trägt die Suva."
    assert align.source_changes(DE, DE) == []
    assert align.source_changes(DE, DE + "\n") == []


def test_replace_sentences_leaves_the_rest_word_for_word():
    sentences = align.split_sentences(FR)
    assert align.replace_sentences(sentences, [3], "Le recours est admis.") == FR.replace("rejeté", "admis")
    # a removed sentence hands its paragraph break to the sentence before
    assert align.replace_sentences(sentences, [2], "") == FR.replace(" La Suva doit apporter la preuve avec une vraisemblance suffisante.", "")
    with pytest.raises(ValueError):
        align.replace_sentences(sentences, [4], "x")


def test_a_change_of_substance_is_carried_over_and_nothing_else_changes():
    old = "La Suva doit apporter la preuve avec une vraisemblance suffisante."
    new = old.replace("suffisante", "prépondérante")
    # Apertus quotes one sentence more than it changes: only the differing one is replaced
    llm = FakeLLM(change(alt=f"{old} Le recours est rejeté.", neu=f"{new} Le recours est rejeté."), NO_DIFFERENCES)
    result = run(llm, DE.replace("hinreichender", "überwiegender"), judgement=JUDGEMENT)
    assert result.text == FR.replace("suffisante", "prépondérante")
    assert result.notes == [] and result.has_reference is True
    system, user, kwargs = llm.calls[0]
    assert "PAS DE TRADUCTION" in system and kwargs["response_format"]["json_schema"]["schema"] is align.CHANGE_SCHEMA
    # the judgement (cited consideration and outcome) is the reference; the change is spelled out
    assert "Die Listenverletzung ist nachzuweisen." in user
    assert "mots supprimés: hinreichender" in user and "mots ajoutés: überwiegender" in user
    # the closing check compares both final texts
    assert "prépondérante" in llm.calls[1][1] and "überwiegender" in llm.calls[1][1]


def test_an_edit_of_the_wording_alone_changes_nothing():
    llm = FakeLLM(change(inhaltlich=False), NO_DIFFERENCES)
    result = run(llm, DE.replace("hält fest", "hat festgehalten"))
    assert result.text == FR and result.notes == [] and result.has_reference is False


def test_no_call_when_the_edit_changed_no_sentence():
    llm = FakeLLM()
    assert run(llm, DE + "  ").text == FR
    assert llm.calls == []


def test_an_edit_against_the_judgement_is_carried_over_and_noted():
    llm = FakeLLM(change(alt="Le recours est rejeté.", neu="Le recours est admis.", widerspricht=True,
                         hinweis="Selon l'arrêt, le recours est rejeté."), NO_DIFFERENCES)
    result = run(llm, DE.replace("wies die Beschwerde ab", "hiess die Beschwerde gut"), judgement=JUDGEMENT)
    assert result.text.endswith("Le recours est admis.")
    assert result.notes == ["Selon l'arrêt, le recours est rejeté."]


def test_new_and_removed_statements():
    llm = FakeLLM(change(alt="Le recours est rejeté.", neu="Le recours est rejeté. Les frais sont à la charge de la Suva."),
                  NO_DIFFERENCES)
    assert run(llm, DE + " Die Kosten trägt die Suva.").text == FR + " Les frais sont à la charge de la Suva."
    removed = " La Suva doit apporter la preuve avec une vraisemblance suffisante."
    llm = FakeLLM(change(alt=removed.strip(), neu=""), NO_DIFFERENCES)
    after = DE.replace(" Die Suva muss den Beweis mit hinreichender Wahrscheinlichkeit erbringen.", "")
    assert run(llm, after).text == FR.replace(removed, "")


@pytest.mark.parametrize("answer", [
    change(alt="Cette phrase ne figure pas dans le texte.", neu="Autre phrase."),          # quote not in the text
    change(alt="Le recours est rejeté.", neu="Die Suva war dazu berechtigt, wirklich."),  # a new text, not the old one changed
    change(alt="Le recours est rejeté.", neu=""),                                           # removes although nothing was removed
    change(alt="Le recours est rejeté.", neu="Le recours est rejeté."),                    # says "substance" but changes nothing
    # a figure that is neither in the old sentence nor in the edit (taken from the judgement or invented)
    change(alt="Le recours est rejeté.", neu="Le recours est rejeté, les frais de 800 fr. restant dus."),
    # more new sentences than the edit has
    change(alt="Le recours est rejeté.", neu="Le recours est rejeté. La preuve est prépondérante. Les frais suivent."),
    change(alt="Le recours est rejeté.", neu="Le recours est admis.", unklar=True, hinweis="Peu clair."),
    "no json",
])
def test_an_answer_that_cannot_be_applied_safely_leaves_the_text_and_tells_the_lawyer(answer):
    answers = [answer, "still no json"] if isinstance(answer, str) else [answer]
    llm = FakeLLM(*answers, NO_DIFFERENCES)
    result = run(llm, DE.replace("hinreichender", "überwiegender"))
    assert result.text == FR
    assert any("à reprendre à la main" in note and "überwiegender" in note for note in result.notes)
    if isinstance(answer, str):
        assert llm.calls[1][2]["temperature"] == 0.3  # one retry with a little temperature
    elif answer["unklar"]:
        assert result.notes[0] == "Peu clair."


def test_closing_check_lists_remaining_differences_and_can_fail_or_be_skipped():
    old = "Le recours est rejeté."
    edit = change(alt=old, neu="Le recours est admis.")
    after = DE.replace("wies die Beschwerde ab", "hiess die Beschwerde gut")
    result = run(FakeLLM(edit, {"abweichungen": ["Seule la version allemande cite l'art. 6 LAA.", ""]}), after)
    assert result.notes == ["Seule la version allemande cite l'art. 6 LAA."]
    result = run(FakeLLM(edit, "no json", "no json"), after)
    assert result.text.endswith("admis.") and result.notes == [align._LABELS["fr"]["check_failed"]]
    llm = FakeLLM(edit)
    assert run(llm, after, final_check=False).notes == [] and len(llm.calls) == 1


def test_german_target_and_wrong_languages():
    llm = FakeLLM(change(alt="Das Bundesgericht wies die Beschwerde ab.", neu="Das Bundesgericht hiess die Beschwerde gut."),
                  NO_DIFFERENCES)
    result = align.align_edit(llm, target_lang="de", target_text=DE, source_lang="fr",
                              source_text=FR.replace("rejeté", "admis"), source_before=FR)
    assert result.text == DE.replace("wies die Beschwerde ab", "hiess die Beschwerde gut")
    assert "KEINE ÜBERSETZUNG" in llm.calls[0][0] and "nicht verfügbar" in llm.calls[0][1]
    with pytest.raises(ValueError):
        align.align_edit(llm, target_lang="de", target_text=DE, source_lang="de", source_text="a", source_before="b")
