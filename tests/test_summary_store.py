"""Tests for the summaries knowledge base (no network, LLM is mocked)."""

import sqlite3
from unittest.mock import Mock

import pytest

from hack_apertus_coffee_exe.summary import store
from hack_apertus_coffee_exe.summary.store import (
    GeneratedSummary,
    InvalidTransitionError,
    SummaryExistsError,
)

CASE = "8C_129/2025"
JOINED_MAIN = "1D_1/2025"
JOINED_OTHER = "1D_2/2025"

GENERATED = GeneratedSummary(
    text="Modell-Summary", model="apertus-v1-5-70b", prompt_version="v1", coherence_ok=True
)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "summaries.db"


@pytest.fixture
def conn(db_path):
    connection = store.connect(db_path)
    store.save_case(connection, CASE, "2025-06-01", "https://search.bger.ch/8C_129")
    store.save_case(
        connection,
        JOINED_MAIN,
        "2026-04-01",
        "https://search.bger.ch/1D_1",
        case_numbers=[JOINED_MAIN, JOINED_OTHER],
    )
    yield connection
    connection.close()


# --- Setup & cases --------------------------------------------------------------


def test_connect_creates_file_and_is_idempotent(db_path):
    store.connect(db_path).close()
    conn = store.connect(db_path)  # second init must not fail
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"cases", "summaries", "case_numbers"} <= tables
    assert db_path.exists()
    conn.close()


def test_db_path_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv(store.DB_PATH_ENV, str(tmp_path / "x.db"))
    assert store.db_path_from_env() == tmp_path / "x.db"
    monkeypatch.delenv(store.DB_PATH_ENV)
    assert store.db_path_from_env() == store.DEFAULT_DB_PATH


def test_every_case_number_resolves_to_main_case(conn):
    assert store.resolve_case_number(conn, JOINED_MAIN) == JOINED_MAIN
    assert store.resolve_case_number(conn, JOINED_OTHER) == JOINED_MAIN
    assert store.resolve_case_number(conn, "9C_999/2025") is None


def test_get_case_returns_source_url(conn):
    case = store.get_case(conn, JOINED_OTHER)
    assert case.case_id == JOINED_MAIN
    assert case.url == "https://search.bger.ch/1D_1"
    assert case.publication_date is None


def test_case_number_cannot_belong_to_two_cases(conn):
    with pytest.raises(ValueError):
        store.save_case(conn, "1D_5/2025", "2026-01-01", "u", case_numbers=[JOINED_OTHER])
    assert store.resolve_case_number(conn, JOINED_OTHER) == JOINED_MAIN
    assert store.get_case(conn, "1D_5/2025") is None  # whole save rolled back


def test_save_case_from_crawl_record(conn):
    record = {
        "case_id": "4A_1/2026",
        "case_ids": ["4A_1/2026", "4A_2/2026"],
        "decision_date": "2026-03-01",
        "publication_date": None,
        "url": "https://search.bger.ch/4A_1",
        "text": "...",
    }
    store.save_case_from_crawl(conn, record)
    assert store.get_case(conn, "4A_2/2026").url == "https://search.bger.ch/4A_1"


def test_summary_for_unknown_case_is_rejected(conn):
    with pytest.raises(LookupError):
        store.save_generated(conn, "9C_999/2025", "de", GENERATED)


# --- Generating -----------------------------------------------------------------


def test_save_generated_stores_version_1(conn):
    s = store.save_generated(conn, CASE, "de", GENERATED)
    assert (s.version, s.status, s.model, s.prompt_version) == (1, "generated", "apertus-v1-5-70b", "v1")
    assert s.coherence_ok is True


def test_generated_at_most_once(conn):
    store.save_generated(conn, CASE, "de", GENERATED)
    with pytest.raises(SummaryExistsError):
        store.save_generated(conn, CASE, "de", GENERATED)


