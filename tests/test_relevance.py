"""Offline tests for the two-pass relevance scoring: parsing, prompts' inputs, Apertus calls (mocked), calibration, eval."""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from hack_apertus_coffee_exe.eval import relevance_eval as rev
from hack_apertus_coffee_exe.triage import calibration as cal
from hack_apertus_coffee_exe.triage import relevance as rel
from hack_apertus_coffee_exe.triage import relevance_period as period
from hack_apertus_coffee_exe.triage.config import TriageConfig, load_config
from hack_apertus_coffee_exe.triage.llm import GatewayConfig
from hack_apertus_coffee_exe.triage.pipeline import CrawlItem

TEXT = """Bundesgericht
8C_1/2026
Urteil vom 2. April 2026
IV. öffentlich-rechtliche Abteilung
Gegenstand
Unfallversicherung (Kausalzusammenhang),
Sachverhalt:
A.
A.a. Der Versicherte stürzte am 3. Mai 2024 von einer Leiter und verletzte sich an der Schulter.
B.
Das kantonale Gericht wies die Beschwerde ab.
Erwägungen:
1.
Streitig ist der natürliche Kausalzusammenhang zwischen dem Unfall und den Schulterbeschwerden.
2.
2.1. Der Unfallversicherer haftet nach Art. 6 Abs. 1 UVG für die Folgen des Unfalls, solange sie mit überwiegender Wahrscheinlichkeit darauf zurückgehen.
2.2. Das Gutachten ist beweiskräftig (BGE 134 V 231).
Demnach erkennt das Bundesgericht:
1.
Die Beschwerde wird abgewiesen.
2.
Die Gerichtskosten werden dem Beschwerdeführer auferlegt.
Luzern, 2. April 2026
Im Namen der IV. öffentlich-rechtlichen Abteilung"""


def _item(case_id="8C_1/2026", subject="Unfallversicherung (Kausalzusammenhang)", date="2026-04-02", text=TEXT) -> CrawlItem:
    return CrawlItem(case_id=case_id, text=text, metadata={"case_ids": [case_id], "decision_date": date, "subject": subject}, language="de")


def _answer(content: str, finish: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)])


class FakeGateway:
    """Stands in for the OpenAI client: returns queued answers and records the requests."""

    def __init__(self, answers):
        self.answers, self.requests = list(answers), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return _answer(*self.answers.pop(0)) if isinstance(self.answers[0], tuple) else _answer(self.answers.pop(0))


TRIAGE = {
    "a_rechtsfrage": "Ist der Kausalzusammenhang erstellt?",
    "b_kriterien": [{"a_id": "K1", "b_stelle": "E2.1", "c_zitat": "Der Unfallversicherer haftet nach Art. 6 Abs. 1 UVG für die Folgen des Unfalls"}],
    "c_begruendung": "Materielle Frage der Unfallversicherung. Das Gericht prüft den Kausalzusammenhang.",
    "d_score": 80,
}
DEEP = {"a_grundsatz": 2, "b_nutzen_uv": 3, "c_korrektur": False, "d_merksatz": "Der Versicherer haftet, solange die Folgen überwiegend wahrscheinlich sind.", "e_score": 60}


def _client(answers, tmp_path=None) -> tuple[rel.ApertusRelevanceClient, FakeGateway]:
    gateway = FakeGateway([json.dumps(a) if isinstance(a, dict) else a for a in answers])
    config = GatewayConfig(api_key="test", base_url="http://gateway.invalid", model="apertus-v1-5-70b")
    return rel.ApertusRelevanceClient(config=config, client=gateway, cache_dir=tmp_path), gateway


# ---------------------------------------------------------------- structure


def test_candidates_are_the_two_social_insurance_divisions():
    assert rel.is_candidate(_item("8C_1/2026")) and rel.is_candidate(_item("9F_3/2026"))
    assert not rel.is_candidate(_item("5A_1/2026"))
    joined = CrawlItem("1C_5/2026", "x", {"case_ids": ["1C_5/2026", "9C_6/2026"]})
    assert rel.is_candidate(joined)


