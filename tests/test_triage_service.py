"""Triage contract (#48): signatures, the score_period wrapper and the open stubs. No network."""

import inspect
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from hack_apertus_coffee_exe import triage
from hack_apertus_coffee_exe.triage import service


def _params(fn):
    return {name: (p.kind, p.default) for name, p in inspect.signature(fn).parameters.items()}


def test_contract_signatures():
    kw, pos, empty = inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty
    assert _params(triage.score_period) == {"date_from": (pos, empty), "date_to": (pos, empty), "model": (kw, None)}
    assert _params(triage.get_cases) == {
        "conn": (pos, empty), "date_from": (pos, empty), "date_to": (pos, empty), "ampel": (kw, None),
    }
    assert _params(triage.apply_feedback) == {
        "conn": (pos, empty), "case_id": (pos, empty), "ampel": (pos, empty),
        "comment": (kw, None), "user": (kw, empty),
    }


def test_open_stubs_say_what_they_wait_for():
    with pytest.raises(NotImplementedError, match="#50"):
        triage.get_cases(None, "2026-04-01", "2026-06-30")
    with pytest.raises(NotImplementedError, match="#64"):
        triage.apply_feedback(None, "8C_7/2025", "gruen", user="test")


def test_period_name_only_for_whole_periods():
    assert service.period_name("2026-04-01", "2026-06-30") == "2026-Q2"
    assert service.period_name("2026-06-01", "2026-06-30") == "2026-06"
    assert service.period_name("2026-01-01", "2026-12-31") == "2026"
    with pytest.raises(ValueError):
        service.period_name("2026-04-03", "2026-06-30")


def test_score_period_crawls_first_then_runs_the_relevance_scoring(monkeypatch):
    from hack_apertus_coffee_exe.crawl import ensure
    from hack_apertus_coffee_exe.triage import relevance, relevance_period

    calls = []

    @dataclass
    class Status:
        new_judgements: int = 3

    monkeypatch.setattr(ensure, "ensure_period", lambda a, b: calls.append(("ensure", a, b)) or Status())

    class FakeClient:
        calls = 7
        model = "apertus-test"

        def __init__(self, config, cache_dir):
            calls.append(("client", config.model))

    monkeypatch.setattr(relevance, "ApertusRelevanceClient", FakeClient)

    def fake_run(name, **kwargs):
        calls.append(("run", name, kwargs["raw_dir"], kwargs["judgements_dir"]))
        result = SimpleNamespace(to_dict=lambda: {"case_id": "8C_1/2026", "ampel": "gruen"})
        return {"records": [1, 2], "calibration": {"t": 0.04}, "results": [result]}

    monkeypatch.setattr(relevance_period, "run", fake_run)

    out = triage.score_period("2026-04-01", "2026-06-30", model="apertus-test")
    assert [c[0] for c in calls] == ["ensure", "client", "run"]
    assert calls[2] == ("run", "2026-Q2", "data/raw", "judgements")
    assert out == {
        "period": "2026-Q2", "ensure": {"new_judgements": 3}, "candidates": 2,
        "calibration": {"t": 0.04}, "results": [{"case_id": "8C_1/2026", "ampel": "gruen"}], "apertus_calls": 7,
    }
