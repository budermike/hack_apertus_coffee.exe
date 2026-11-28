"""Summary contract (#48): summarize() wraps run.summarize_case. No network, LLM never called."""

import inspect
import shutil
from pathlib import Path

import pytest

from hack_apertus_coffee_exe import summary
from hack_apertus_coffee_exe.summary import run, service, store

MOCK = Path("src/hack_apertus_coffee_exe/triage/mock_crawl")


def test_contract_signature():
    assert list(inspect.signature(summary.summarize).parameters) == ["conn", "case_id"]


def test_find_judgement_in_any_spelling(tmp_path):
    raw = tmp_path / "raw" / "2026-10"
    raw.mkdir(parents=True)
    shutil.copy(MOCK / "8C_901_2026.json", raw / "2026-10-14_8C_901_2026.json")
    for spelling in ("8C_901/2026", "8C_901_2026"):
        assert service.find_judgement(spelling, raw_dir=tmp_path / "raw").case_id == "8C_901/2026"
    assert service.find_judgement("9C_1/2026", raw_dir=tmp_path / "raw") is None


def test_summarize_finds_the_judgement_and_uses_summarize_case(monkeypatch, tmp_path):
    raw = tmp_path / "raw" / "2026-10"
    raw.mkdir(parents=True)
    shutil.copy(MOCK / "8C_901_2026.json", raw / "2026-10-14_8C_901_2026.json")
    monkeypatch.setattr(service, "load_paths", lambda: type("P", (), {"raw_dir": str(tmp_path / "raw")})())
    seen = {}

    def fake_summarize_case(judgement, conn, get_summarizer):
        seen["case_id"] = judgement.case_id
        return {"de": "DE", "fr": "FR"}

    monkeypatch.setattr(run, "summarize_case", fake_summarize_case)
    conn = store.connect(tmp_path / "s.db")
    assert summary.summarize(conn, "8C_901_2026") == {"de": "DE", "fr": "FR"}
    assert seen["case_id"] == "8C_901/2026"
    with pytest.raises(LookupError):
        summary.summarize(conn, "9C_1/2026")
    conn.close()
