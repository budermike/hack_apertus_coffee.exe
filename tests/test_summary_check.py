"""Rule-based consistency check (issue #16)."""

from pathlib import Path

from hack_apertus_coffee_exe.summary.check import (
    check_summaries,
    cited_articles,
    cited_considerations,
)
from hack_apertus_coffee_exe.summary.judgement import Judgement, find_judgement, operative_outcome

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


# Real case 8C_221/2025: partially admitted (CHF 250 report costs), rejected for the rest, no remand.
PARTIAL_JUDGEMENT = Judgement(
    case_id="8C_221/2025",
    language="fr",
    text=(
        "Considérant en droit :\n4.\nBla.\n6.\n6.1\nBla.\n7.\nBla.\n"
        "Par ces motifs, le Tribunal fédéral prononce :\n1.\nLe recours est partiellement admis. "
        "La CNA est tenue au remboursement des frais du rapport. Le recours est rejeté pour le surplus.\n2.\nFrais."
    ),
)
PARTIAL_OUTCOME = operative_outcome(PARTIAL_JUDGEMENT.text)


def test_partial_outcome_described_as_rejected_for_the_rest_is_ok():
    de = "Bla (E. 6.1).\n\nDie Suva muss die Kosten des Berichts übernehmen. Im Übrigen wird die Beschwerde abgewiesen."
    fr = "Bla (consid. 6.1).\n\nLe recours est rejeté, à l'exception d'une admission partielle concernant les frais du rapport."
    report = check_summaries(de, fr, PARTIAL_JUDGEMENT, PARTIAL_OUTCOME)
    assert report.ok, report.to_dicts()


def test_invented_remand_is_an_error():
    de = (
        "Das Bundesgericht hob den Entscheid auf und wies die Sache zur weiteren Abklärung zurück.\n\n"
        "Im Übrigen wird die Beschwerde abgewiesen."
    )
    fr = "Le recours est partiellement admis et rejeté pour le surplus."
    report = check_summaries(de, fr, PARTIAL_JUDGEMENT, PARTIAL_OUTCOME)
    assert [i.kind for i in report.errors_for("de")] == ["remand"]
    assert report.errors_for("fr") == []
