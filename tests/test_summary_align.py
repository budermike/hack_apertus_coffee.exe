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


@pytest.fixture(autouse=True)
def no_back_check(monkeypatch):
    """The reverse question before a sentence is touched has its own tests below."""
    monkeypatch.setattr(align, "BACK_CHECK", False)
    # sentence by sentence only; writing the whole text anew is tested at the end
    monkeypatch.setattr(align, "WHOLE_TEXT", False)


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
    """A German edit carried over to the French text; the notes are German, the language of the edit."""
    return align.align_edit(llm, target_lang="fr", target_text=FR, source_lang="de", source_text=de_after,
                            source_before=DE, **kwargs)


NOT_CARRIED_OVER = "von Hand nachführen"


def test_split_sentences_keeps_abbreviations_together_and_loses_nothing():
    parts = align.split_sentences(DE)
    assert [sentence[:20] for sentence, _ in parts] == [
        "Das Bundesgericht hä", "Die Suva muss den Be", "Das Bundesgericht wi"]
    assert parts[1][1] == "\n\n"
    assert "".join(sentence + gap for sentence, gap in parts) == DE
    assert len(align.split_sentences("Vgl. Art. 4 ATSG und E. 3.1. Am 12. März 2025 entschied B.________ anders.")) == 2
    assert len(align.split_sentences("Das Gutachten des Dr. med. D.________ vom 25. Juni 2025 ist beweiskräftig. Darauf ist abzustellen.")) == 2
    # a year or an amount at the end of a sentence ends it; an abbreviation of a statute too
    french = "L'accident est survenu le 3 mai 2021. La Suva a nié son obligation de prester. Voir l'art. 29 al. 2 Cst. Le recours est rejeté."
    assert [sentence[:10] for sentence, _ in align.split_sentences(french)] == ["L'accident", "La Suva a ", "Voir l'art", "Le recours"]


def test_source_changes_list_every_edited_sentence_on_its_own():
    after = DE.replace("hinreichender", "überwiegender").replace("\n\nDas Bundesgericht wies die Beschwerde ab.", "")
    changes = align.source_changes(DE, after + " Die Kosten trägt die Suva.")
    assert [(c["before"][:16], c["after"][:16], c["removed"], c["added"]) for c in changes] == [
        ("Die Suva muss de", "Die Suva muss de", "hinreichender", "überwiegender"),        # reworded
        ("Das Bundesgerich", "", "Das Bundesgericht wies die Beschwerde ab.", ""),            # removed
        ("", "Die Kosten trägt", "", "Die Kosten trägt die Suva."),                           # new
    ]
    assert align.source_changes(DE, DE) == []
    assert align.source_changes(DE, DE + "\n") == []
    # a removed passage of several sentences: one change per sentence, so each can be placed on its own
    removed = align.source_changes("Eins bleibt hier. Zwei geht weg. Drei geht auch weg.", "Eins bleibt hier.")
    assert [(c["before"], c["after"]) for c in removed] == [("Zwei geht weg.", ""), ("Drei geht auch weg.", "")]


def test_source_changes_ignore_spaces_and_do_not_pair_unrelated_sentences():
    assert align.source_changes("Die Suva  haftet. Punkt zwei.", "Die Suva haftet. Punkt zwei.") == []
    changes = align.source_changes("Erster Satz zur Kausalität. Zweiter Satz zum Taggeld.",
                                   "Ganz neuer Einstieg ohne Bezug. Erster Satz zur Kausalität, leicht ergänzt.")
    assert [(c["before"][:11], c["after"][:10]) for c in changes] == [
        ("", "Ganz neuer"), ("Erster Satz", "Erster Sat"), ("Zweiter Sat", "")]


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
    result = run(llm, DE.replace("hinreichender", "überwiegender"), judgement=JUDGEMENT, final_check=True)
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
    # carried over all the same, and said so in German (the language of the edit) with Apertus' reason
    assert len(result.notes) == 1 and result.notes[0].startswith("Übertragen, widerspricht aber nach Apertus dem Urteilstext")
    assert result.notes[0].endswith("Selon l'arrêt, le recours est rejeté.")


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
    # the edit changed one sentence, the answer makes two of it
    change(alt="Le recours est rejeté.", neu="Le recours est admis. La cause est renvoyée à la Suva."),
    # says "substance" but names no sentence: the lawyer is told, the change is not dropped silently
    change(alt="", neu=""),
    change(alt="Le recours est rejeté.", neu="Le recours est admis.", unklar=True, hinweis="Peu clair."),
    change(alt="Le recours est rejeté.", neu="Le recours est admis.", unklar=True),  # no reason given: a default note
    "no json",
])
def test_an_answer_that_cannot_be_applied_safely_leaves_the_text_and_tells_the_lawyer(answer):
    answers = [answer, "still no json"] if isinstance(answer, str) else [answer]
    llm = FakeLLM(*answers, NO_DIFFERENCES)
    result = run(llm, DE.replace("hinreichender", "überwiegender"))
    assert result.text == FR
    assert len(result.notes) == 1 and "überwiegender" in result.notes[0]
    if isinstance(answer, str):
        assert llm.calls[1][2]["temperature"] == 0.3  # one retry with a little temperature
    if not isinstance(answer, str) and answer["unklar"]:
        assert result.notes[0].startswith("Apertus hält diese Änderung für unklar") and result.notes[0].endswith(answer["hinweis"] or "»")
    else:
        assert NOT_CARRIED_OVER in result.notes[0]


