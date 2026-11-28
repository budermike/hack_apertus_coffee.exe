"""Thinking mode for the relevance scoring (#76). Offline, the gateway is faked."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from hack_apertus_coffee_exe.triage import calibration as cal
from hack_apertus_coffee_exe.triage import relevance as rel
from hack_apertus_coffee_exe.triage import relevance_period as period
from hack_apertus_coffee_exe.triage.config import load_config
from hack_apertus_coffee_exe.triage.llm import GatewayConfig

TRIAGE = {
    "a_rechtsfrage": "Ist der Kausalzusammenhang erstellt?",
    "b_kriterien": [{"a_id": "K1", "b_stelle": "E2.1", "c_zitat": "Der Unfallversicherer haftet nach Art. 6 Abs. 1 UVG"}],
    "c_begruendung": "Materielle Frage der Unfallversicherung. Das Gericht prüft den Kausalzusammenhang.",
    "d_score": 80,
}
SCHEMA = rel.TRIAGE_SCHEMA


class BadRequestError(Exception):
    """Same class name as the openai package's HTTP 400 error."""


class Gateway:
    """Fake OpenAI client: answers with queued messages, records the requests."""

    def __init__(self, messages, reject_format=False):
        self.messages, self.requests, self.reject_format = list(messages), [], reject_format
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if self.reject_format and "response_format" in kwargs:
            raise BadRequestError("response_format is not supported for this model")
        return SimpleNamespace(choices=[SimpleNamespace(message=self.messages.pop(0), finish_reason="stop")])


def _client(model, messages, tmp_path=None, reject_format=False):
    gateway = Gateway(messages, reject_format)
    config = GatewayConfig(api_key="test", base_url="http://gateway.invalid", model=model)
    return rel.ApertusRelevanceClient(config=config, client=gateway, cache_dir=tmp_path, thinking_extra_tokens=4000), gateway


def test_split_thinking_from_tags_or_separate_field():
    msg = SimpleNamespace(content="<think>K1 erfüllt, UV-Frage.</think>\n```json\n" + json.dumps(TRIAGE) + "\n```")
    thinking, answer = rel.split_thinking(msg)
    assert thinking == "K1 erfüllt, UV-Frage." and json.loads(answer) == TRIAGE
    msg = SimpleNamespace(content=json.dumps(TRIAGE), reasoning_content="Erst die Belege prüfen.")
    assert rel.split_thinking(msg) == ("Erst die Belege prüfen.", json.dumps(TRIAGE))
    assert rel.is_thinking_model("apertus-v1-5-70b-thinking") and not rel.is_thinking_model("apertus-v1-5-70b")


def test_normal_model_is_unchanged():
    client, gateway = _client("apertus-v1-5-70b", [SimpleNamespace(content=json.dumps(TRIAGE))])
    answer = client.call("triage", "k", "system", "user", SCHEMA, 1600)
    assert answer == TRIAGE and "thinking" not in answer
    request = gateway.requests[0]
    assert request["max_tokens"] == 1600 and request["response_format"]["json_schema"]["strict"]


def test_thinking_model_gets_room_and_keeps_its_reasoning(tmp_path):
    content = "<think>Die Suva ist Partei, K5 und K1 sprechen dafür.</think>" + json.dumps(TRIAGE)
    client, gateway = _client("apertus-v1-5-70b-thinking", [SimpleNamespace(content=content)], tmp_path)
    answer = client.call("triage", "k", "system", "user", SCHEMA, 1600)
    assert answer["d_score"] == 80 and answer["thinking"].startswith("Die Suva ist Partei")
    assert gateway.requests[0]["max_tokens"] == 1600 + 4000
    cached = json.loads((tmp_path / "relevance" / "triage" / "k.json").read_text(encoding="utf-8"))
    assert cached["answer"]["thinking"] == answer["thinking"]  # second run needs no call and keeps the reasoning
    again, _ = _client("apertus-v1-5-70b-thinking", [], tmp_path)
    assert again.call("triage", "k", "system", "user", SCHEMA, 1600) == answer and again.calls == 0


def test_refused_response_format_is_retried_without_it():
    client, gateway = _client(
        "apertus-v1-5-70b-thinking", [SimpleNamespace(content="<think>…</think>" + json.dumps(TRIAGE))], reject_format=True
    )
    assert client.call("triage", "k", "system", "user", SCHEMA, 1600)["d_score"] == 80
    assert "response_format" in gateway.requests[0] and "response_format" not in gateway.requests[1]
    assert client.calls == 2 and client._send_format is False  # later calls skip the format right away


def test_normal_model_does_not_hide_a_refused_format():
    client, _ = _client("apertus-v1-5-70b", [], reject_format=True)
    with pytest.raises(BadRequestError):
        client.call("triage", "k", "system", "user", SCHEMA, 1600)


def test_model_comes_from_the_config_and_reasoning_reaches_the_results():
    assert rel.DEFAULT_MODEL == load_config().relevance_model
    rows = [("U1", 90, 1), ("U2", 70, 0), ("U3", 20, 0), ("U4", 85, 1), ("U5", 10, 0)]
    records = [{"case_id": c, "case_ids": [c], "area": "UV", "decision_date": "2026-01-15", "language": "de", "subject": "UV",
                "triage": {"d_score": t, "c_begruendung": "Grund.", "a_rechtsfrage": "?", "b_kriterien": [], "thinking": f"Überlegung {c}"},
                "deep": None, "precedents": [], "error": None, "model": "apertus-v1-5-70b-thinking"} for c, t, _ in rows]
    fitted = cal.calibrate(records, {c for c, _, y in rows if y})
    by = {r.case_id: r for r in period.to_results(records, fitted)}
    assert by["U1"].metadata["relevance"]["thinking"] == "Überlegung U1"