def test_languages_are_independent(conn):
    store.save_generated(conn, CASE, "de", GENERATED)
    store.save_generated(conn, CASE, "fr", GENERATED)
    assert len(store.list_display(conn)) == 2


def test_invalid_language_rejected(conn):
    with pytest.raises(ValueError):
        store.save_generated(conn, CASE, "it", GENERATED)


def test_generated_summary_cannot_claim_human(conn):
    with pytest.raises(ValueError):
        store.save_generated(conn, CASE, "de", GeneratedSummary("t", "human", None, None))


def test_get_or_generate_calls_llm_only_once(conn):
    generate = Mock(return_value=GENERATED)
    first = store.get_or_generate(conn, CASE, "de", generate)
    second = store.get_or_generate(conn, CASE, "de", generate)
    assert generate.call_count == 1
    assert first == second


def test_joined_case_has_one_summary_for_all_numbers(conn):
    generate = Mock(return_value=GENERATED)
    store.get_or_generate(conn, JOINED_MAIN, "de", generate)
    via_other = store.get_or_generate(conn, JOINED_OTHER, "de", generate)
    assert generate.call_count == 1
    assert via_other.case_id == JOINED_MAIN


def test_restart_shows_summaries_without_llm_call(db_path, conn):
    """Acceptance criterion: after a restart everything is there, no new LLM call."""
    store.get_or_generate(conn, CASE, "de", Mock(return_value=GENERATED))
    store.get_or_generate(conn, CASE, "fr", Mock(return_value=GENERATED))
    conn.close()

    reopened = store.connect(db_path)
    generate = Mock(side_effect=AssertionError("LLM must not be called"))
    assert store.get_or_generate(reopened, CASE, "de", generate).text == "Modell-Summary"
    assert len(store.list_display(reopened)) == 2
    generate.assert_not_called()
    reopened.close()


# --- Editing & approving --------------------------------------------------------


def test_edit_is_new_version_marked_human(conn):
    store.save_generated(conn, CASE, "de", GENERATED)
    edited = store.save_edit(conn, CASE, "de", "Korrigiert")
    assert (edited.version, edited.status, edited.model) == (2, "edited", "human")
    assert edited.prompt_version is None
    assert edited.coherence_ok is None


def test_edit_without_summary_fails(conn):
    with pytest.raises(InvalidTransitionError):
        store.save_edit(conn, CASE, "de", "x")


def test_generated_summary_cannot_be_approved(conn):
    store.save_generated(conn, CASE, "de", GENERATED)
    with pytest.raises(InvalidTransitionError):
        store.approve(conn, CASE, "de")


def test_approve_copies_edited_text(conn):
    store.save_generated(conn, CASE, "de", GENERATED)
    store.save_edit(conn, CASE, "de", "Korrigiert")
    approved = store.approve(conn, CASE, "de")
    assert (approved.version, approved.status, approved.model) == (3, "approved", "human")
    assert approved.text == "Korrigiert"


def test_cannot_approve_twice(conn):
    store.save_generated(conn, CASE, "de", GENERATED)
    store.save_edit(conn, CASE, "de", "Korrigiert")
    store.approve(conn, CASE, "de")
    with pytest.raises(InvalidTransitionError):
        store.approve(conn, CASE, "de")


def test_history_keeps_all_versions(conn):
    store.save_generated(conn, CASE, "de", GENERATED)
    store.save_edit(conn, CASE, "de", "Korrigiert")
    store.approve(conn, CASE, "de")
    history = store.get_history(conn, CASE, "de")
    assert [s.status for s in history] == ["generated", "edited", "approved"]
    assert history[0].text == "Modell-Summary"  # original never overwritten


# --- Display --------------------------------------------------------------------


def test_display_is_generated_while_edit_pending(conn):
    store.save_generated(conn, CASE, "de", GENERATED)
    store.save_edit(conn, CASE, "de", "Entwurf")
    assert store.get_display(conn, CASE, "de").status == "generated"
    assert store.get_pending_edit(conn, CASE, "de").text == "Entwurf"