def test_area_comes_from_the_subject_in_three_languages():
    assert rel.area_of("Unfallversicherung (Kausalzusammenhang)") == "UV"
    assert rel.area_of("Assurance-invalidité (rente d'invalidité)") == "IV"
    assert rel.area_of("Assicurazione militare") == "MV"
    assert rel.area_of("Arbeitslosenversicherung (Einstellung; Unfall)") == "ALV"  # the head of the subject wins
    assert rel.area_of("Öffentliches Personalrecht") == "andere" and rel.area_of(None) == "andere"


def test_segment_gives_citable_marks_and_the_outcome():
    doc = rel.segment(TEXT)
    assert [s["id"] for s in doc["segs"]] == ["R", "S.A.a", "S.B", "E1", "E2.1", "E2.2", "D1", "D2"]
    assert doc["title"] == "Urteil vom 2. April 2026" and doc["court"].startswith("IV.")
    assert doc["outcome"] == "abgewiesen" and doc["single_judge"] is False
    assert "Luzern" not in rel.labelled_text(doc["segs"])  # everything after the operative part is dropped
    assert rel.labelled_text(doc["segs"]).splitlines()[4].startswith("[E2.1] Der Unfallversicherer")


def test_norms_are_language_independent():
    assert rel.norms("Art. 6 Abs. 1 UVG und art. 4 LPGA; ATF 134 V 231") == {"UVG:6", "ATSG:4", "BGE:134:V:231"}


def test_quote_status_tells_verbatim_from_invented():
    source = rel._squash(TEXT)
    assert rel.quote_status("haftet nach Art. 6 Abs. 1 UVG für die Folgen", source) == "exakt"
    assert rel.quote_status("Das Bundesgericht ändert seine Rechtsprechung zum Schleudertrauma grundlegend", source) == "fehlt"
    assert rel.quote_status("zu kurz", source) == "fehlt"


def test_exemplars_only_come_from_earlier_quarters():
    def prec(case, date, selected):
        return rel.Precedent(case, date, "UV", "Unfallversicherung", "abgewiesen", False, frozenset({"area:UV", "UVG:6"}), selected)

    pool = [prec("A", "2025-11-03", True), prec("B", "2025-12-01", False), prec("C", "2026-01-10", True), prec("D", "2026-02-10", False)]
    got = rel.exemplars("2026-02-20", frozenset({"area:UV", "UVG:6"}), pool)
    assert [p.case_id for _, p in got] == ["A", "B"]  # C and D are from the same quarter: no leakage
    text = rel.exemplar_text(got)
    assert "A (UV;" in text and "VON SUVA GEWÄHLT" in text and "von Suva NICHT gewählt" in text
    assert rel.exemplar_text([]) == "(keine früheren Fälle verfügbar)"


# ---------------------------------------------------------------- Apertus calls


def test_score_item_runs_both_passes_and_checks_the_quote():
    client, gateway = _client([TRIAGE, DEEP])
    record = rel.score_item(_item(), [], client)
    assert record["area"] == "UV" and record["error"] is None and client.calls == 2
    assert record["triage"]["d_score"] == 80 and record["triage"]["b_kriterien"][0]["beleg_status"] == "exakt"
    assert record["deep"]["e_score"] == 60
    first, second = gateway.requests
    assert first["model"] == "apertus-v1-5-70b" and first["temperature"] == 0.0 and first["response_format"]["json_schema"]["strict"]
    assert "[E2.1] Der Unfallversicherer" in first["messages"][1]["content"]  # whole text with marks, no excerpt
    assert "K3 Urteil aus der Invalidenversicherung" in first["messages"][0]["content"]
    assert "b_nutzen_uv" in second["messages"][0]["content"]


def test_low_triage_score_skips_the_second_pass_and_other_areas_cost_no_call():
    client, _ = _client([dict(TRIAGE, d_score=20)])
    record = rel.score_item(_item(), [], client)
    assert record["deep"] is None and client.calls == 1
    client, _ = _client([])
    record = rel.score_item(_item(subject="Krankenversicherung"), [], client)
    assert record["area"] == "KV" and record["triage"] is None and client.calls == 0


