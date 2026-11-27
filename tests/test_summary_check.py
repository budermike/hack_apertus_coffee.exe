"""Rule-based consistency check (issue #16)."""

from pathlib import Path

from hack_apertus_coffee_exe.summary.check import (
    check_summaries,
    cited_articles,
    cited_considerations,
)
from hack_apertus_coffee_exe.summary.judgement import find_judgement, operative_outcome

J = find_judgement("8C_901/2026", Path("src/hack_apertus_coffee_exe/triage/mock_crawl"))
OUTCOME = operative_outcome(J.text)

GOOD_DE = (
    "Die Liste in Art. 6 Abs. 2 UVG ist abschliessend. Die unbegründete Einschätzung des Kreisarztes genügt nicht (E. 3).\n\n"
    "Die Beschwerde wird teilweise gutgeheissen und die Sache zur Einholung eines Gutachtens nach Art. 44 ATSG an die Suva zurückgewiesen."
)
GOOD_FR = (
    "La liste de l'art. 6 al. 2 LAA est exhaustive. L'avis non motivé du médecin d'arrondissement ne suffit pas (consid. 3).\n\n"
    "Le recours est partiellement admis et la cause renvoyée à la Suva pour expertise selon l'art. 44 LPGA."
)


def test_extracts_articles_across_languages():
    assert cited_articles(GOOD_DE) == {("6", "UVG"), ("44", "ATSG")}
    assert cited_articles(GOOD_FR) == {("6", "UVG"), ("44", "ATSG")}
    assert cited_considerations("vgl. (E. 4.4 und 5.1) sowie (consid. 2.3)") == {"4.4", "5.1", "2.3"}
    assert cited_considerations("(Erwägung 6.2)") == {"6.2"}


def test_good_pair_passes():
    report = check_summaries(GOOD_DE, GOOD_FR, J, OUTCOME)
    assert report.ok, report.to_dicts()
    assert report.issues == []


def test_invented_consideration_and_article_are_errors():
    bad = GOOD_DE.replace("(E. 3)", "(E. 7.2)").replace("Art. 44 ATSG", "Art. 43 ATSG")
    report = check_summaries(bad, GOOD_FR, J, OUTCOME)
    kinds = {i.kind for i in report.errors_for("de")}
    assert kinds == {"consideration", "article"}
    assert not report.ok
    assert any(i.kind == "article_mismatch" for i in report.issues)


def test_wrong_outcome_in_closing_paragraph():
    bad = GOOD_FR.replace("Le recours est partiellement admis et la cause renvoyée", "Le recours est rejeté et la cause renvoyée")
    report = check_summaries(GOOD_DE, bad, J, OUTCOME)
    assert [i.kind for i in report.errors_for("fr")] == ["outcome"]


def test_empty_summary_is_error():
    report = check_summaries("", GOOD_FR, J, OUTCOME)
    assert [i.kind for i in report.errors_for("de")] == ["empty"]