def test_display_prefers_newest_approved(conn):
    store.save_generated(conn, CASE, "de", GENERATED)
    store.save_edit(conn, CASE, "de", "Erste Freigabe")
    store.approve(conn, CASE, "de")
    store.save_edit(conn, CASE, "de", "Zweite Freigabe")
    store.approve(conn, CASE, "de")
    store.save_edit(conn, CASE, "de", "Offener Entwurf")
    display = store.get_display(conn, CASE, "de")
    assert (display.status, display.text) == ("approved", "Zweite Freigabe")
    assert store.get_pending_edit(conn, CASE, "de").text == "Offener Entwurf"


def test_display_for_unknown_case_is_none(conn):
    assert store.get_display(conn, "9C_999/2025", "de") is None


# --- One edited version (what the UI uses) ---------------------------------------


def test_replace_edit_keeps_the_generated_text_and_one_human_version(conn):
    store.save_generated(conn, CASE, "de", GENERATED)
    store.replace_edit(conn, CASE, "de", "Erste Fassung")
    second = store.replace_edit(conn, CASE, "de", "Zweite Fassung")
    assert (second.status, second.model, second.version) == ("approved", "human", 2)
    history = store.get_history(conn, CASE, "de")
    assert [(s.status, s.text) for s in history] == [("generated", "Modell-Summary"), ("approved", "Zweite Fassung")]
    assert store.get_display(conn, CASE, "de").text == "Zweite Fassung"  # shown at once, no approval
    assert store.get_pending_edit(conn, CASE, "de") is None


def test_replace_edit_also_replaces_versions_saved_the_old_way(conn):
    store.save_generated(conn, CASE, "de", GENERATED)
    store.save_edit(conn, CASE, "de", "Alt, freigegeben")
    store.approve(conn, CASE, "de")
    store.save_edit(conn, CASE, "de", "Alt, offen")
    store.replace_edit(conn, CASE, "de", "Neu")
    assert [s.text for s in store.get_history(conn, CASE, "de")] == ["Modell-Summary", "Neu"]


def test_replace_edit_needs_a_generated_summary_and_leaves_the_other_language(conn):
    with pytest.raises(InvalidTransitionError):
        store.replace_edit(conn, CASE, "de", "ohne Vorlage")
    store.save_generated(conn, CASE, "de", GENERATED)
    store.save_generated(conn, CASE, "fr", GENERATED)
    store.replace_edit(conn, CASE, "de", "Nur Deutsch")
    assert store.get_display(conn, CASE, "fr").status == "generated"


def test_discard_edit_returns_to_the_generated_text(conn):
    store.save_generated(conn, CASE, "de", GENERATED)
    store.save_generated(conn, CASE, "fr", GENERATED)
    store.replace_edit(conn, CASE, "de", "Bearbeitet")
    store.replace_edit(conn, CASE, "fr", "Modifié")
    assert store.discard_edit(conn, CASE, "de") == 1
    assert store.get_display(conn, CASE, "de").text == "Modell-Summary"
    assert store.get_display(conn, CASE, "fr").text == "Modifié"
    assert store.discard_edit(conn, CASE) == 1  # both languages: only FR had an edit left
    assert store.discard_edit(conn, CASE) == 0
    assert [s.status for s in store.get_history(conn, CASE, "fr")] == ["generated"]
    with pytest.raises(ValueError):
        store.discard_edit(conn, CASE, "it")
    assert store.get_history(conn, "9C_999/2025", "de") == []


# --- Pipeline fields, both languages, lookup, search -----------------------------


