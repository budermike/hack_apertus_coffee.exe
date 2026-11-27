"""Triage configuration: score weights, traffic-light thresholds, exports, feedback.

Values are read from ``configs/triage.json`` when it exists; anything missing
falls back to the defaults below. Nothing in the pipeline hard-codes these
numbers (issues #12 and #28).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import json
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("configs/triage.json")

AMPEL_GREEN = "gruen"
AMPEL_YELLOW = "gelb"
AMPEL_RED = "rot"

RATINGS = ("--", "-", "0", "+", "++")


@dataclass(frozen=True)
class FeedbackConfig:
    """How lawyer feedback (issue #28) changes scores."""

    # Direct effect on the rated judgement itself (points on the 0-100 scale).
    direct_offsets: dict[str, float] = field(
        default_factory=lambda: {"--": -20.0, "-": -10.0, "0": 0.0, "+": 10.0, "++": 20.0}
    )
    # "++" always ends up at least green, "--" always ends up red.
    strong_ratings_force_ampel: bool = True
    # Indirect effect on other judgements: points = weight * strength * similarity * 100.
    positive_weight: float = 0.3
    negative_weight: float = 0.3
    # Strength of each rating as a positive (+) or negative (-) reference.
    rating_strength: dict[str, float] = field(
        default_factory=lambda: {"--": -1.0, "-": -0.5, "0": 0.0, "+": 0.5, "++": 1.0}
    )
    store_path: str = "data/processed/triage/feedback.json"


@dataclass(frozen=True)
class TriageConfig:
    """All tunable triage settings in one place."""

    # Which stored score drives rank and traffic light: "final_score" or "cosine_score".
    rank_by: str = "final_score"
    # final_score = llm_weight * apertus_score + (1 - llm_weight) * cosine_score
    llm_weight: float = 0.75
    top_k: int = 3
    # Placeholder thresholds on the 0-100 scale of `rank_by`; replace with the
    # F2-best value from the eval sweep (issue #21). Green >= green, yellow >= yellow.
    green_threshold: float = 60.0
    yellow_threshold: float = 40.0
    # Length of the Suva-format recall list (Suva grades the top 30 hits).
    recall_top_n: int = 30
    export_dir: str = "results"
    cache_dir: str = "data/processed/triage"
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)

    def __post_init__(self) -> None:
        if self.rank_by not in ("final_score", "cosine_score"):
            raise ValueError(f"rank_by must be 'final_score' or 'cosine_score', got {self.rank_by!r}")
        if not 0.0 <= self.llm_weight <= 1.0:
            raise ValueError("llm_weight must be between 0 and 1")
        if self.yellow_threshold > self.green_threshold:
            raise ValueError("yellow_threshold must not be above green_threshold")

    def ampel_for(self, score: float) -> str:
        if score >= self.green_threshold:
            return AMPEL_GREEN
        if score >= self.yellow_threshold:
            return AMPEL_YELLOW
        return AMPEL_RED

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _known(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    names = {f.name for f in fields(cls)}
    unknown = set(data) - names
    if unknown:
        raise ValueError(f"Unknown {cls.__name__} keys: {sorted(unknown)}")
    return dict(data)


def load_config(path: str | Path | None = None) -> TriageConfig:
    """Load ``configs/triage.json`` (or ``path``); missing file means all defaults."""
    cfg_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        if path is not None:
            raise FileNotFoundError(f"Triage config not found: {cfg_path}")
        return TriageConfig()
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    data.pop("_comment", None)
    feedback_data = data.pop("feedback", {}) or {}
    feedback_data.pop("_comment", None)
    feedback = FeedbackConfig(**_known(FeedbackConfig, feedback_data))
    return TriageConfig(**_known(TriageConfig, data), feedback=feedback)
