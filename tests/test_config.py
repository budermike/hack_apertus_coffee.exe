"""Central settings (#48): configs/paths.json and environment variables. No network."""

import json

import pytest

from hack_apertus_coffee_exe import config
from hack_apertus_coffee_exe.config import env, load_paths


def test_paths_from_configs_folder():
    paths = load_paths()
    assert paths.raw_dir == "data/raw"
    assert paths.vectordb_dir == "data/processed/vectordb"
    assert paths.judgements_dir == "judgements"
    assert (paths.default_period_from, paths.default_period_to) == ("2026-04-01", "2026-06-30")
    assert (config.config_dir() / "paths.json").is_file()


def test_every_key_is_required(tmp_path):
    incomplete = tmp_path / "paths.json"
    incomplete.write_text(json.dumps({"raw_dir": "x"}))
    with pytest.raises(KeyError):
        load_paths(incomplete)


def test_modules_take_their_defaults_from_the_config():
    from hack_apertus_coffee_exe.crawl import vectordb
    from hack_apertus_coffee_exe.summary import run
    from hack_apertus_coffee_exe.triage.config import AMPEL_GREEN

    paths = load_paths()
    assert (vectordb.DEFAULT_DB_DIR, vectordb.DEFAULT_RAW_DIR) == (paths.vectordb_dir, paths.raw_dir)
    assert (vectordb.DEFAULT_JUDGEMENTS_DIR, vectordb.DEFAULT_REPORT_PATH) == (paths.judgements_dir, paths.vectordb_report)
    assert run.GREEN == AMPEL_GREEN


def test_env_takes_first_non_empty_name(monkeypatch):
    monkeypatch.setenv("APERTUS_MODEL", "")
    monkeypatch.setenv("LLM_NAME", "apertus-v1-5-70b")
    assert env("APERTUS_MODEL", "LLM_NAME") == "apertus-v1-5-70b"
    monkeypatch.delenv("LLM_NAME")
    assert env("APERTUS_MODEL", "LLM_NAME", default="apertus-v1-5-8b") == "apertus-v1-5-8b"