def test_pipeline_fields_are_stored_and_title_kept_on_edit(conn):
    generated = GeneratedSummary(
        text="T", model="apertus-v1-5-8b", prompt_version="v1", coherence_ok=False,
        title="BGer-Urteil 8C_129/2025", issues=[{"severity": "error", "detail": "E. 9 fehlt"}],
        facts={"form": "fall"}, examples=["8C_177/2025"],
    )
    stored = store.save_generated(conn, CASE, "de", generated)
    reread = store.get_display(conn, CASE, "de")
    assert reread == stored
    assert reread.issues[0]["detail"] == "E. 9 fehlt"
    assert reread.facts == {"form": "fall"} and reread.examples == ["8C_177/2025"]
    edited = store.save_edit(conn, CASE, "de", "Neu")
    assert edited.title == "BGer-Urteil 8C_129/2025"
    assert edited.issues == [] and edited.facts == {}


def test_get_or_generate_languages_one_call_for_de_and_fr(conn):
    generate = Mock(return_value={"de": GENERATED, "fr": GENERATED})
    first = store.get_or_generate_languages(conn, CASE, generate)
    again = store.get_or_generate_languages(conn, CASE, generate)
    assert generate.call_count == 1
    assert set(first) == {"de", "fr"} and first == again


def test_get_or_generate_languages_keeps_existing_language(conn):
    store.save_generated(conn, CASE, "de", GENERATED)
    store.save_edit(conn, CASE, "de", "Jurist")
    store.approve(conn, CASE, "de")
    other = GeneratedSummary("Neu generiert", "apertus-v1-5-8b", "v2", True)
    result = store.get_or_generate_languages(conn, CASE, Mock(return_value={"de": other, "fr": other}))
    assert result["de"].text == "Jurist"  # approved text is never replaced
    assert result["fr"].text == "Neu generiert"


def test_normalized_case_number_resolves(conn):
    assert store.resolve_case_number(conn, "1D_2_2025") == JOINED_MAIN
    assert store.resolve_case_number(conn, "8C_129_2025") == CASE


def test_save_case_keeps_known_values(conn):
    store.save_case(conn, CASE, None, None, publication_date="2025-07-01")
    case = store.get_case(conn, CASE)
    assert (case.decision_date, case.url, case.publication_date) == (
        "2025-06-01", "https://search.bger.ch/8C_129", "2025-07-01"
    )


def test_search_matches_text_and_case_numbers(conn):
    store.save_generated(conn, CASE, "de", GeneratedSummary("Kausalzusammenhang Schulter", "m", "v1", True))
    store.save_generated(conn, JOINED_MAIN, "fr", GeneratedSummary("Recours rejeté", "m", "v1", True))
    assert store.search(conn, "schulter") == [CASE]
    assert store.search(conn, "1D_2/2025") == [JOINED_MAIN]
    assert store.search(conn, "schulter recours") == []
    assert set(store.search(conn, "")) == {CASE, JOINED_MAIN}
    assert store.list_case_ids(conn) == [JOINED_MAIN, CASE]


# --- Schema constraints (direct SQL bypassing the API) ----------------------------


@pytest.mark.parametrize(
    "status, model, prompt_version",
    [
        ("edited", "apertus-v1-5-70b", None),
        ("generated", "human", "v1"),
        ("approved", "human", "v1"),
    ],
)
def test_check_constraint_rejects_inconsistent_rows(conn, status, model, prompt_version):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO summaries (case_id, language, version, text, model, prompt_version,"
            " created_at, status) VALUES (?, 'de', 1, 't', ?, ?, 't', ?)",
            (CASE, model, prompt_version, status),
        )


def test_feedback_persistence_and_undo(conn):
    res1 = store.save_feedback(conn, "8C_129_2025", decision="high", level="high", adjustment=25.0, direction=1)
    assert res1["stored"] is True and res1["caseId"] == CASE
    assert store.get_latest_feedback(conn)["8C_129_2025"]["userLevel"] == "high"
    assert store.feedback_examples(conn) == {CASE: True}

    store.save_feedback(conn, CASE, decision=None, level="medium", adjustment=0.0, direction=-1)
    assert store.get_latest_feedback(conn)["8C_129_2025"]["userLevel"] is None
    assert store.get_latest_feedback(conn)["8C_129_2025"]["adjustment"] == 0.0
    assert store.feedback_examples(conn) == {}
