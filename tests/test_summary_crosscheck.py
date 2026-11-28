"""Adversarial DE <-> FR check and repair (summary/crosscheck.py). No network: Apertus is faked."""

from __future__ import annotations

from hack_apertus_coffee_exe.summary import crosscheck as cc

DE = "Die Suva verweigerte eine Integritätsentschädigung von 15 %. Das Bundesgericht wies die Beschwerde des Versicherten am 3. März 2025 ab."
FR_SAME = "La Suva a refusé une indemnité pour atteinte à l'intégrité de 15 %. Le Tribunal fédéral a rejeté le recours de l'assuré le 3 mars 2025."
FR_FLIPPED = "La Suva a refusé une indemnité pour atteinte à l'intégrité de 15 %. Le Tribunal fédéral a admis le recours de l'assuré le 3 mars 2025."


def test_numbers_are_compared_language_independently():
    assert cc.numbers("am 21. März 2005, 10 % und 250 Fr., CHF 1'200") == {
        "datum": {"2005-03-21"}, "prozent": {"10"}, "betrag": {"250", "1200"},
    }
    assert cc.numbers("le 21 mars 2005, 10 % et 250 francs, 1 200 francs") == {
        "datum": {"2005-03-21"}, "prozent": {"10"}, "betrag": {"250", "1200"},
    }
    assert cc.number_conflicts(DE, FR_SAME) == []


def test_different_numbers_contradict_and_a_missing_number_only_warns():
    contradiction = cc.number_conflicts("Integritätsentschädigung von 10 %.", "indemnité de 15 %.")
    assert [(f.kind, f.topic, f.serious) for f in contradiction] == [("widerspruch", "zahl", True)]
    omission = cc.number_conflicts("Kosten von 250 Fr. trägt die Suva.", "La Suva supporte les frais.")
    assert [(f.kind, f.serious) for f in omission] == [("nur_de", False)]


class FakeApertus:
    """Answers the reviewer by a rule ("admis" in FR = contradiction) and the referee with ``verdict``."""

    def __init__(self, verdict="de", always_conflict=False):
        self.verdict, self.always_conflict, self.calls = verdict, always_conflict, []

    def __call__(self, system, user, schema, name):
        self.calls.append(name)
        if name == "schiedsrichter":
            return {"richtig": self.verdict, "beleg": "Das Dispositiv weist die Beschwerde ab."}
        flipped = "admis" in user or self.always_conflict
        if not flipped:
            return {"abweichungen": []}
        de_first = user.index("(DE)") < user.index("(FR)")
        q_de, q_fr = "wies die Beschwerde ab", "a admis le recours"
        return {"abweichungen": [{"art": "widerspruch", "thema": "ergebnis", "gewicht": "hoch",
                                  "text_1": q_de if de_first else q_fr, "text_2": q_fr if de_first else q_de}]}


def test_two_passes_confirm_a_finding_and_keep_quotes_per_language():
    findings = cc.review(FakeApertus(), DE, FR_FLIPPED)
    assert len(findings) == 1
    f = findings[0]
    assert (f.kind, f.topic, f.serious, f.confirmed) == ("widerspruch", "ergebnis", True, True)
    assert f.de == "wies die Beschwerde ab" and f.fr == "a admis le recours"   # quotes mapped back to their language


def test_a_finding_of_only_one_pass_is_not_confirmed():
    def one_sided(system, user, schema, name):
        if user.index("(DE)") < user.index("(FR)"):
            return {"abweichungen": [{"art": "nur_text_1", "thema": "sachverhalt", "gewicht": "hoch", "text_1": "x", "text_2": ""}]}
        return {"abweichungen": []}
    assert [(f.kind, f.confirmed) for f in cc.review(one_sided, DE, FR_SAME)] == [("nur_de", False)]


def test_the_judgement_decides_and_the_wrong_language_is_rewritten():
    fake, rewritten = FakeApertus(verdict="de"), []

    def rewrite(language, notes):
        rewritten.append((language, notes))
        return FR_SAME

    texts, issues, trace = cc.crosscheck_and_repair({"de": DE, "fr": FR_FLIPPED}, fake, "Dispositiv: abgewiesen", rewrite)
    assert [lang for lang, _ in rewritten] == ["fr"] and "Corrige la version française" in rewritten[0][1][0]
    assert texts == {"de": DE, "fr": FR_SAME} and trace["rewritten"] == ["fr"]
    assert issues == []
    assert fake.calls.count("schiedsrichter") == 1 and fake.calls.count("abweichungen") == 4   # 2 passes, again after the fix


