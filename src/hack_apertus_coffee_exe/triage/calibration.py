"""From the two Apertus scores to one relevance value and a list (issue #62, docs/ISSUE_RECALL.md).

The relevance value ``p`` is a logistic regression with four parameters: the
triage score, the score of the second pass ("Vertiefung") and one intercept
each for accident insurance and for the other areas. Within an area ``p`` is
strictly monotone in what Apertus said, so Apertus still decides the order.

Threshold: a judgement goes on the list when ``p > F2 / 5``. That is the point
where adding a judgement with probability ``p`` no longer raises the expected
F2 (a miss weighs as much as four false alarms). ``list_factor`` < 1 lowers the
threshold on purpose to trade a longer list for higher recall.
"""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np

AMPEL_GREEN, AMPEL_YELLOW, AMPEL_RED = "gruen", "gelb", "rot"


def f2(tp: int, fp: int, fn: int) -> float:
    return 5 * tp / (5 * tp + 4 * fn + fp) if tp else 0.0


def features(record: dict[str, Any]) -> tuple[int, int] | None:
    """``(triage score, Vertiefung score)`` of a scored judgement; ``None`` if Apertus gave no triage answer."""
    if not record.get("triage"):
        return None
    return record["triage"]["d_score"], (record.get("deep") or {}).get("e_score", 0)


def _x(scores: tuple[int, int], area: str) -> list[float]:
    return [scores[0] / 100.0, scores[1] / 100.0, 1.0 if area == "UV" else 0.0, 0.0 if area == "UV" else 1.0]


def fit(X: Sequence[Sequence[float]], y: Sequence[float], l2: float = 0.05, steps: int = 60) -> np.ndarray:
    """Logistic regression by Newton's method with a small ridge penalty."""
    X, y, w = np.asarray(X, float), np.asarray(y, float), np.zeros(len(X[0]))
    for _ in range(steps):
        p = 1 / (1 + np.exp(-X @ w))
        w -= np.linalg.solve((X.T * (p * (1 - p))) @ X + l2 * np.eye(len(w)), X.T @ (p - y) + l2 * w)
    return w


def predict(cal: dict[str, Any], scores: tuple[int, int], area: str) -> float:
    return float(1 / (1 + np.exp(-np.dot(cal["w"], _x(scores, area)))))


def threshold(p: Sequence[float], y: Sequence[int], start: float = 0.16, rounds: int = 12) -> float:
    """Fixed point of the rule ``t = F2 / 5`` on the calibration data."""
    p, y, t = np.asarray(p), np.asarray(y), start
    for _ in range(rounds):
        sel = p > t
        t = max(0.05, f2(int((sel & (y == 1)).sum()), int((sel & (y == 0)).sum()), int((~sel & (y == 1)).sum())) / 5)
    return round(float(t), 4)


def calibrate(records: Sequence[dict[str, Any]], selected_ids: set[str]) -> dict[str, Any]:
    """Fit weights and threshold on scored judgements whose Suva decision is known."""
    rows = [(features(r), r["area"], int(bool(set(r["case_ids"]) & selected_ids))) for r in records if features(r)]
    if not rows or not any(y for *_, y in rows):
        raise ValueError("Calibration needs scored judgements with at least one Suva-selected case")
    w = fit([_x(s, a) for s, a, _ in rows], [y for *_, y in rows])
    cal: dict[str, Any] = {"w": [round(float(v), 4) for v in w], "n": len(rows), "n_pos": sum(y for *_, y in rows)}
    cal["t"] = threshold([predict(cal, s, a) for s, a, _ in rows], [y for *_, y in rows])
    return cal


def relevance(record: dict[str, Any], cal: dict[str, Any]) -> float | None:
    """Relevance value 0..1, or ``None`` when the judgement was not scored (other area or failed call)."""
    scores = features(record)
    return None if scores is None else round(predict(cal, scores, record["area"]), 4)


def display_score(p: float | None, t_list: float, green: float = 60.0, yellow: float = 40.0) -> float:
    """Relevance value on the project's 0-100 traffic-light scale (``configs/triage.json``).

    Piecewise linear and monotone, so the ranking is the one Apertus and the
    calibration gave: the list threshold lands on ``green``, half of it on
    ``yellow``. That keeps the existing thresholds of config, exports and UI
    valid; the raw value stays in ``metadata["relevance"]["p"]``.
    """
    if p is None:
        return 0.0
    half = t_list / 2
    if p > t_list:
        return round(green + (100.0 - green) * (p - t_list) / (1.0 - t_list), 2)
    if p > half:
        return min(round(yellow + (green - yellow) * (p - half) / (t_list - half), 2), green - 0.01)
    return min(round(yellow * p / half, 2), yellow - 0.01)


def ampel_for(p: float | None, cal: dict[str, Any], list_factor: float = 1.0) -> str:
    """Green = on the list, yellow = band just below (worth a look), red = not relevant or not scored."""
    t = cal["t"] * list_factor
    if p is None:
        return AMPEL_RED
    return AMPEL_GREEN if p > t else AMPEL_YELLOW if p > t / 2 else AMPEL_RED
