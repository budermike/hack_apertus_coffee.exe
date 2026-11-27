"""Judgement loading, titles, excerpts and outcome (no network)."""

from datetime import date
from pathlib import Path

from hack_apertus_coffee_exe.summary.judgement import (
    Judgement,
    consideration_numbers,
    consideration_passages,
    excerpt,
    find_judgement,
    load_judgements,
    operative_outcome,
    same_case,
    title_de,
    title_fr,
)

MOCK = Path("src/hack_apertus_coffee_exe/triage/mock_crawl")


def test_load_mock_crawl_records():
    judgements = load_judgements(MOCK)
    j = next(j for j in judgements if j.case_id == "8C_901/2026")
    assert j.language == "de"
    assert j.decision_date == date(2026, 10, 14)
    assert "Demnach erkennt das Bundesgericht" in j.text


def test_find_accepts_both_id_spellings():
    assert find_judgement("8C_901_2026", MOCK).case_id == "8C_901/2026"
    assert same_case("8c_7/2025", "8C_7_2025")


def test_titles_follow_suva_format():
    j = Judgement(case_id="8C_7/2025", text="", language="fr", decision_date=date(2025, 9, 1))
    assert title_de(j) == "BGer-Urteil 8C_7/2025 vom 1. September 2025"
    assert title_fr(j) == "Arrêt du TF 8C_7/2025 du 1er septembre 2025"


def test_considerations_and_outcome_from_mock():
    j = find_judgement("8C_901/2026", MOCK)
    assert consideration_numbers(j.text) == {"1", "2", "3"}
    outcome = operative_outcome(j.text)
    assert outcome.result == "teilweise gutgeheissen"
    assert outcome.remand is True
    assert "Art. 44 ATSG" in consideration_passages(j.text, ["3"])


def test_outcome_french_and_rejected():
    text = "Considérant en droit :\n1. Bla.\n\nPar ces motifs, le Tribunal fédéral prononce :\n1. Le recours est rejeté.\n2. Frais."
    assert operative_outcome(text).result == "abgewiesen"
    assert operative_outcome("kein Dispositiv").result is None


def test_excerpt_keeps_operative_part():
    text = "Sachverhalt:\n" + "A " * 5000 + "\nErwägungen:\n" + "1. B " * 20000 + "\nDemnach erkennt das Bundesgericht:\n1. Die Beschwerde wird abgewiesen."
    out = excerpt(text, max_chars=8000)
    assert len(out) <= 8100
    assert "Die Beschwerde wird abgewiesen." in out
    assert "Erwägungen:" in out


def test_outcome_single_judge_and_joined_cases():
    single = "Erwägung:\n1. Bla.\nDemnach erkennt die Präsidentin:\n1.\nAuf die Beschwerde wird nicht eingetreten.\n2.\nKosten."
    assert operative_outcome(single).result == "nicht eingetreten"
    joined = (
        "Considérant en droit :\n1. Bla.\nPar ces motifs, le Tribunal fédéral prononce :\n"
        "1.\nLes causes 8C_1/2026 et 8C_2/2026 sont jointes.\n2.\nLes recours sont partiellement admis.\n3.\nFrais."
    )
    assert operative_outcome(joined).result == "teilweise gutgeheissen"
    phrase = "Demnach erkennt das Bundesgericht:\n1.\nIn Gutheissung der Beschwerde wird das Urteil aufgehoben."
    assert operative_outcome(phrase).result == "gutgeheissen"