def test_closing_check_lists_remaining_differences_and_can_fail_or_be_skipped():
    old = "Le recours est rejeté."
    edit = change(alt=old, neu="Le recours est admis.")
    after = DE.replace("wies die Beschwerde ab", "hiess die Beschwerde gut")
    llm = FakeLLM(edit)
    assert run(llm, after).notes == [] and len(llm.calls) == 1  # off by default: no list of differences
    llm = FakeLLM(edit, {"abweichungen": ["Nur die französische Fassung nennt Art. 6 UVG.", ""]})
    assert run(llm, after, final_check=True).notes == ["Nur die französische Fassung nennt Art. 6 UVG."]
    assert "vergleichst die deutsche und die französische" in llm.calls[1][0]  # asked in the language of the edit
    result = run(FakeLLM(edit, "no json", "no json"), after, final_check=True)
    assert result.text.endswith("admis.") and result.notes == [align._LABELS["de"]["check_failed"]]


def test_german_target_and_wrong_languages():
    llm = FakeLLM(change(alt="Das Bundesgericht wies die Beschwerde ab.", neu="Das Bundesgericht hiess die Beschwerde gut."),
                  NO_DIFFERENCES)
    result = align.align_edit(llm, target_lang="de", target_text=DE, source_lang="fr",
                              source_text=FR.replace("rejeté", "admis"), source_before=FR)
    assert result.text == DE.replace("wies die Beschwerde ab", "hiess die Beschwerde gut")
    assert "KEINE ÜBERSETZUNG" in llm.calls[0][0] and "nicht verfügbar" in llm.calls[0][1]
    with pytest.raises(ValueError):
        align.align_edit(llm, target_lang="de", target_text=DE, source_lang="de", source_text="a", source_before="b")


def test_a_quote_that_skips_part_of_the_sentence_does_not_cost_that_part():
    target = "La Suva doit apporter une preuve suffisante, à défaut elle reste tenue de prester. Le recours est rejeté."
    llm = FakeLLM(change(alt="La Suva doit apporter une preuve suffisante.", neu="La Suva doit apporter une preuve prépondérante."),
                  NO_DIFFERENCES)
    result = align.align_edit(llm, target_lang="fr", target_text=target, source_lang="de",
                              source_text=DE.replace("hinreichender", "überwiegender"), source_before=DE)
    assert result.text == target and NOT_CARRIED_OVER in result.notes[0]


def test_whole_sentences_go_only_when_the_edit_removed_a_whole_passage():
    # the German edit removed three words inside a sentence; the answer wants to drop a French sentence
    llm = FakeLLM(change(alt="La Suva doit apporter la preuve avec une vraisemblance suffisante.", neu=""), NO_DIFFERENCES)
    result = run(llm, DE.replace(" mit hinreichender Wahrscheinlichkeit", ""))
    assert result.text == FR and NOT_CARRIED_OVER in result.notes[0]


def test_every_change_that_was_not_carried_over_is_reported_and_calls_are_limited():
    before = " ".join(f"Satz {n} betrifft die Leistungspflicht Nummer {n}{n}." for n in range(10, 26))
    after = before.replace("Leistungspflicht", "Kausalität")
    llm = FakeLLM(*[change(alt="Cette phrase n'existe pas ici.", neu="Autre.")] * align.MAX_CHANGES,
                  {"abweichungen": [f"Différence {n}." for n in range(9)]})
    result = align.align_edit(llm, target_lang="fr", target_text=FR, source_lang="de", source_text=after,
                              source_before=before, final_check=True)
    assert len(llm.calls) == align.MAX_CHANGES + 1
    reported = [n for n in result.notes if NOT_CARRIED_OVER in n]
    assert len(reported) == align.MAX_CHANGES + 1  # every change, and the note that more were edited
    assert len([n for n in result.notes if n.startswith("Différence")]) == align.MAX_CHECK_NOTES


