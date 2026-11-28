"""Gateway smoke test (src/keera_example.py): which environment variables it reads.

No network and no .env: importing the module must not call the gateway, and
`gateway_settings` only looks at the environment that the test sets.
"""

import pytest

import keera_example

ALL_NAMES = [
    "LLM_API_KEY",
    "APERTUS_API_KEY",
    "KEERA_API_KEY",
    "LLM_BASE_URL",
    "APERTUS_BASE_URL",
    "LLM_NAME",
    "APERTUS_MODEL",
]


@pytest.fixture(autouse=True)
def empty_environment(monkeypatch):
    for name in ALL_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_reads_the_names_required_by_the_template(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "key-from-llm")
    monkeypatch.setenv("LLM_BASE_URL", "https://gateway.example/api/v1")
    monkeypatch.setenv("LLM_NAME", "apertus-v1-5-70b")

    assert keera_example.gateway_settings() == {
        "api_key": "key-from-llm",
        "base_url": "https://gateway.example/api/v1",
        "model": "apertus-v1-5-70b",
    }


def test_older_apertus_names_still_work(monkeypatch):
    monkeypatch.setenv("APERTUS_API_KEY", "key-from-apertus")
    monkeypatch.setenv("APERTUS_BASE_URL", "https://old.example/api/v1")
    monkeypatch.setenv("APERTUS_MODEL", "apertus-v1-5-70b")

    assert keera_example.gateway_settings() == {
        "api_key": "key-from-apertus",
        "base_url": "https://old.example/api/v1",
        "model": "apertus-v1-5-70b",
    }


def test_template_names_win_over_the_aliases(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "key-from-llm")
    monkeypatch.setenv("APERTUS_API_KEY", "key-from-apertus")
    monkeypatch.setenv("KEERA_API_KEY", "key-from-keera")
    monkeypatch.setenv("LLM_BASE_URL", "https://new.example/api/v1")
    monkeypatch.setenv("APERTUS_BASE_URL", "https://old.example/api/v1")
    monkeypatch.setenv("LLM_NAME", "apertus-v1-5-70b")
    monkeypatch.setenv("APERTUS_MODEL", "apertus-v1-5-8b")

    settings = keera_example.gateway_settings()

    assert settings["api_key"] == "key-from-llm"
    assert settings["base_url"] == "https://new.example/api/v1"
    assert settings["model"] == "apertus-v1-5-70b"


def test_endpoint_and_model_have_defaults(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "key")

    settings = keera_example.gateway_settings()

    assert settings["base_url"] == keera_example.DEFAULT_BASE_URL
    assert settings["model"] == keera_example.DEFAULT_MODEL


def test_empty_value_counts_as_not_set(monkeypatch):
    # .env.example ships `LLM_API_KEY=` with no value.
    monkeypatch.setenv("LLM_API_KEY", "  ")
    monkeypatch.setenv("APERTUS_API_KEY", "key-from-apertus")

    assert keera_example.gateway_settings()["api_key"] == "key-from-apertus"


def test_missing_key_stops_and_names_the_variable():
    with pytest.raises(SystemExit) as stopped:
        keera_example.gateway_settings()

    assert "LLM_API_KEY" in str(stopped.value)