def test_broken_answer_is_retried_and_a_final_failure_is_recorded_not_raised():
    client, gateway = _client(["{not json", dict(TRIAGE, d_score=20)])
    assert rel.score_item(_item(), [], client)["triage"]["d_score"] == 20
    assert gateway.requests[1]["temperature"] == 0.4 and gateway.requests[1]["max_tokens"] == 2 * gateway.requests[0]["max_tokens"]
    client, _ = _client(["{not json", json.dumps({"d_score": 5})])  # second answer violates the schema
    record = rel.score_item(_item(), [], client)
    assert record["triage"] is None and "RelevanceError" in record["error"]


def test_answers_are_cached_per_judgement(tmp_path):
    client, _ = _client([TRIAGE, DEEP], tmp_path)
    first = rel.score_item(_item(), [], client)
    again, gateway = _client([], tmp_path)  # no answers queued: a call would fail
    assert rel.score_item(_item(), [], again) == first and again.calls == 0 and gateway.requests == []
    assert (tmp_path / "relevance" / "triage" / "2026-04-02_8C_1-2026.json").exists()


# ---------------------------------------------------------------- calibration


def _records():
    """Scored judgements: high scores selected, low ones not; area UV and IV."""
    rows = [("U1", "UV", 90, 80, 1), ("U2", "UV", 85, 60, 1), ("U3", "UV", 80, 30, 0), ("U4", "UV", 40, 0, 0), ("U5", "UV", 10, 0, 0),
            ("I1", "IV", 85, 75, 1), ("I2", "IV", 80, 20, 0), ("I3", "IV", 60, 10, 0), ("I4", "IV", 20, 0, 0), ("I5", "IV", 5, 0, 0)]
    records = [{"case_id": c, "case_ids": [c], "area": a, "decision_date": "2026-01-15", "language": "de", "subject": a,
                "triage": {"d_score": t, "c_begruendung": "Grund.", "a_rechtsfrage": "Frage?", "b_kriterien": [{"a_id": "K1"}]},
                "deep": {"e_score": d, "d_merksatz": "Regel."} if t >= 50 else None, "precedents": [], "error": None, "model": "m"}
               for c, a, t, d, _ in rows]
    return records, {c for c, *_, y in rows if y}


def test_calibration_orders_by_the_apertus_scores_and_sets_an_f2_threshold():
    records, selected = _records()
    fitted = cal.calibrate(records, selected)
    assert fitted["n"] == 10 and fitted["n_pos"] == 3 and 0.05 <= fitted["t"] <= 0.2
    p = {r["case_id"]: cal.relevance(r, fitted) for r in records}
    assert p["U1"] > p["U2"] > p["U3"] > p["U4"] > p["U5"] and p["I1"] > p["I2"] > p["I3"]  # monotone within an area
    assert cal.relevance({"triage": None, "area": "KV"}, fitted) is None
    with pytest.raises(ValueError):
        cal.calibrate(records, set())


def test_list_factor_lowers_the_threshold():
    fitted = {"w": [0, 0, 0, 0], "t": 0.2}
    assert cal.ampel_for(0.15, fitted) == "gelb" and cal.ampel_for(0.15, fitted, list_factor=0.7) == "gruen"
    assert cal.ampel_for(0.05, fitted) == "rot" and cal.ampel_for(None, fitted) == "rot"
    assert cal.f2(8, 4, 2) == pytest.approx(40 / 52)


def test_display_score_puts_the_list_threshold_on_green():
    t = 0.1
    assert cal.display_score(None, t) == 0.0 and cal.display_score(0.0, t) == 0.0
    assert cal.display_score(1.0, t) == 100.0
    assert cal.display_score(0.1001, t) >= 60.0 > cal.display_score(0.1, t) > cal.display_score(0.0501, t) >= 40.0 > cal.display_score(0.05, t)
    values = [cal.display_score(p / 1000, t) for p in range(0, 1001)]
    assert values == sorted(values)  # monotone: the ranking is unchanged
    assert cal.display_score(0.5, t, green=70.0, yellow=30.0) == pytest.approx(70 + 30 * 0.4 / 0.9, abs=0.01)


