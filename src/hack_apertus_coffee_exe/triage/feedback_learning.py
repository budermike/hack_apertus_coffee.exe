"""Learn from the lawyers' relevance decisions (correction list of 28.11.2026, issue #29).

Before, a click on "relevant" / "nicht relevant" moved that one judgement by a fixed
number of points and nothing else changed. Now every decision is a training example.

The relevance value ``p0`` of a judgement comes from the calibration (``calibration.py``):
a logistic regression on the two Apertus scores and the area, fitted on the quarters where
Suva's decision is known. That model is not touched. On top of it a small correction is
learned from the decisions::

    logit p = logit p0 + sum of the corrections of the judgement's features

The features are the legal area (UV, IV, ...) and the criteria Apertus found in the
judgement (K1..K5, N1..N3). Each feature has one correction, a number that starts at 0.
The corrections are the maximum a posteriori estimate of a logistic regression with
``logit p0`` as fixed offset and a normal prior N(0, ``prior_sd``^2) on every correction
(Newton's method, the problem is convex, so the result does not depend on the order of
the clicks).

What follows from that:

- No decisions: all corrections are 0 and every score is exactly the stored one.
- A "nicht relevant" lowers the corrections of that judgement's area and criteria, so
  judgements that share them go down, the more they share the more. Judgements that
  share nothing do not move. A "relevant" does the opposite.
- How far they move is not a fixed number: it depends on how surprising the decision
  was (a "nicht relevant" on a judgement with p0 = 0.9 says more than on one with 0.1)
  and it grows with every further decision that points the same way.
- ``prior_sd`` limits how far a few clicks can move things.
- The list is protected (``protect_list``): a judgement the calibration put on the list
  stays on it. Learning may lower its score down to the list threshold and so move it to
  the end of the list, but only the lawyer's own decision on that judgement takes it off.
  Learning may add judgements to the list.

Why the protection: in the simulation with Suva's real decisions (``eval/feedback_sim.py``,
``results/tables/feedback_learning_sim.csv``) learning without it shortens the list and
loses judgements Suva selected, while the order of the list does not get measurably
better. The project prefers a longer list to a missed judgement (``list_factor``), so
decisions must not cost recall.

What it cannot learn: anything Apertus did not put into the area or the criteria, and
nothing about judgements Apertus did not score (they have no ``p0``).
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from . import calibration
from .config import TriageConfig, load_config

# Standard deviation of the prior on every correction (logit scale). Smaller = a single
# decision moves less. See eval/feedback_sim.py for what the values do.
DEFAULT_PRIOR_SD = 0.25

_P_MIN = 1e-4  # p0 is clipped to [_P_MIN, 1 - _P_MIN] before the logit


def canonical(case_id: str) -> str:
    """``8C_7_2025`` and ``8C_7/2025`` -> ``8C_7/2025``, so UI ids and case numbers compare equal."""
    cleaned = case_id.strip()
    if "/" in cleaned or "_" not in cleaned:
        return cleaned
    head, _, year = cleaned.rpartition("_")
    return f"{head}/{year}" if year.isdigit() and len(year) == 4 and "_" in head else cleaned


def feature_names(area: str | None, criteria: Iterable[str]) -> tuple[str, ...]:
    """Names of the features a judgement has, e.g. ``("area:UV", "crit:K1", "crit:K4")``."""
    names = [f"area:{area}"] if area else []
    names += sorted({f"crit:{c}" for c in criteria if isinstance(c, str) and c})
    return tuple(names)


@dataclass(frozen=True)
class Example:
    """One decided judgement: its base relevance, its features and the lawyer's decision."""

    p0: float
    features: tuple[str, ...]
    relevant: bool


def _logit(p: float) -> float:
    p = min(max(float(p), _P_MIN), 1.0 - _P_MIN)
    return math.log(p / (1.0 - p))


def fit_corrections(examples: Sequence[Example], prior_sd: float = DEFAULT_PRIOR_SD, steps: int = 50) -> dict[str, float]:
    """Corrections per feature: MAP estimate of the logistic regression with offset ``logit p0``."""
    names = sorted({name for example in examples for name in example.features})
    if not names:
        return {}
    index = {name: i for i, name in enumerate(names)}
    phi = np.zeros((len(examples), len(names)))
    for row, example in enumerate(examples):
        for name in example.features:
            phi[row, index[name]] = 1.0
    offset = np.array([_logit(example.p0) for example in examples])
    y = np.array([float(example.relevant) for example in examples])
    precision = 1.0 / (prior_sd * prior_sd)
    theta = np.zeros(len(names))
    for _ in range(steps):
        p = 1.0 / (1.0 + np.exp(-(offset + phi @ theta)))
        gradient = phi.T @ (p - y) + precision * theta
        hessian = (phi.T * (p * (1.0 - p))) @ phi + precision * np.eye(len(names))
        step = np.linalg.solve(hessian, gradient)
        theta -= step
        if float(np.abs(step).max()) < 1e-10:
            break
    return {name: round(float(value), 4) for name, value in zip(names, theta)}


def corrected(p0: float, features: Iterable[str], corrections: Mapping[str, float]) -> float:
    """Relevance after the corrections of the judgement's features."""
    shift = sum(corrections.get(name, 0.0) for name in features)
    return 1.0 / (1.0 + math.exp(-(_logit(p0) + shift))) if shift else float(p0)


