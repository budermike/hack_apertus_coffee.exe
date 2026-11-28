"""Central settings (#48), standard library only.

- ``configs/*.json``: domain settings and folders. ``configs/paths.json`` holds the
  folders shared by several stages and the default period; ``crawl.json`` and
  ``triage.json`` keep the settings of their stage.
- ``.env``: secrets and environment (``APERTUS_API_KEY``, ``APERTUS_BASE_URL``,
  ``APERTUS_MODEL``, ``EMBEDDING_MODEL``, ``SUMMARY_DB_PATH``, ``HF_TOKEN``), read with
  :func:`env`. Entry points load ``.env`` with python-dotenv as before.

Modules take their default folders from :func:`load_paths` instead of hard-coding them.
Paths stay relative to the project folder, as everywhere else in the code.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PATHS_FILE = "paths.json"


def config_dir() -> Path:
    """``configs/`` of the project (next to ``src/``), or of the current folder."""
    for candidate in (PROJECT_ROOT / "configs", Path.cwd() / "configs"):
        if (candidate / PATHS_FILE).is_file():
            return candidate
    raise FileNotFoundError(f"configs/{PATHS_FILE} not found under {PROJECT_ROOT} or {Path.cwd()}")


@dataclass(frozen=True)
class Paths:
    raw_dir: str  # crawled judgements, one JSON per judgement
    vectordb_dir: str  # Vector-DB (metadata.sqlite3, vectors/, FTS)
    judgements_dir: str  # Suva reference summaries (Export_*.html, list of selected cases)
    vectordb_report: str  # CSV report of the Vector-DB counts per quarter
    default_period_from: str  # ISO date
    default_period_to: str  # ISO date


@lru_cache(maxsize=None)
def load_paths(path: str | Path | None = None) -> Paths:
    """Read ``configs/paths.json`` (or ``path``). Every key is required: no defaults in code."""
    file = Path(path) if path is not None else config_dir() / PATHS_FILE
    data = json.loads(file.read_text(encoding="utf-8"))
    period = data["default_period"]
    return Paths(
        raw_dir=data["raw_dir"],
        vectordb_dir=data["vectordb_dir"],
        judgements_dir=data["judgements_dir"],
        vectordb_report=data["vectordb_report"],
        default_period_from=period["from"],
        default_period_to=period["to"],
    )


def env(name: str, *aliases: str, default: str | None = None) -> str | None:
    """First non-empty value of ``name`` or its aliases in the environment (``.env`` loaded by the caller)."""
    for key in (name, *aliases):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return default
