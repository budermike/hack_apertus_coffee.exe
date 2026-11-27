"""SQLite summary store (issue #26)."""

from hack_apertus_coffee_exe.summary.store import SummaryStore


def test_versions_are_kept_and_latest_wins(tmp_path):
    db = tmp_path / "s.sqlite"
    store = SummaryStore(db)
    first = store.add("8C_1/2026", "de", "BGer-Urteil 8C_1/2026", "Fassung 1", model="m", coherence_ok=True)
    store.add("8C_1/2026", "fr", "Arrêt du TF 8C_1/2026", "Version 1", model="m", coherence_ok=True)
    assert store.has("8C_1/2026")

    edited = store.edit(first, "Fassung 2 (Jurist)")
    store.approve(edited)
    store.close()

    reopened = SummaryStore(db)  # restart: everything still there, no LLM needed
    latest = reopened.latest("8C_1/2026", "de")
    assert latest.text == "Fassung 2 (Jurist)"
    assert latest.status == "approved"
    assert latest.parent_id == first
    assert [s.text for s in reopened.history("8C_1/2026", "de")] == ["Fassung 1", "Fassung 2 (Jurist)"]
    assert reopened.case_ids() == ["8C_1/2026"]


def test_missing_case():
    store = SummaryStore(":memory:")
    assert store.latest("x", "de") is None
    assert not store.has("x")