# --- triage result rows (results/triage_*.json, data/processed/triage_periods/*.json) ---


def row_case_ids(row: Mapping[str, Any]) -> tuple[str, ...]:
    ids = (row.get("metadata") or {}).get("case_ids")
    return tuple(ids) if isinstance(ids, list) and ids else (row["case_id"],)


def row_base(row: Mapping[str, Any]) -> tuple[float, float, tuple[str, ...]] | None:
    """``(p0, list threshold, features)`` of a triage result; ``None`` if Apertus gave no score."""
    relevance = (row.get("metadata") or {}).get("relevance")
    if not isinstance(relevance, dict):
        return None
    p0, threshold = relevance.get("p"), relevance.get("threshold")
    if isinstance(p0, bool) or not isinstance(p0, (int, float)) or not isinstance(threshold, (int, float)) or threshold <= 0:
        return None
    criteria = row.get("key_topics") if isinstance(row.get("key_topics"), list) else []
    return float(p0), float(threshold), feature_names(relevance.get("area"), criteria)


@dataclass(frozen=True)
class Learned:
    """The corrections learned from a set of decisions, and how to score a judgement with them."""

    corrections: dict[str, float] = field(default_factory=dict)
    used: int = 0
    ignored: int = 0
    prior_sd: float = DEFAULT_PRIOR_SD
    green: float = 60.0
    yellow: float = 40.0
    protect_list: bool = True

    @property
    def active(self) -> bool:
        """False while no decision could be used: every score stays as stored."""
        return self.used > 0 and any(self.corrections.values())

    def relevance(self, row: Mapping[str, Any]) -> float | None:
        """Corrected relevance value, or ``None`` if the judgement does not move (or has no base value)."""
        base = row_base(row)
        if base is None:
            return None
        p0, _, features = base
        if not any(self.corrections.get(name) for name in features):
            return None
        return round(corrected(p0, features, self.corrections), 4)

    def score(self, row: Mapping[str, Any]) -> float | None:
        """Relevance on the 0-100 scale of the UI; ``None`` = keep the stored score."""
        p = self.relevance(row)
        if p is None:
            return None
        p0, t_list, _ = row_base(row)
        score = calibration.display_score(p, t_list, self.green, self.yellow)
        if self.protect_list and p0 > t_list:
            score = max(score, self.green)  # was on the list: stays on it, at its end at worst
        return score

    def ampel(self, row: Mapping[str, Any]) -> str | None:
        p = self.relevance(row)
        if p is None:
            return None
        p0, t_list, _ = row_base(row)
        if self.protect_list and p0 > t_list:
            return calibration.AMPEL_GREEN
        return calibration.ampel_for(p, {"t": t_list})

    def summary(self) -> dict[str, Any]:
        """For ``GET /api/config``: what was learned, largest correction first."""
        ordered = sorted(self.corrections.items(), key=lambda item: -abs(item[1]))
        return {
            "used": self.used,
            "ignored": self.ignored,
            "priorSd": self.prior_sd,
            "listProtected": self.protect_list,
            "corrections": [{"feature": name, "value": value} for name, value in ordered if value],
        }


class FeedbackLearner:
    """Turns the lawyers' decisions and the triage results on show into ``Learned``.

    The fit takes well under a millisecond for realistic numbers of decisions; it is
    cached anyway, because the judgement list is served on every search.
    """

    def __init__(
        self, config: TriageConfig | None = None, prior_sd: float = DEFAULT_PRIOR_SD, protect_list: bool = True
    ) -> None:
        self.config = config or load_config()
        self.prior_sd = float(prior_sd)
        self.protect_list = bool(protect_list)
        self._lock = threading.Lock()
        self._cache: tuple[Any, Learned] | None = None

    def learned(self, decisions: Mapping[str, bool], rows: Iterable[Mapping[str, Any]]) -> Learned:
        """``decisions``: ``{case number or UI id: relevant?}``, the latest decision per judgement.

        ``rows`` give the base relevance and the features of the decided judgements. A
        decision on a judgement that is not in ``rows``, or that Apertus did not score,
        cannot be used and is counted in ``ignored``.
        """
        wanted = {canonical(case_id): bool(relevant) for case_id, relevant in decisions.items()}
        examples: dict[tuple[str, ...], Example] = {}
        if wanted:
            for row in rows:
                case_ids = row_case_ids(row)
                hit = next((cid for cid in case_ids if cid in wanted), None)
                base = row_base(row) if hit else None
                if hit and base:
                    examples[case_ids] = Example(base[0], base[2], wanted[hit])
        covered = {cid for case_ids in examples for cid in case_ids}
        ignored = sum(1 for case_id in wanted if case_id not in covered)
        used = [examples[key] for key in sorted(examples)]
        key = (tuple(used), ignored, self.prior_sd, self.protect_list)
        with self._lock:
            if self._cache is None or self._cache[0] != key:
                self._cache = (
                    key,
                    Learned(
                        corrections=fit_corrections(used, self.prior_sd),
                        used=len(used),
                        ignored=ignored,
                        prior_sd=self.prior_sd,
                        green=self.config.green_threshold,
                        yellow=self.config.yellow_threshold,
                        protect_list=self.protect_list,
                    ),
                )
            return self._cache[1]