def test_a_gateway_error_in_the_closing_check_keeps_what_was_carried_over():
    class Failing(FakeLLM):
        def chat(self, system, user, **kwargs):
            if len(self.calls) == 1:
                raise TimeoutError("gateway")
            return super().chat(system, user, **kwargs)

    result = run(Failing(change(alt="Le recours est rejeté.", neu="Le recours est admis.")),
                 DE.replace("wies die Beschwerde ab", "hiess die Beschwerde gut"), final_check=True)
    assert result.text.endswith("Le recours est admis.") and result.notes == [align._LABELS["de"]["check_failed"]]


def test_reference_is_reported_even_when_nothing_changed_and_an_emptied_text_does_not_break():
    assert run(FakeLLM(), DE + "  ", judgement=JUDGEMENT).has_reference is True
    llm = FakeLLM(change(alt="Un.", neu=""), change(alt="Un.", neu="Deux."), NO_DIFFERENCES)
    result = align.align_edit(llm, target_lang="fr", target_text="Un.", source_lang="de",
                              source_text="Neu eins hier.", source_before="Alt eins hier. Alt zwei hier.")
    assert isinstance(result.text, str)


def test_a_loose_quote_changes_only_the_words_that_differ():
    # Apertus quotes the sentence without «de l'assuré»: those words stay
    target = "La présomption n'a pas été renversée. Le recours de l'assuré est rejeté."
    llm = FakeLLM(change(alt="Le recours est rejeté.", neu="Le recours est admis."), NO_DIFFERENCES)
    result = align.align_edit(llm, target_lang="fr", target_text=target, source_lang="de",
                              source_text=DE.replace("wies die Beschwerde ab", "hiess die Beschwerde gut"), source_before=DE)
    assert result.text == "La présomption n'a pas été renversée. Le recours de l'assuré est admis."


def test_a_shortened_text_is_shortened_in_the_other_language_without_notes():
    # as reported from the UI on 28.11.: three German sentences removed at the end; what says the
    # same in French stands in three different paragraphs
    de_before = ("Das Valideneinkommen ist nach den Löhnen des Gesamtarbeitsvertrags zu bestimmen. "
                 "Im vorliegenden Fall ist der Mindestlohn eines Elektromonteurs EFZ gemäss Gesamtarbeitsvertrag massgeblich. "
                 "Da der Versicherte nicht mehr bei seiner bisherigen Arbeitgeberin beschäftigt war, kann daran nicht angeknüpft werden. "
                 "Die Beschwerde der Suva wird gutgeheissen und der Einspracheentscheid bestätigt.")
    de_after = "Das Valideneinkommen ist nach den Löhnen des Gesamtarbeitsvertrags zu bestimmen."
    minimum = "En l'espèce, le salaire minimum d'un électromonteur CFC prévu par la CCT est déterminant."
    employer = "Par ailleurs, l'assuré n'était plus employé par son ancien employeur, de sorte qu'il ne peut y être fait référence."
    outcome = "En définitive, le recours de la Suva est admis et la décision sur opposition confirmée."
    fr = (f"Le revenu sans invalidité se détermine selon les salaires de la CCT. {minimum}\n\n"
          f"Le Tribunal fédéral a rappelé les principes applicables. {employer}\n\n{outcome}")
    llm = FakeLLM(change(alt=minimum, neu=""), change(alt=employer, neu=""), change(alt=outcome, neu=""))
    result = align.align_edit(llm, target_lang="fr", target_text=fr, source_lang="de", source_text=de_after, source_before=de_before)
    assert result.text == ("Le revenu sans invalidité se détermine selon les salaires de la CCT.\n\n"
                           "Le Tribunal fédéral a rappelé les principes applicables.")
    assert result.notes == [] and len(llm.calls) == 3
    system, user, kwargs = llm.calls[0]
    # a small question of its own: the French text and the removed German sentence, no judgement text
    assert "Phrase allemande supprimée" in user and "Elektromonteurs" in user and "Résumé français" in user
    assert kwargs["response_format"]["json_schema"]["schema"] is align.DROP_SCHEMA and kwargs["max_tokens"] == 300
    # what Apertus really answers (28.11.): the right sentence, handed back unchanged, once even
    # marked "wording only". The sentence is removed all the same.
    llm = FakeLLM(change(alt=minimum, neu=minimum, inhaltlich=False), change(alt=employer, neu=employer), change(alt=outcome, neu=""))
    again = align.align_edit(llm, target_lang="fr", target_text=fr, source_lang="de", source_text=de_after, source_before=de_before)
    assert again.text == result.text and again.notes == []
    # a sentence Apertus makes up is not in the text: nothing is removed, the lawyer is told in German
    llm = FakeLLM(change(alt="Cette phrase inventée ne figure nulle part dans le résumé.", neu=""), change(alt=employer, neu=""), change(alt=outcome, neu=""))
    partly = align.align_edit(llm, target_lang="fr", target_text=fr, source_lang="de", source_text=de_after, source_before=de_before)
    assert minimum in partly.text and employer not in partly.text
    assert len(partly.notes) == 1 and NOT_CARRIED_OVER in partly.notes[0] and "Elektromonteurs" in partly.notes[0]


