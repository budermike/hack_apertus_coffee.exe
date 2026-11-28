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


PASSAGE_SEGS = [
    {"id": "E2.1", "sec": "law", "text": (
        "Streitig ist die Leistungspflicht. Im Fokus steht die Frage, ob der Unfall vom 13. März 2018 zu einer "
        "richtungsgebenden Verschlimmerung des Vorzustands führte (vgl. Urteil 8C_7/2022 vom 22. April 2022 E. 5.1). "
        "Das Gutachten des Dr. med. F.________ vom 25. Juni 2025 ist beweiskräftig."
    )},
    {"id": "E5.1", "sec": "law", "text": "Die Vorinstanz begründete einlässlich, weshalb sie weitere Abklärungen als angezeigt erachtete."},
]


def test_sentences_do_not_end_at_abbreviations_or_dates():
    assert [s[:12] for s in rel.sentences(PASSAGE_SEGS[0]["text"])] == ["Streitig ist", "Im Fokus ste", "Das Gutachte"]
    assert len(rel.sentences("Der Unfall geschah am 3. Mai 2021. Die Suva lehnte ab, gestützt auf Art. 6 Abs. 2 UVG. Sie zahlte 800 Fr. Der Fall ist klar.")) == 4


def test_locate_passage_finds_the_wording_of_the_judgement_behind_a_reworded_quote():
    # Apertus states as a fact what the judgement puts as a question: the judgement's own sentence is shown
    quote = "Der Unfall vom 13. März 2018 bewirkte eine richtungsgebende Verschlimmerung des Vorzustands."
    assert rel.quote_status(quote, rel._squash(" ".join(s["text"] for s in PASSAGE_SEGS))) == "fehlt"
    passage = rel.locate_passage(quote, "E2.1", PASSAGE_SEGS)
    assert passage["at"] == "E2.1" and passage["text"].startswith("Im Fokus steht die Frage, ob der Unfall")
    assert passage["text"] in PASSAGE_SEGS[0]["text"]  # word for word from the judgement, nothing made up
    # the mark Apertus named is wrong: the passage is found in the rest of the judgement
    assert rel.locate_passage(quote, "E9.9", PASSAGE_SEGS)["at"] == "E2.1"
    # nothing in the judgement is close: no passage, and none is invented
    assert rel.locate_passage("Das Bundesgericht ändert seine Rechtsprechung zum Schleudertrauma grundlegend", "E2.1", PASSAGE_SEGS) is None
    assert rel.locate_passage("zu kurz", "E2.1", PASSAGE_SEGS) is None


class _FrClient:
    def __init__(self, answer):
        self.answer, self.users = answer, []

    def call(self, stage, key, system, user, schema, max_tokens):
        self.users.append(user)
        # the gateway returns the fields in alphabetical order: the prefixes keep the order of the texts
        assert list(schema["properties"]) == sorted(schema["properties"]) == ["a_motivation_fr", "b_question_fr", "c_regle_fr"]
        return self.answer


def test_french_reason_and_rule_stay_two_texts(tmp_path):
    cache = tmp_path / "fr_cache.json"
    texts = {"reason_de": "Das Urteil betrifft den Kausalzusammenhang und ist für die Suva relevant.",
             "question_de": "Besteht ein Kausalzusammenhang?", "rule_de": "Der Rückfall ist nach Art. 11 UVV zu entschädigen."}
    good = {"a_motivation_fr": "L'arrêt porte sur le lien de causalité et intéresse la Suva.",
            "b_question_fr": "Existe-t-il un lien de causalité ?", "c_regle_fr": "La rechute est indemnisée selon l'art. 11 OLAA."}
    client = _FrClient(good)
    out = rel.translate_reason_fr(client, cache_path=cache, **texts)
    assert out == {"reason_fr": good["a_motivation_fr"], "question_fr": good["b_question_fr"], "rule_fr": good["c_regle_fr"]}
    assert "Texte A, motivation" in client.users[0] and "Texte C, à retenir pour la pratique" in client.users[0]
    assert rel.translate_reason_fr(_FrClient({}), cache_path=cache, **texts) == out  # from the cache

    # Apertus puts the rule into the field of the reason as well: not shown, not stored
    mixed = dict(good, a_motivation_fr="La rechute est indemnisée selon l'art. 11 OLAA.")
    other = dict(texts, question_de="Ist der Rückfall zu entschädigen?")
    assert rel.translate_reason_fr(_FrClient(mixed), cache_path=cache, **other) == {"reason_fr": "", "question_fr": "", "rule_fr": ""}
    assert rel.fr_cache_key(other["reason_de"], other["question_de"], other["rule_de"]) not in json.loads(cache.read_text())