def test_a_conflict_that_survives_the_rewrite_is_an_error_for_the_lawyer():
    texts, issues, trace = cc.crosscheck_and_repair(
        {"de": DE, "fr": FR_FLIPPED}, FakeApertus(verdict="de"), "Dispositiv", lambda lang, notes: FR_FLIPPED)
    assert [(i.language, i.severity, i.kind) for i in issues] == [("de/fr", "error", "de_fr_conflict")]
    assert trace["remaining"] == 1


def test_no_real_conflict_per_referee_means_no_rewrite_and_no_issue():
    rewrite_calls = []
    texts, issues, trace = cc.crosscheck_and_repair(
        {"de": DE, "fr": FR_SAME}, FakeApertus(verdict="beide", always_conflict=True), "Dispositiv",
        lambda lang, notes: rewrite_calls.append(lang) or "")
    assert rewrite_calls == [] and issues == [] and texts == {"de": DE, "fr": FR_SAME}


def test_a_failing_checker_never_blocks_the_summary():
    def broken(*_):
        raise TimeoutError("gateway")
    texts, issues, _ = cc.crosscheck_and_repair({"de": DE, "fr": FR_FLIPPED}, broken, "", lambda *a: "never")
    assert texts == {"de": DE, "fr": FR_FLIPPED}
    assert [(i.severity, i.kind) for i in issues] == [("warning", "crosscheck_failed")]


def test_to_fix_follows_the_verdict():
    f = cc.Finding("nur_de", "zahl", "250 Fr.", "", serious=True)
    f.verdict = "de"
    assert cc.to_fix(f) == {"fr"}                     # the statement belongs in FR too
    f.verdict = "keine"
    assert cc.to_fix(f) == {"de"}                     # not covered by the judgement: remove it from DE
    f.kind, f.verdict = "widerspruch", "fr"
    assert cc.to_fix(f) == {"de"}


def test_summarizer_runs_the_crosscheck_only_when_switched_on(monkeypatch):
    from types import SimpleNamespace

    from hack_apertus_coffee_exe.summary import generate
    from hack_apertus_coffee_exe.summary.check import Issue
    from hack_apertus_coffee_exe.summary.judgement import Judgement

    from datetime import date

    judgement = Judgement(case_id="8C_1/2026", text="Erwägungen:\n1. Text.\nDemnach erkennt das Bundesgericht:\n1. Die Beschwerde wird abgewiesen.",
                          language="de", decision_date=date(2026, 1, 5))
    seen = {}

    def fake_repair(texts, call, evidence, rewrite):
        seen["evidence"] = evidence
        return {**texts, "fr": rewrite("fr", ["note"])}, [Issue("de/fr", "error", "de_fr_conflict", "DE und FR widersprechen sich (ergebnis)")], {"rewritten": ["fr"]}

    monkeypatch.setattr("hack_apertus_coffee_exe.summary.crosscheck.crosscheck_and_repair", fake_repair)
    llm = SimpleNamespace(model="apertus-v1-5-70b", _client=None)
    examples = SimpleNamespace(select=lambda judgement, language: [])
    for switched_on in (False, True):
        s = generate.Summarizer(llm, examples, crosscheck=switched_on)
        monkeypatch.setattr(s, "analyse", lambda j: {"erwaegungen": ["1"]})
        monkeypatch.setattr(s, "faults", lambda text, language, j: [])
        monkeypatch.setattr(s, "write", lambda j, language, a, ex, notes=(): f"{language}-{'fixed' if notes else 'first'}")
        result = s.summarize(judgement)
        kinds = [i.kind for i in result.check.issues]
        if switched_on:
            assert result.summary_fr == "fr-fixed" and result.attempts["fr"] == 2 and "de_fr_conflict" in kinds
            assert "Dispositiv" in seen["evidence"] and not result.coherence_ok
        else:
            assert result.summary_fr == "fr-first" and "de_fr_conflict" not in kinds