def test_a_removed_sentence_the_other_language_never_had_is_no_note():
    llm = FakeLLM(change(alt="", neu=""))
    result = run(llm, DE.replace("\n\nDas Bundesgericht wies die Beschwerde ab.", ""))
    assert result.text == FR and result.notes == []


def test_a_gateway_error_keeps_what_was_carried_over_and_reports_the_rest():
    class Failing(FakeLLM):
        def __init__(self, *answers, fail_from):
            super().__init__(*answers)
            self.fail_from = fail_from

        def chat(self, system, user, **kwargs):
            if len(self.calls) >= self.fail_from:
                raise TimeoutError("gateway")
            return super().chat(system, user, **kwargs)

    after = DE.replace("hinreichender", "überwiegender").replace("wies die Beschwerde ab", "hiess die Beschwerde gut")
    first = change(alt="La Suva doit apporter la preuve avec une vraisemblance suffisante.",
                   neu="La Suva doit apporter la preuve avec une vraisemblance prépondérante.")
    result = run(Failing(first, fail_from=1), after)
    assert "prépondérante" in result.text and "rejeté" in result.text
    assert len(result.notes) == 1 and NOT_CARRIED_OVER in result.notes[0] and "hiess die Beschwerde gut" in result.notes[0]
    with pytest.raises(TimeoutError):  # nothing carried over yet: the API reports that Apertus could not be reached
        run(Failing(fail_from=0), after)
    assert FakeLLM(first).chat("s", "u", timeout=1) and align.CALL_TIMEOUT == 60


def test_a_sentence_is_touched_only_when_the_reverse_question_names_the_edited_sentence():
    old = "La Suva doit apporter la preuve avec une vraisemblance suffisante."
    edit = change(alt=old, neu=old.replace("suffisante", "prépondérante"))
    after = DE.replace("hinreichender", "überwiegender")
    # the edited German sentence is the second of three: Apertus confirms it for the French sentence
    llm = FakeLLM(edit, {"satz": 2})
    result = run(llm, after, back_check=True)
    assert "prépondérante" in result.text and result.notes == []
    system, user, kwargs = llm.calls[1]
    assert "Phrase française" in user and old in user and "[2] Die Suva muss den Beweis mit hinreichender" in user
    assert kwargs["response_format"]["json_schema"]["name"] == "gegenprobe" and kwargs["max_tokens"] == 20
    # Apertus names another German sentence: the French sentence belongs to something that was not edited
    for answer in ({"satz": 1}, {"satz": 0}, "no json"):
        answers = [answer, answer] if isinstance(answer, str) else [answer]
        result = run(FakeLLM(edit, *answers), after, back_check=True)
        assert result.text == FR and len(result.notes) == 1 and NOT_CARRIED_OVER in result.notes[0]
    # the same for a removed sentence: the wrong French sentence is not removed
    removed = DE.replace("\n\nDas Bundesgericht wies die Beschwerde ab.", "")
    wrong = run(FakeLLM(change(alt=old, neu=""), {"satz": 2}), removed, back_check=True)
    assert wrong.text == FR and NOT_CARRIED_OVER in wrong.notes[0]
    right = run(FakeLLM(change(alt="Le recours est rejeté.", neu=""), {"satz": 3}), removed, back_check=True)
    assert "rejeté" not in right.text and right.notes == []
    # a new sentence has no counterpart to ask about
    llm = FakeLLM(change(alt="Le recours est rejeté.", neu="Le recours est rejeté. Les frais sont à la charge de la Suva."))
    assert run(llm, DE + " Die Kosten trägt die Suva.", back_check=True).text.endswith("de la Suva.") and len(llm.calls) == 1


