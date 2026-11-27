"""Lawyer feedback (issue #28): store ratings and turn them into score adjustments.

The system learns from feedback without training:

- direct effect: the rated judgement's score moves by a configured offset;
  ``++`` always ends up green and ``--`` always red (configurable);
- indirect effect: ``+``/``++`` judgements act as extra positive references and
  ``-``/``--`` judgements as negative references; every other judgement gains or
  loses ``weight * strength * similarity * 100`` points, using its strongest
  positive and strongest negative match.

Ratings live in a small JSON file until the summary DB (#26) and slider UI
(#27) exist; both can call :class:`FeedbackStore` directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .config import AMPEL_GREEN, AMPEL_RED, RATINGS, FeedbackConfig, TriageConfig
from .embeddings import max_chunk_similarity


@dataclass(frozen=True)
class Feedback:
    case_id: str
    rating: str
    comment: str = ""
    updated_at: str = ""


class FeedbackStore:
    """JSON-file store: ``{"version": 1, "ratings": {case_id: {...}}}``."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._ratings: dict[str, Feedback] = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for case_id, entry in (data.get("ratings") or {}).items():
                self._ratings[case_id] = Feedback(
                    case_id=case_id,
                    rating=entry["rating"],
                    comment=entry.get("comment", ""),
                    updated_at=entry.get("updated_at", ""),
                )

    def all(self) -> dict[str, Feedback]:
        return dict(self._ratings)

    def get(self, case_id: str) -> Feedback | None:
        return self._ratings.get(case_id)

    def set(self, case_id: str, rating: str, comment: str = "") -> Feedback:
        if rating not in RATINGS:
            raise ValueError(f"Rating must be one of {RATINGS}, got {rating!r}")
        fb = Feedback(
            case_id=case_id,
            rating=rating,
            comment=comment,
            updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self._ratings[case_id] = fb
        self.save()
        return fb

    def clear(self, case_id: str) -> bool:
        removed = self._ratings.pop(case_id, None) is not None
        if removed:
            self.save()
        return removed

    def save(self) -> None:
        payload = {
            "version": 1,
            "ratings": {
                cid: {"rating": fb.rating, "comment": fb.comment, "updated_at": fb.updated_at}
                for cid, fb in sorted(self._ratings.items())
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)


@dataclass(frozen=True)
class FeedbackAdjustment:
    score: float
    delta: float
    direct: float
    indirect_positive: float
    indirect_negative: float
    own_rating: str | None
    positive_source: str | None
    negative_source: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rating": self.own_rating,
            "delta": round(self.delta, 2),
            "direct": round(self.direct, 2),
            "from_positive": round(self.indirect_positive, 2),
            "positive_source": self.positive_source,
            "from_negative": round(-self.indirect_negative, 2) or 0.0,
            "negative_source": self.negative_source,
        }


def adjust_score(
    case_id: str,
    base_score: float,
    own_vectors: np.ndarray | None,
    ratings: Mapping[str, Feedback],
    reference_vectors: Mapping[str, np.ndarray],
    config: TriageConfig,
) -> FeedbackAdjustment:
    """Apply indirect, then direct feedback effects to one judgement's score."""
    fb_cfg: FeedbackConfig = config.feedback

    best_pos, pos_src = 0.0, None
    best_neg, neg_src = 0.0, None
    if own_vectors is not None:
        for other_id, fb in ratings.items():
            if other_id == case_id:
                continue
            strength = float(fb_cfg.rating_strength.get(fb.rating, 0.0))
            vecs = reference_vectors.get(other_id)
            if strength == 0.0 or vecs is None:
                continue
            weighted = abs(strength) * max(0.0, max_chunk_similarity(own_vectors, vecs))
            if strength > 0 and weighted > best_pos:
                best_pos, pos_src = weighted, other_id
            elif strength < 0 and weighted > best_neg:
                best_neg, neg_src = weighted, other_id

    indirect_pos = fb_cfg.positive_weight * best_pos * 100.0
    indirect_neg = fb_cfg.negative_weight * best_neg * 100.0

    own = ratings.get(case_id)
    direct = float(fb_cfg.direct_offsets.get(own.rating, 0.0)) if own else 0.0

    score = min(100.0, max(0.0, base_score + indirect_pos - indirect_neg + direct))
    if own and fb_cfg.strong_ratings_force_ampel:
        if own.rating == "++" and config.ampel_for(score) != AMPEL_GREEN:
            score = config.green_threshold
        elif own.rating == "--" and config.ampel_for(score) != AMPEL_RED:
            score = max(0.0, config.yellow_threshold - 0.01)
    score = round(score, 2)

    return FeedbackAdjustment(
        score=score,
        delta=score - base_score,
        direct=direct,
        indirect_positive=indirect_pos,
        indirect_negative=indirect_neg,
        own_rating=own.rating if own else None,
        positive_source=pos_src,
        negative_source=neg_src,
    )