def test_results_are_ranked_and_failures_stay_visible():
    records, selected = _records()
    records.append(dict(records[0], case_id="K1", case_ids=["K1"], area="KV", triage=None, deep=None))
    records.append(dict(records[0], case_id="F1", case_ids=["F1"], triage=None, deep=None, error="RelevanceError: x"))
    fitted = cal.calibrate(records, selected)
    results = period.to_results(records, fitted)
    assert [r.rank for r in results] == list(range(1, 13)) and results[0].case_id == "U1" and results[-1].case_id == "K1"
    by = {r.case_id: r for r in results}
    assert by["U1"].relevant and by["U1"].ampel == "gruen" and by["U1"].metadata["relevance"]["p"] == cal.relevance(records[0], fitted)
    config = TriageConfig()
    for result in results:  # the score is on the project's scale: the colours agree with the config thresholds
        assert config.ampel_for(result.final_score) == result.ampel, (result.case_id, result.final_score)
    assert [r.final_score for r in results] == sorted((r.final_score for r in results), reverse=True)
    assert by["U1"].relevance_reason == "Grund. Merksatz: Regel." and by["U1"].key_topics == ["K1"]
    assert by["K1"].ampel == "rot" and "nicht bewertet" in by["K1"].relevance_reason and by["K1"].apertus_score is None
    assert by["F1"].ampel == "gelb" and "von Hand prüfen" in by["F1"].relevance_reason  # never silently red
    assert by["F1"].final_score == config.yellow_threshold
    assert by["U5"].metadata["relevance"]["threshold"] == fitted["t"]
    longer = period.to_results(records, fitted, list_factor=0.3)
    assert sum(r.relevant for r in longer) >= sum(r.relevant for r in results)


def test_public_record_drops_suvas_decisions_on_other_judgements():
    record = {"case_id": "A", "precedents": [{"case_id": "B", "similarity": 0.5, "selected": True, "subject": "x"}]}
    assert period.public_record(record)["precedents"] == [{"case_id": "B", "similarity": 0.5}]


def test_config_carries_label_periods_and_list_factor(tmp_path):
    path = tmp_path / "triage.json"
    path.write_text(json.dumps({"label_periods": ["2025-Q4"], "list_factor": 0.8}), encoding="utf-8")
    config = load_config(path)
    assert config.label_periods == ("2025-Q4",) and config.list_factor == 0.8
    assert TriageConfig().list_factor == 0.3
    with pytest.raises(ValueError):
        TriageConfig(list_factor=0.0)


# ---------------------------------------------------------------- evaluation


def test_holdout_never_calibrates_on_the_measured_quarter():
    a, selected = _records()
    b = [dict(r, case_id="x" + r["case_id"], case_ids=["x" + r["case_id"]]) for r in a]
    selected |= {"x" + c for c in selected}
    folds = rev.holdout({"2025-Q4": a, "2026-Q1": b}, selected)
    assert [(f["train"], f["test"], f["direction"]) for f in folds] == [("2026-Q1", "2025-Q4", "rückwärts"), ("2025-Q4", "2026-Q1", "vorwärts")]
    m = rev.metrics(folds[1]["p"] > folds[1]["cal"]["t"], folds[1]["y"])
    assert m["tp"] + m["fn"] == 3 and m["recall"] == 1.0
    table = rev.threshold_table(folds)
    assert [row["factor"] for row in table] == list(rev.FACTORS)
    assert all(x["recall"] <= y["recall"] and x["selected"] <= y["selected"] for x, y in zip(table, table[1:]))  # lower threshold, longer list
    report = rev.render(folds, table, 0.7, "apertus-v1-5-70b")
    assert "0.7 **(eingestellt)**" in report and "2025-Q4 | 2026-Q1 | vorwärts" in report


def test_hits_at_counts_ties_proportionally():
    p, y = np.array([0.9, 0.5, 0.5, 0.5, 0.1]), np.array([True, True, False, False, True])
    assert rev.hits_at(p, y, k=2) == pytest.approx(1 + 1 / 3)
    assert rev.hits_at(p, y, k=10) == 3.0
