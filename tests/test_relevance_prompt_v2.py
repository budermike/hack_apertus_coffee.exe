"""Prompt v2 for the relevance scoring (#62/#67): opt-in, own cache stages and files, French reason."""

from __future__ import annotations

import json

import pytest

from hack_apertus_coffee_exe.present.judgements import to_ui_item
from hack_apertus_coffee_exe.triage import relevance as rel
from hack_apertus_coffee_exe.triage import relevance_period as period
from hack_apertus_coffee_exe.triage.config import TriageConfig, load_config
from hack_apertus_coffee_exe.triage import calibration
from hack_apertus_coffee_exe.triage.date_filter import DateRange
from tests.test_relevance import DEEP, RUN_CASES, TRIAGE, GatewayByCase, _answer, _client, _cli_relevance, _item, _records

FR = "Question matérielle de l'assurance-accidents. Le TF examine le lien de causalité (consid. 2.1)."
TRIAGE_V2 = dict(TRIAGE, c_begruendung_fr=FR)


def test_v1_is_the_default_and_unchanged():
    assert TriageConfig().relevance_prompt == "v1" and load_config().relevance_prompt == "v1"
    assert rel.PROMPTS["v1"]["triage"] is rel.TRIAGE_SYSTEM and rel.PROMPTS["v1"]["triage_schema"] is rel.TRIAGE_SCHEMA
    assert rel.prompt_suffix("v1") == "" and rel.prompt_suffix("v2") == "_v2"
    with pytest.raises(ValueError):
        rel.prompt_suffix("v3")


def test_v2_drops_the_rates_and_quotas_and_asks_for_french():
    v2 = rel.PROMPTS["v2"]
    assert "Erfahrungswerte:" in rel.TRIAGE_SYSTEM and "Erfahrungswerte:" not in v2["triage"]
    assert "Gewichtung:" in v2["triage"] and "c_begruendung_fr" in v2["triage"]
    assert "Bedenke die Quoten oben." not in v2["deep"] and "Sei streng." in v2["deep"]
    props = list(v2["triage_schema"]["properties"])
    assert props.index("c_begruendung") < props.index("c_begruendung_fr") < props.index("d_score")  # score last
    assert set(v2["triage_schema"]["required"]) == set(props)


def test_score_item_with_v2_uses_its_own_stages_and_prompt(tmp_path):
    client, gateway = _client([TRIAGE_V2, DEEP], tmp_path)
    record = rel.score_item(_item(), [], client, prompt="v2")
    assert record["error"] is None and record["triage"]["c_begruendung_fr"] == FR
    first, second = gateway.requests
    assert first["response_format"]["json_schema"]["name"] == "triage_v2"
    assert second["response_format"]["json_schema"]["name"] == "deep_v2"
    assert "Erfahrungswerte:" not in first["messages"][0]["content"]
    assert (tmp_path / "relevance" / "triage_v2" / "2026-04-02_8C_1-2026.json").exists()
    assert not (tmp_path / "relevance" / "triage").exists()  # v1 cache untouched


def test_scores_path_and_results_carry_the_french_reason(tmp_path):
    q1 = DateRange.from_period("2026-Q1")
    assert period.scores_path(tmp_path, q1).name == "relevance_scores_q1_2026.json"
    assert period.scores_path(tmp_path, q1, "v2").name == "relevance_scores_q1_2026_v2.json"
    client, _ = _client([TRIAGE_V2, DEEP])
    record = rel.score_item(_item(), [], client, prompt="v2")
    fitted = calibration.calibrate(*_records())
    [result] = period.to_results([record], fitted)
    assert result.metadata["relevance"]["reason_fr"] == FR
    item = to_ui_item(result.to_dict())
    assert item["explanation"]["fr"] == FR and item["explanation"]["de"] == TRIAGE["c_begruendung"]  # rule shown apart


def test_v1_results_have_no_french_reason_and_ui_falls_back():
    client, _ = _client([TRIAGE, DEEP])
    record = rel.score_item(_item(), [], client)
    assert "c_begruendung_fr" not in record["triage"]
    row = {"case_id": "8C_1/2026", "relevance_reason": "Grund.", "metadata": {"relevance": {"p": 0.5}}}
    assert to_ui_item(row)["explanation"] == {"de": "Grund.", "fr": "Grund."}


class GatewayV2(GatewayByCase):
    def _create(self, **kwargs):
        stage = kwargs["response_format"]["json_schema"]["name"]
        assert stage in ("triage_v2", "deep_v2"), stage
        case_id = kwargs["messages"][1]["content"].split("Metadaten des zu beurteilenden Urteils: ")[1].split(" ")[0]
        self.asked.append((stage, case_id))
        score = next(s for c, _, _, s in RUN_CASES if c == case_id)
        return _answer(json.dumps(dict(TRIAGE_V2, d_score=score) if stage == "triage_v2" else DEEP))


def test_cli_prompt_v2_writes_suffixed_files_next_to_v1(tmp_path, monkeypatch):
    assert _cli_relevance(tmp_path, monkeypatch, GatewayV2(), "--list-factor", "1.0", "--prompt", "v2") == 0
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == [
        "ranking_q1_2026_v2.csv", "recall_q1_2026_v2.txt", "triage.json",
    ]
    assert sorted(p.name for p in (tmp_path / "tables").iterdir()) == [
        "relevance_calibration_q1_2026_v2.json", "relevance_scores_q1_2026_v2.json", "relevance_scores_q4_2025_v2.json",
    ]
    rows = json.loads((tmp_path / "out" / "triage.json").read_text(encoding="utf-8"))
    assert all(r["metadata"]["relevance"]["reason_fr"] == FR for r in rows)