def test_french_cache_entries_from_before_the_fix_are_not_read():
    old_key = rel.hashlib.sha256(json.dumps(["Grund", "Frage", "Regel"], ensure_ascii=False).encode()).hexdigest()
    assert rel.fr_cache_key("Grund", "Frage", "Regel") != old_key


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


# ---------------------------------------------------------------- whole run (score files, list)

# One labelled quarter (2025-Q4, Suva selected 8C_1/2025) and the quarter to score (2026-Q1).
RUN_CASES = [
    ("8C_1/2025", "2025-10-10", "Unfallversicherung (Kausalzusammenhang)", 90),
    ("8C_2/2025", "2025-11-11", "Unfallversicherung (Taggeld)", 30),
    ("9C_3/2025", "2025-12-12", "Invalidenversicherung (Rente)", 10),
    ("8C_4/2026", "2026-01-15", "Unfallversicherung (Kausalzusammenhang)", 85),
    ("8C_5/2026", "2026-02-10", "Unfallversicherung (Taggeld)", 20),
]


class GatewayByCase:
    """Stands in for the OpenAI client: one answer (or exception) per case number and stage."""

    def __init__(self, broken=None):
        self.broken = broken or {}  # case number -> exception to raise, or text to answer
        self.asked: list[tuple[str, str]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        stage = kwargs["response_format"]["json_schema"]["name"]
        case_id = kwargs["messages"][1]["content"].split("Metadaten des zu beurteilenden Urteils: ")[1].split(" ")[0]
        self.asked.append((stage, case_id))
        answer = self.broken.get(case_id)
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            score = next(s for c, _, _, s in RUN_CASES if c == case_id)
            answer = json.dumps(dict(TRIAGE, d_score=score) if stage == "triage" else DEEP)
        return _answer(answer)


def _run_folders(tmp_path):
    raw, judgements = tmp_path / "raw", tmp_path / "judgements"
    for case_id, date, subject, _ in RUN_CASES:
        folder = raw / date[:7]
        folder.mkdir(parents=True, exist_ok=True)
        record = {"case_id": case_id, "case_ids": [case_id], "decision_date": date, "subject": subject, "language": "de",
                  "text": TEXT.replace("8C_1/2026", case_id)}
        (folder / f"{date}_{case_id.replace('/', '_')}.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    judgements.mkdir()
    import zipfile

    with zipfile.ZipFile(judgements / "labels.xlsx", "w") as archive:  # the label list is read as text from the xlsx
        archive.writestr("xl/sharedStrings.xml", "<sst><si><t>BGer-Urteil 8C_1/2025</t></si></sst>")
    return raw, judgements


def _run(tmp_path, gateway, **kwargs):
    raw, judgements = _run_folders(tmp_path)
    config = GatewayConfig(api_key="test", base_url="http://gateway.invalid", model="apertus-v1-5-70b")
    client = rel.ApertusRelevanceClient(config=config, client=gateway)
    return period.run("2026-Q1", label_periods=("2025-Q4",), raw_dir=raw, judgements_dir=judgements,
                      tables_dir=tmp_path / "tables", client=client, workers=1, log=lambda _: None, **kwargs)


def test_not_asked_tells_a_gateway_failure_from_a_final_invalid_answer():
    assert rel.not_asked({"error": "APITimeoutError: Request timed out."})
    assert rel.not_asked({"error": "ValueError: Missing API key: set LLM_API_KEY or APERTUS_API_KEY in .env or environment."})
    assert not rel.not_asked({"error": "RelevanceError: triage 2026-01-15_8C_4-2026: invalid JSON (Expecting value)"})
    assert not rel.not_asked({"error": None}) and not rel.not_asked({})


def test_run_writes_the_score_files_and_the_results(tmp_path):
    out = _run(tmp_path, GatewayByCase())
    assert sorted(p.name for p in (tmp_path / "tables").iterdir()) == ["relevance_scores_q1_2026.json", "relevance_scores_q4_2025.json"]
    assert out["calibration"]["periods"] == ["2025-Q4"] and out["calibration"]["n"] == 3 and out["calibration"]["n_pos"] == 1
    assert [r.case_id for r in out["results"]] == ["8C_4/2026", "8C_5/2026"]
    assert all(r.llm_model == "apertus-v1-5-70b" for r in out["results"])


def test_run_writes_nothing_when_apertus_could_not_be_asked(tmp_path):
    tables = tmp_path / "tables"
    tables.mkdir()
    committed = tables / "relevance_scores_q1_2026.json"
    committed.write_text("the committed scores", encoding="utf-8")
    gateway = GatewayByCase({"8C_5/2026": TimeoutError("gateway timed out")})
    with pytest.raises(period.IncompleteRun) as stopped:
        _run(tmp_path, gateway)
    message = str(stopped.value)
    assert "2026-Q1: 1" in message and "2025-Q4" not in message and "8C_5/2026" in message and "TimeoutError" in message
    assert committed.read_text(encoding="utf-8") == "the committed scores"
    assert [p.name for p in tables.iterdir()] == ["relevance_scores_q1_2026.json"]
    assert len({case for _, case in gateway.asked}) == 5  # every judgement was tried before the run stopped


def test_run_stops_when_a_labelled_quarter_is_incomplete(tmp_path):
    with pytest.raises(period.IncompleteRun, match="2025-Q4: 1"):
        _run(tmp_path, GatewayByCase({"8C_2/2025": ConnectionError("no route to host")}))
    assert not (tmp_path / "tables").exists()


def test_allow_failed_writes_everything_and_shows_the_judgement_in_yellow(tmp_path):
    out = _run(tmp_path, GatewayByCase({"8C_5/2026": TimeoutError("gateway timed out")}), allow_failed=True)
    assert (tmp_path / "tables" / "relevance_scores_q1_2026.json").exists()
    failed = next(r for r in out["results"] if r.case_id == "8C_5/2026")
    assert failed.ampel == "gelb" and "von Hand prüfen" in failed.relevance_reason and "TimeoutError" in failed.relevance_reason


def test_an_answer_that_stays_invalid_is_final_and_does_not_stop_the_run(tmp_path):
    gateway = GatewayByCase({"8C_5/2026": "{not json"})  # both tries fail: RelevanceError
    out = _run(tmp_path, gateway)
    assert gateway.asked.count(("triage", "8C_5/2026")) == 2
    failed = next(r for r in out["results"] if r.case_id == "8C_5/2026")
    assert failed.ampel == "gelb" and "RelevanceError" in failed.relevance_reason
    stored = json.loads((tmp_path / "tables" / "relevance_scores_q1_2026.json").read_text(encoding="utf-8"))
    assert [bool(r["error"]) for r in stored] == [False, True]


def _cli_relevance(tmp_path, monkeypatch, gateway, *extra):
    from hack_apertus_coffee_exe.triage import cli

    raw, judgements = _run_folders(tmp_path)
    monkeypatch.setattr(rel, "_build_client", lambda config, timeout=60, max_retries=3: gateway)
    return cli.main([
        "relevance", "--period", "2026-Q1", "--label-periods", "2025-Q4", "--input", str(raw),
        "--judgements-dir", str(judgements), "--tables-dir", str(tmp_path / "tables"), "--export-dir", str(tmp_path / "out"),
        "--output", str(tmp_path / "out" / "triage.json"), "--no-cache", "--workers", "1", *extra,
    ])


def test_cli_exits_with_2_and_leaves_the_suva_list_alone(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    out.mkdir()
    (out / "recall_q1_2026.txt").write_text("8C_4/2026\n", encoding="utf-8")
    code = _cli_relevance(tmp_path, monkeypatch, GatewayByCase({"8C_4/2026": TimeoutError("gateway timed out")}))
    assert code == 2
    assert "Nothing was written" in capsys.readouterr().err
    assert (out / "recall_q1_2026.txt").read_text(encoding="utf-8") == "8C_4/2026\n"
    assert [p.name for p in out.iterdir()] == ["recall_q1_2026.txt"] and not (tmp_path / "tables").exists()


def test_cli_writes_list_scores_and_calibration_when_every_judgement_was_asked(tmp_path, monkeypatch):
    assert _cli_relevance(tmp_path, monkeypatch, GatewayByCase(), "--list-factor", "1.0") == 0
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["ranking_q1_2026.csv", "recall_q1_2026.txt", "triage.json"]
    listed = (tmp_path / "out" / "recall_q1_2026.txt").read_text(encoding="utf-8").strip().split(", ")
    assert listed[0] == "8C_4/2026" and set(listed) <= {"8C_4/2026", "8C_5/2026"}  # best first, only the scored quarter
    assert sorted(p.name for p in (tmp_path / "tables").iterdir()) == [
        "relevance_calibration_q1_2026.json", "relevance_scores_q1_2026.json", "relevance_scores_q4_2025.json",
    ]


def test_cli_allow_failed_keeps_the_old_behaviour(tmp_path, monkeypatch):
    code = _cli_relevance(tmp_path, monkeypatch, GatewayByCase({"8C_5/2026": TimeoutError("gateway timed out")}), "--allow-failed")
    assert code == 0 and (tmp_path / "out" / "recall_q1_2026.txt").exists()