def test_a_changed_date_or_amount_is_never_wording_only_and_loose_quotes_are_accepted():
    target = "La Suva a cessé les prestations au 31 mars 2024. Le recours est rejeté."
    before = "Die Suva stellte die Leistungen per 31. März 2024 ein. Die Beschwerde wird abgewiesen."
    after = "Die Suva stellte die Leistungen ein. Die Beschwerde wird abgewiesen."
    # Apertus calls it wording only: the date goes all the same
    llm = FakeLLM(change(alt="La Suva a cessé les prestations au 31 mars 2024.", neu="La Suva a cessé les prestations.", inhaltlich=False))
    result = align.align_edit(llm, target_lang="fr", target_text=target, source_lang="de", source_text=after, source_before=before)
    assert result.text == "La Suva a cessé les prestations. Le recours est rejeté." and result.notes == []
    # a removed sentence that Apertus quotes only roughly is still found
    llm = FakeLLM({"alt": "La Suva a mis fin aux prestations le 31 mars 2024."})
    result = align.align_edit(llm, target_lang="fr", target_text=target, source_lang="de",
                              source_text="Die Beschwerde wird abgewiesen.", source_before=before)
    assert result.text == "Le recours est rejeté." and result.notes == []


def test_removed_or_added_sentences_let_the_other_language_be_written_anew():
    # default behaviour: the German text was cut to its first sentence
    anew = "Le Tribunal fédéral retient que l'art. 6 al. 2 LAA exige une lésion figurant dans la liste (consid. 4.2)."
    llm = FakeLLM(anew)
    result = run(llm, DE.split(" Die Suva")[0], whole_text=True, judgement=JUDGEMENT)
    assert result.text == anew and result.notes == [] and result.has_reference is True
    system, user, kwargs = llm.calls[0]
    assert len(llm.calls) == 1 and "ni plus ni moins" in system and "response_format" not in kwargs
    assert kwargs["timeout"] == align.RENDER_TIMEOUT
    # only the edited German text goes along: with the old French text Apertus keeps its sentences
    assert "Version allemande modifiée" in user and "Das Bundesgericht hält fest" in user and "Le recours est rejeté" not in user
    # a new sentence, too
    assert run(FakeLLM("Neu."), DE + " Die Kosten trägt die Suva.", whole_text=True).text == "Neu."
    with pytest.raises(ValueError):
        run(FakeLLM("  "), DE + " Die Kosten trägt die Suva.", whole_text=True)
    # and the other way round: a French text cut short gives a German text written anew, asked in German
    llm = FakeLLM("Das Bundesgericht hält fest, dass Art. 6 Abs. 2 UVG eine Listenverletzung verlangt (E. 4.2).")
    result = align.align_edit(llm, target_lang="de", target_text=DE, source_lang="fr",
                              source_text=FR.split(" La Suva")[0], source_before=FR, whole_text=True)
    assert result.text.startswith("Das Bundesgericht hält fest") and result.notes == []
    assert "nicht mehr und nicht weniger" in llm.calls[0][0] and "Bearbeitete französische Fassung" in llm.calls[0][1]


def test_an_edit_inside_a_sentence_is_still_carried_over_at_its_place_and_the_rest_stays():
    old = "La Suva doit apporter la preuve avec une vraisemblance suffisante."
    llm = FakeLLM(change(alt=old, neu=old.replace("suffisante", "prépondérante")))
    result = run(llm, DE.replace("hinreichender", "überwiegender"), whole_text=True)
    assert result.text == FR.replace("suffisante", "prépondérante") and len(llm.calls) == 1
    # wording only: nothing happens, nothing is written anew
    llm = FakeLLM(change(inhaltlich=False))
    assert run(llm, DE.replace("hält fest", "hat festgehalten"), whole_text=True).text == FR and len(llm.calls) == 1


def test_an_edit_that_cannot_be_placed_lets_the_text_be_written_anew_instead_of_a_note():
    llm = FakeLLM(change(alt="Cette phrase ne figure pas dans le texte.", neu="Autre phrase."), "Texte français entièrement réécrit.")
    result = run(llm, DE.replace("hinreichender", "überwiegender"), whole_text=True)
    assert result.text == "Texte français entièrement réécrit." and result.notes == []
    # more reworded sentences than MAX_SENTENCE_EDITS: written anew at once
    before = " ".join(f"Satz {n} betrifft die Leistungspflicht Nummer {n}{n}." for n in range(10, 16))
    llm = FakeLLM("Six phrases.")
    result = align.align_edit(llm, target_lang="fr", target_text=FR, source_lang="de",
                              source_text=before.replace("Leistungspflicht", "Kausalität"), source_before=before, whole_text=True)
    assert result.text == "Six phrases." and len(llm.calls) == 1
