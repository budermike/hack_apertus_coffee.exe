"""From the two Apertus scores to one relevance value and a list (issue #62, docs/ISSUE_RECALL.md).

The relevance value ``p`` is a logistic regression with four parameters: the
triage score, the score of the second pass ("Vertiefung") and one intercept
each for accident insurance and for the other areas. Within an area ``p`` is
strictly monotone in what Apertus said, so Apertus still decides the order.

Threshold: a judgement goes on the list when ``p > F2 / 5``. That is the point
where adding a judgement with probability ``p`` no longer raises the expected
F2 (a miss weighs as much as four false alarms). ``list_factor`` < 1 lowers the
threshold on purpose to trade a longer list for higher recall.

Recall target (#75): with ``target_recall`` the list threshold is instead the
highest value at which that share of the known Suva judgements of the labelled
periods is on the list (0.9 = "90 % drin"); ``yellow_recall`` sets the lower bound
of yellow the same way (1.0 = every known Suva judgement is at least yellow).
"""

from __future__ import annotations

import math
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


def recall_threshold(p: Sequence[float], y: Sequence[int], target: float) -> float:
    """Highest threshold at which at least ``target`` of the positives have ``p > threshold``.

    With ``n`` positives, the ``ceil(target * n)``-th highest positive value is the last
    one that must stay on the list; the threshold sits just below it.
    """
    positives = sorted((float(v) for v, label in zip(p, y) if label), reverse=True)
    if not positives:
        raise ValueError("A recall target needs at least one Suva-selected case")
    k = min(len(positives), max(1, math.ceil(target * len(positives) - 1e-9)))
    return round(max(positives[k - 1] - 1e-4, 0.0), 6)


def calibrate(
    records: Sequence[dict[str, Any]],
    selected_ids: set[str],
    target_recall: float | None = None,
    yellow_recall: float | None = None,
) -> dict[str, Any]:
    """Fit weights and threshold on scored judgements whose Suva decision is known.

    ``t`` is the F2 threshold. With ``target_recall`` / ``yellow_recall`` the list
    threshold ``t_recall`` and the lower bound of yellow ``t_yellow`` come from the
    share of known Suva judgements that must lie above them (#75).
    """
    rows = [(features(r), r["area"], int(bool(set(r["case_ids"]) & selected_ids))) for r in records if features(r)]
    if not rows or not any(y for *_, y in rows):
        raise ValueError("Calibration needs scored judgements with at least one Suva-selected case")
    w = fit([_x(s, a) for s, a, _ in rows], [y for *_, y in rows])
    cal: dict[str, Any] = {"w": [round(float(v), 4) for v in w], "n": len(rows), "n_pos": sum(y for *_, y in rows)}
    p, y = [predict(cal, s, a) for s, a, _ in rows], [label for *_, label in rows]
    cal["t"] = threshold(p, y)
    if target_recall is not None:
        cal["target_recall"], cal["t_recall"] = target_recall, recall_threshold(p, y, target_recall)
    if yellow_recall is not None:
        t_yellow = recall_threshold(p, y, yellow_recall)
        cal["yellow_recall"], cal["t_yellow"] = yellow_recall, min(t_yellow, list_threshold(cal))
    return cal


def list_threshold(cal: dict[str, Any], list_factor: float = 1.0) -> float:
    """Threshold for the list (green): the recall-target threshold if set, else ``t * list_factor``."""
    return cal["t_recall"] if cal.get("t_recall") is not None else cal["t"] * list_factor


def yellow_threshold(cal: dict[str, Any], list_factor: float = 1.0) -> float:
    """Lower bound of yellow: from ``yellow_recall`` if set, else half the list threshold."""
    t_list = list_threshold(cal, list_factor)
    t_yellow = cal.get("t_yellow")
    return t_yellow if t_yellow is not None and t_yellow < t_list else t_list / 2


def relevance(record: dict[str, Any], cal: dict[str, Any]) -> float | None:
    """Relevance value 0..1, or ``None`` when the judgement was not scored (other area or failed call)."""
    scores = features(record)
    return None if scores is None else round(predict(cal, scores, record["area"]), 4)


def display_score(
    p: float | None, t_list: float, green: float = 60.0, yellow: float = 40.0, t_yellow: float | None = None
) -> float:
    """Relevance value on the project's 0-100 traffic-light scale (``configs/triage.json``).

    Piecewise linear and monotone, so the ranking is the one Apertus and the
    calibration gave: the list threshold lands on ``green``, the lower bound of
    yellow (``t_yellow``, default half the list threshold) on ``yellow``. That keeps
    the existing thresholds of config, exports and UI valid; the raw value stays in
    ``metadata["relevance"]["p"]``.
    """
    if p is None:
        return 0.0
    half = t_yellow if t_yellow is not None and 0 < t_yellow < t_list else t_list / 2
    if p > t_list:
        return round(green + (100.0 - green) * (p - t_list) / (1.0 - t_list), 2)
    if p > half:
        return min(round(yellow + (green - yellow) * (p - half) / (t_list - half), 2), green - 0.01)
    return min(round(yellow * p / half, 2), yellow - 0.01)


def ampel_for(p: float | None, cal: dict[str, Any], list_factor: float = 1.0) -> str:
    """Green = on the list, yellow = band just below (worth a look), red = not relevant or not scored."""
    if p is None:
        return AMPEL_RED
    t, t_yellow = list_threshold(cal, list_factor), yellow_threshold(cal, list_factor)
    return AMPEL_GREEN if p > t else AMPEL_YELLOW if p > t_yellow else AMPEL_RED
