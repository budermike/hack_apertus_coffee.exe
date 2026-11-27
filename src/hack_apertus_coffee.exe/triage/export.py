"""Deliverable exports for issue #33: ranking CSV and Suva-format recall list."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Sequence

from .pipeline import TriageResult

RANKING_COLUMNS = ("Rang", "Geschaeftsnummer", "Score", "Ampel", "Begruendung")


def write_ranking_csv(results: Sequence[TriageResult], path: str | Path) -> Path:
    """``Rang, Geschaeftsnummer, Score, Ampel, Begruendung``, sorted by rank."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(results, key=lambda r: r.rank if r.rank is not None else 10**9)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(RANKING_COLUMNS)
        for res in ordered:
            score = res.rank_score if res.rank_score is not None else res.final_score
            writer.writerow([res.rank, res.case_id, f"{score:.2f}", res.ampel, res.relevance_reason])
    return out


def format_recall_list(results: Sequence[TriageResult], top_n: int) -> str:
    """Suva format: ``8C_691/2024, 9C_612/2025, ...`` for the top ``top_n`` ranks."""
    ordered = sorted(results, key=lambda r: r.rank if r.rank is not None else 10**9)
    return ", ".join(res.case_id for res in ordered[: max(0, top_n)])


def write_recall_list(results: Sequence[TriageResult], path: str | Path, top_n: int) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(format_recall_list(results, top_n) + "\n", encoding="utf-8")
    return out


def export_period(
    results: Sequence[TriageResult], export_dir: str | Path, label: str, top_n: int
) -> tuple[Path, Path]:
    """Write ``ranking_<label>.csv`` and ``recall_<label>.txt`` into ``export_dir``."""
    base = Path(export_dir)
    return (
        write_ranking_csv(results, base / f"ranking_{label}.csv"),
        write_recall_list(results, base / f"recall_{label}.txt", top_n),
    )
