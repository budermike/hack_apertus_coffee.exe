from hack_apertus_coffee_exe.eval.run_relevance import RoomyRelevanceClient
from hack_apertus_coffee_exe.triage import relevance
from hack_apertus_coffee_exe.triage.llm import GatewayConfig


def test_roomy_client_multiplies_token_budget(monkeypatch):
    seen = []
    monkeypatch.setattr(relevance.ApertusRelevanceClient, "call",
                        lambda self, stage, key, system, user, schema, max_tokens: seen.append(max_tokens) or {})
    client = RoomyRelevanceClient(config=GatewayConfig(model="m", base_url="http://x", api_key="k"), token_factor=4)
    client.call("deep", "k", "s", "u", {}, 400)
    assert seen == [1600]
