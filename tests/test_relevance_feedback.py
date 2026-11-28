"""Lawyer feedback as precedents (#64, on top of #94): own cache, opt-in for the CLI run."""

from __future__ import annotations

import json

from hack_apertus_coffee_exe.triage import relevance as rel
from hack_apertus_coffee_exe.triage import relevance_period as period
from hack_apertus_coffee_exe.triage.config import TriageConfig, load_config
from tests.test_relevance import DEEP, TRIAGE, GatewayByCase, _client, _item, _run

KEYS = frozenset({"area:UV", "UVG:6"})


def _prec(case_id, selected, feedback=False):
    return rel.Precedent(case_id, "2025-11-03", "UV", "Unfallversicherung", "abgewiesen", False, KEYS, selected, feedback=feedback)


def _stages(gateway):
    return [r["response_format"]["json_schema"]["name"] for r in gateway.requests]


def test_off_by_default():
    assert not TriageConfig().feedback_learn and not load_config().feedback_learn


def test_without_feedback_precedents_the_normal_cache_is_used(tmp_path):
    client, gateway = _client([TRIAGE, DEEP], tmp_path)
    rel.score_item(_item(), [_prec("8C_9/2025", True)], client)
    assert _stages(gateway) == ["triage", "deep"]
    assert (tmp_path / "relevance" / "triage" / "2026-04-02_8C_1-2026.json").exists()


def test_runs_with_and_without_feedback_do_not_overwrite_each_others_cache(tmp_path):
    plain = [_prec("8C_9/2025", False)]
    lawyer = [_prec("8C_9/2025", True, feedback=True)]  # a lawyer called it relevant
    client, _ = _client([TRIAGE, DEEP], tmp_path)
    rel.score_item(_item(), plain, client)  # normal run, cached
    client, gateway = _client([dict(TRIAGE, d_score=95), DEEP], tmp_path)
    record = rel.score_item(_item(), lawyer, client)
    assert _stages(gateway) == ["triage_fb", "deep_fb"] and record["triage"]["d_score"] == 95  # asked, not the cached 80
    client, gateway = _client([], tmp_path)  # nothing queued: any call would fail
    assert rel.score_item(_item(), plain, client)["triage"]["d_score"] == 80 and gateway.requests == []
    assert rel.score_item(_item(), lawyer, client)["triage"]["d_score"] == 95 and gateway.requests == []


def test_a_changed_decision_is_asked_again(tmp_path):
    client, _ = _client([TRIAGE, DEEP], tmp_path)
    rel.score_item(_item(), [_prec("8C_9/2025", True, feedback=True)], client)
    client, gateway = _client([dict(TRIAGE, d_score=10)], tmp_path)
    record = rel.score_item(_item(), [_prec("8C_9/2025", False, feedback=True)], client)  # now not relevant
    assert _stages(gateway) == ["triage_fb"] and record["triage"]["d_score"] == 10  # the request changed


def test_pool_marks_only_the_lawyer_decisions(tmp_path):
    from tests.test_relevance import _run_folders

    raw, _ = _run_folders(tmp_path)
    pool = period.precedent_pool(raw, ["2025-Q4"], {"8C_1/2025"}, {}, feedback_overrides={"8C_2/2025": True})
    marks = {p.case_id: (p.selected, p.feedback) for p in pool}
    assert marks["8C_1/2025"] == (True, False) and marks["8C_2/2025"] == (True, True) and marks["9C_3/2025"] == (False, False)


class Recorder(GatewayByCase):
    def _create(self, **kwargs):
        name = kwargs["response_format"]["json_schema"]["name"]
        self.stages = getattr(self, "stages", set()) | {name}
        self.prompts = getattr(self, "prompts", []) + [kwargs["messages"][1]["content"]]
        kwargs["response_format"]["json_schema"]["name"] = name.removesuffix("_fb")
        return super()._create(**kwargs)


def _line(gateway, judged, precedent):
    prompt = next(p for p in gateway.prompts if p.split("Metadaten des zu beurteilenden Urteils: ")[1].startswith(judged))
    return next(line for line in prompt.splitlines() if line.startswith(f"- {precedent} "))


def test_cli_run_ignores_the_database_unless_switched_on(tmp_path, monkeypatch):
    monkeypatch.setattr(period, "_load_feedback_overrides", lambda: {"8C_2/2025": True})
    gateway = Recorder()
    _run(tmp_path, gateway)
    assert gateway.stages == {"triage", "deep"}
    assert _line(gateway, "8C_4/2026", "8C_2/2025").endswith("von Suva NICHT gewählt.")
    assert sorted(p.name for p in (tmp_path / "tables").iterdir()) == ["relevance_scores_q1_2026.json", "relevance_scores_q4_2025.json"]


def test_switched_on_the_decision_is_shown_and_written_apart(tmp_path, monkeypatch):
    monkeypatch.setattr(period, "_load_feedback_overrides", lambda: {"8C_2/2025": True})
    gateway = Recorder()
    out = _run(tmp_path, gateway, config=TriageConfig(feedback_learn=True))
    assert "triage_fb" in gateway.stages
    assert _line(gateway, "8C_4/2026", "8C_2/2025").endswith("VON SUVA GEWÄHLT.")
    assert sorted(p.name for p in (tmp_path / "tables").iterdir()) == [
        "relevance_scores_q1_2026_fb.json", "relevance_scores_q4_2025_fb.json",
    ]
    assert out["calibration"]["n_pos"] == 1  # the calibration keeps Suva's own label only
    stored = json.loads((tmp_path / "tables" / "relevance_scores_q1_2026_fb.json").read_text(encoding="utf-8"))
    assert all(set(p) == {"case_id", "similarity"} for r in stored for p in r["precedents"])  # decisions stay private


def test_explicit_feedback_argument_replaces_the_database(tmp_path, monkeypatch):
    monkeypatch.setattr(period, "_load_feedback_overrides", lambda: (_ for _ in ()).throw(AssertionError("DB read")))
    gateway = Recorder()
    _run(tmp_path, gateway, config=TriageConfig(feedback_learn=True), feedback={"8C_2/2025": True})
    assert _line(gateway, "8C_4/2026", "8C_2/2025").endswith("VON SUVA GEWÄHLT.")
