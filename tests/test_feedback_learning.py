"""Learning from the lawyers' relevance decisions (no network, no model)."""

import json
import math
import random
from pathlib import Path

import numpy as np
import pytest

from hack_apertus_coffee_exe.triage import calibration
from hack_apertus_coffee_exe.triage.config import TriageConfig
from hack_apertus_coffee_exe.triage.feedback_learning import (
    Example,
    FeedbackLearner,
    canonical,
    corrected,
    feature_names,
    fit_corrections,
    apply_decision,
    row_base,
)

T_LIST = 0.05


def row(case_id, p, area="UV", criteria=("K1",), **extra):
    """A triage result as in results/triage_*.json, reduced to what learning reads."""
    score = calibration.display_score(p, T_LIST) if p is not None else 0.0
    return {
        "case_id": case_id,
        "final_score": score,
        "key_topics": list(criteria),
        "metadata": {"case_ids": extra.get("case_ids", [case_id]), "relevance": {"p": p, "threshold": T_LIST, "area": area}},
    }


ROWS = [
    row("8C_1/2026", 0.80, "UV", ("K1", "K2")),
    row("8C_2/2026", 0.60, "UV", ("K1",)),
    row("8C_3/2026", 0.07, "UV", ("K2",)),      # just on the list
    row("8C_4/2026", 0.04, "UV", ("K1", "K2")),  # just below the list
    row("9C_5/2026", 0.30, "IV", ("K4",)),       # shares nothing with the UV judgements
    row("8C_6/2026", None, "KV", ()),            # Apertus did not score it
]


def learner(**kwargs):
    return FeedbackLearner(config=TriageConfig(), **kwargs)


def scores(learned):
    """Score per case after learning; the stored one where learning does not move it."""
    return {r["case_id"]: (learned.score(r) if learned.score(r) is not None else r["final_score"]) for r in ROWS}


BASE = {r["case_id"]: r["final_score"] for r in ROWS}


def test_without_decisions_every_score_stays_exactly_as_stored():
    learned = learner().learned({}, ROWS)
    assert not learned.active and learned.corrections == {} and learned.used == 0
    assert all(learned.score(r) is None and learned.ampel(r) is None for r in ROWS)


def test_not_relevant_lowers_what_shares_area_or_criteria_and_nothing_else():
    learned = learner().learned({"8C_1/2026": False}, ROWS)
    assert learned.active and learned.used == 1 and learned.ignored == 0
    assert set(learned.corrections) == {"area:UV", "crit:K1", "crit:K2"}
    assert all(value < 0 for value in learned.corrections.values())
    after = scores(learned)
    assert after["8C_2/2026"] < BASE["8C_2/2026"]            # same area, one shared criterion
    assert after["9C_5/2026"] == BASE["9C_5/2026"]           # nothing shared: not moved
    assert learned.score(ROWS[4]) is None and learned.score(ROWS[5]) is None
    assert all(after[cid] <= BASE[cid] for cid in BASE)      # a "no" never raises a score


def test_the_more_a_judgement_shares_the_more_it_moves():
    learned = learner().learned({"8C_1/2026": False}, ROWS)
    rel = {r["case_id"]: (learned.relevance(r), r["metadata"]["relevance"]["p"]) for r in ROWS[:3]}
    drop = {cid: math.log(p0 / (1 - p0)) - math.log(p / (1 - p)) for cid, (p, p0) in rel.items()}
    # 8C_1 has area + K1 + K2, 8C_2 area + K1: three corrections against two
    assert drop["8C_1/2026"] == pytest.approx(1.5 * drop["8C_2/2026"], rel=1e-2)


def test_relevant_raises_and_can_put_a_judgement_on_the_list():
    learned = learner(prior_sd=1.0).learned({"8C_3/2026": True}, ROWS)
    after = scores(learned)
    assert all(after[cid] >= BASE[cid] for cid in BASE)
    assert BASE["8C_4/2026"] < 60 <= after["8C_4/2026"]


def test_a_surprising_decision_teaches_more_than_an_expected_one():
    surprising = fit_corrections([Example(0.9, ("area:UV",), False)])
    expected = fit_corrections([Example(0.1, ("area:UV",), False)])
    assert surprising["area:UV"] < expected["area:UV"] < 0


def test_more_decisions_the_same_way_move_more_and_the_order_does_not_matter():
    one = fit_corrections([Example(0.8, ("area:UV",), False)])
    three = [Example(0.8, ("area:UV",), False), Example(0.7, ("area:UV", "crit:K1"), False), Example(0.6, ("area:UV",), False)]
    learned = fit_corrections(three)
    assert learned["area:UV"] < one["area:UV"] < 0
    shuffled = three[:]
    random.Random(3).shuffle(shuffled)
    assert fit_corrections(shuffled) == learned
    # contradicting decisions cancel out
    both = fit_corrections([Example(0.5, ("area:UV",), False), Example(0.5, ("area:UV",), True)])
    assert both == {"area:UV": 0.0}


def test_prior_limits_how_far_one_decision_moves_things():
    weak = fit_corrections([Example(0.8, ("area:UV",), False)], prior_sd=0.1)
    strong = fit_corrections([Example(0.8, ("area:UV",), False)], prior_sd=1.0)
    assert strong["area:UV"] < weak["area:UV"] < 0
    assert abs(weak["area:UV"]) < 0.01


