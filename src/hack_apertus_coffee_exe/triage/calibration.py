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
    list_factor: float = 1.0,
) -> dict[str, Any]:
    """Fit weights and threshold on scored judgements whose Suva decision is known.

    ``t`` is the F2 threshold. With ``target_recall`` / ``yellow_recall`` the list
    threshold ``t_recall`` and the lower bound of yellow ``t_yellow`` come from the
    share of known Suva judgements that must lie above them (#75). ``basis`` records
    why each threshold sits where it does (see ``describe``).
    """
    scored = [r for r in records if features(r)]
    rows = [(features(r), r["area"], int(bool(set(r["case_ids"]) & selected_ids))) for r in scored]
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
    ids = [r["case_id"] for r in scored]
    t_list, t_yellow = list_threshold(cal, list_factor), yellow_threshold(cal, list_factor)
    cal["basis"] = {
        "list": {
            **({"rule": "recall_target", "target": target_recall} if cal.get("t_recall") is not None
               else {"rule": "f2_factor", "t_f2": cal["t"], "factor": list_factor}),
            **_coverage(p, y, ids, t_list),
        },
        "yellow": {
            **({"rule": "recall_target", "target": yellow_recall} if cal.get("t_yellow") is not None and cal["t_yellow"] < t_list
               else {"rule": "half_list"}),
            **_coverage(p, y, ids, t_yellow),
        },
    }
    return cal


def _coverage(p: Sequence[float], y: Sequence[int], ids: Sequence[str], t: float) -> dict[str, Any]:
    """What a threshold does on the calibration data: known Suva cases above it, and the cases at its edge."""
    known = sorted(((float(v), cid) for v, label, cid in zip(p, y, ids) if label), reverse=True)
    kept = [(v, cid) for v, cid in known if v > t]
    missed = [(v, cid) for v, cid in known if v <= t]
    case = lambda row: {"case_id": row[1], "p": round(row[0], 4)} if row else None  # noqa: E731
    return {
        "threshold": round(float(t), 6),
        "known": len(known),
        "known_kept": len(kept),
        "candidates": len(p),
        "candidates_kept": int(sum(float(v) > t for v in p)),
        "lowest_kept": case(kept[-1] if kept else None),
        "highest_missed": case(missed[0] if missed else None),
    }


_TEXT = {
    "de": {
        "list_recall": "Grün ab {score}: Die Schwelle ist aus der Suva-Auswahl abgeleitet, nicht von Hand gesetzt. "
        "In {periods} hat Suva {known} der bewerteten Urteile ausgewählt. Ziel: {target} davon auf der Liste. "
        "Die Schwelle liegt knapp unter dem am tiefsten bewerteten Urteil, das auf der Liste bleibt ({edge}); so stehen {kept} von {known} auf der Liste, "
        "bei {cand_kept} von {cand} Kandidaten.{ties}",
        "list_f2": "Grün ab {score}: F2-optimale Schwelle {t_f2} mal Faktor {factor} (von Hand gesetzt). "
        "In {periods} stünden damit {kept} von {known} der von Suva ausgewählten Urteile auf der Liste, bei {cand_kept} von {cand} Kandidaten.",
        "yellow_recall": "Gelb ab {score}: Darüber liegen {kept} von {known} der von Suva ausgewählten Urteile (Ziel: {target}); "
        "die Grenze liegt knapp unter dem am tiefsten bewerteten ({edge}).",
        "yellow_half": "Gelb ab {score}: halbe Listenschwelle (feste Regel); darüber liegen {kept} von {known} der von Suva ausgewählten Urteile.",
        "ties": " Mehr als das Ziel, weil gleich bewertete Urteile gemeinsam über der Schwelle liegen.",
        "and": " und ",
        "labelled": "den Quartalen mit bekannter Suva-Auswahl",
    },
    "fr": {
        "list_recall": "Vert dès {score} : le seuil découle de la sélection de la Suva, il n'est pas fixé à la main. "
        "En {periods}, la Suva a retenu {known} des arrêts évalués. Objectif : {target} d'entre eux sur la liste. "
        "Le seuil se situe juste sous l'arrêt le moins bien noté qui reste sur la liste ({edge}) ; ainsi {kept} sur {known} figurent sur la liste, "
        "pour {cand_kept} candidats sur {cand}.{ties}",
        "list_f2": "Vert dès {score} : seuil F2 optimal {t_f2} multiplié par le facteur {factor} (fixé à la main). "
        "En {periods}, {kept} des {known} arrêts retenus par la Suva figureraient ainsi sur la liste, pour {cand_kept} candidats sur {cand}.",
        "yellow_recall": "Jaune dès {score} : {kept} des {known} arrêts retenus par la Suva se situent au-dessus (objectif : {target}) ; "
        "la limite se situe juste sous le moins bien noté ({edge}).",
        "yellow_half": "Jaune dès {score} : moitié du seuil de la liste (règle fixe) ; {kept} des {known} arrêts retenus par la Suva se situent au-dessus.",
        "ties": " Davantage que l'objectif, car des arrêts de même valeur se situent ensemble au-dessus du seuil.",
        "and": " et ",
        "labelled": "les trimestres dont la sélection Suva est connue",
    },
}


def describe(cal: dict[str, Any], lang: str = "de", green: float = 60.0, yellow: float = 40.0) -> list[str]:
    """Plain-language reasons for the list threshold and the lower bound of yellow, from ``cal["basis"]``.

    Empty when the calibration has no ``basis`` (written before the explanation existed).
    """
    basis = cal.get("basis")
    if not isinstance(basis, dict):
        return []
    text = _TEXT.get(lang, _TEXT["de"])
    periods = text["and"].join(cal.get("periods") or []) or text["labelled"]
    pct = lambda v: f"{v:.0%}".replace("%", " %")  # noqa: E731
    edge = lambda c: f"{c['case_id']}, {c['p']:.4f}" if c else "–"  # noqa: E731
    lst, ylw = basis["list"], basis["yellow"]
    common = lambda b: {"kept": b["known_kept"], "known": b["known"], "cand_kept": b["candidates_kept"], "cand": b["candidates"]}  # noqa: E731
    if lst["rule"] == "recall_target":
        ties = text["ties"] if lst["known_kept"] > math.ceil(lst["target"] * lst["known"] - 1e-9) else ""
        first = text["list_recall"].format(
            score=f"{green:g}", periods=periods, target=pct(lst["target"]), edge=edge(lst["lowest_kept"]), ties=ties, **common(lst)
        )
    else:
        first = text["list_f2"].format(score=f"{green:g}", periods=periods, t_f2=lst["t_f2"], factor=lst["factor"], **common(lst))
    if ylw["rule"] == "recall_target":
        second = text["yellow_recall"].format(score=f"{yellow:g}", target=pct(ylw["target"]), edge=edge(ylw["lowest_kept"]), **common(ylw))
    else:
        second = text["yellow_half"].format(score=f"{yellow:g}", **common(ylw))
    return [first, second]


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
    return ampel_between(p, list_threshold(cal, list_factor), yellow_threshold(cal, list_factor))


def ampel_between(p: float | None, t_list: float, t_yellow: float | None = None) -> str:
    """Traffic light for ``p`` from the two thresholds; without ``t_yellow`` yellow starts at half the list threshold."""
    if p is None:
        return AMPEL_RED
    t_yellow = t_yellow if t_yellow is not None and 0 < t_yellow < t_list else t_list / 2
    return AMPEL_GREEN if p > t_list else AMPEL_YELLOW if p > t_yellow else AMPEL_RED