def test_corrections_are_the_maximum_a_posteriori_estimate():
    examples = [
        Example(0.8, ("area:UV", "crit:K1"), False),
        Example(0.3, ("area:UV", "crit:K2"), True),
        Example(0.6, ("area:IV", "crit:K1"), False),
        Example(0.2, ("area:IV",), False),
    ]
    sd = 0.7
    theta = fit_corrections(examples, prior_sd=sd)
    names = sorted(theta)
    # gradient of the negative log posterior is zero at the estimate
    gradient = {name: theta[name] / sd**2 for name in names}
    for example in examples:
        p = corrected(example.p0, example.features, theta)
        for name in example.features:
            gradient[name] += p - float(example.relevant)
    assert max(abs(value) for value in gradient.values()) < 1e-3


def test_a_judgement_on_the_list_stays_on_it_unless_the_lawyer_decides_otherwise():
    decisions = {"8C_1/2026": False, "8C_2/2026": False}
    protected = learner(prior_sd=2.0).learned(decisions, ROWS)
    open_ = learner(prior_sd=2.0, protect_list=False).learned(decisions, ROWS)
    just_on_list = ROWS[2]
    assert open_.score(just_on_list) < 60 and open_.ampel(just_on_list) != calibration.AMPEL_GREEN
    assert protected.score(just_on_list) == 60 and protected.ampel(just_on_list) == calibration.AMPEL_GREEN
    assert protected.relevance(just_on_list) == open_.relevance(just_on_list)  # the value itself is not hidden
    assert protected.summary()["listProtected"] is True


def test_decisions_without_scores_are_counted_as_ignored():
    learned = learner().learned({"8C_6/2026": True, "5A_9/2020": False, "8C_1_2026": False}, ROWS)
    assert (learned.used, learned.ignored) == (1, 2)
    assert learned.summary()["corrections"][0]["feature"] in {"area:UV", "crit:K1", "crit:K2"}
    only_unusable = learner().learned({"8C_6/2026": True}, ROWS)
    assert not only_unusable.active and only_unusable.ignored == 1


def test_case_numbers_in_both_spellings_and_joined_cases():
    assert canonical("8C_7_2025") == canonical("8C_7/2025") == "8C_7/2025"
    assert canonical("8C_7") == "8C_7" and canonical(" 4A_12_2026 ") == "4A_12/2026"
    joined = row("1D_1/2026", 0.5, "UV", ("K1",), case_ids=["1D_1/2026", "1D_2/2026"])
    learned = learner().learned({"1D_2_2026": False}, [joined])
    assert learned.used == 1 and learned.corrections["area:UV"] < 0


def test_same_decisions_are_not_fitted_twice():
    one = learner()
    first = one.learned({"8C_1/2026": False}, ROWS)
    assert one.learned({"8C_1_2026": False}, ROWS) is first
    assert one.learned({"8C_1/2026": True}, ROWS) is not first


def test_feature_names_and_rows_without_base_value():
    assert feature_names("UV", ["K2", "K1", "K2", None, ""]) == ("area:UV", "crit:K1", "crit:K2")
    assert feature_names(None, []) == ()
    assert row_base(ROWS[5]) is None and row_base({"case_id": "x"}) is None
    assert row_base({"case_id": "x", "metadata": {"relevance": {"p": True, "threshold": 0.05}}}) is None
    assert row_base(ROWS[0]) == (0.80, T_LIST, ("area:UV", "crit:K1", "crit:K2"))


def test_on_the_committed_results_one_decision_moves_scores_one_way_only():
    """The Q2 results in the repository: a 'no' on the best judgement lowers or keeps every score."""
    rows = json.loads(Path("results/triage_q2_2026.json").read_text(encoding="utf-8"))
    scored = [r for r in rows if row_base(r)]
    best = max(scored, key=lambda r: r["final_score"])
    learned = FeedbackLearner().learned({best["case_id"]: False}, rows)
    moved = [(learned.score(r), r["final_score"]) for r in rows if learned.score(r) is not None]
    assert moved and all(new <= old + 0.02 for new, old in moved)  # 0.02: the stored threshold is rounded
    assert max(old - new for new, old in moved) < 8                 # one click is no landslide
    on_list = [r for r in scored if r["final_score"] >= 60]
    assert all((learned.score(r) or r["final_score"]) >= 60 for r in on_list)
    assert np.isfinite([new for new, _ in moved]).all()


def test_learning_colours_with_the_stored_yellow_bound_not_half_the_list_threshold():
    """Rows written with a recall-based yellow bound (#75) keep it when learning moves them."""
    base = row("8C_9/2026", 0.045, "UV", ("K1",))
    base["metadata"]["relevance"]["yellow_threshold"] = 0.044  # well above half of T_LIST (0.025)
    rows = [*ROWS, base]
    learned = learner(protect_list=False).learned({"8C_1/2026": False}, rows)
    p = learned.relevance(base)
    assert p is not None and 0.025 < p <= 0.044  # yellow by the old half rule, red by the stored bound
    assert learned.ampel(base) == calibration.AMPEL_RED
    assert learned.score(base) < TriageConfig().yellow_threshold


def test_apply_decision_clamps_to_the_traffic_light_band():
    assert apply_decision(87.7, False) == 39.9 and apply_decision(12.0, False) == 12.0
    assert apply_decision(3.3, True) == 60.0 and apply_decision(91.0, True) == 91.0
    assert apply_decision(55.0, None) == 55.0
    assert apply_decision(80.0, False, green=70.0, yellow=50.0) == 49.9
