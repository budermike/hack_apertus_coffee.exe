"""Summary contract (#48): what other modules and the backend call to get a summary.

Reading and editing summaries go through ``summary.store`` (``get_case``,
``get_display`` / ``get_pending_edit`` / ``get_history``, ``save_edit`` / ``approve``,
``search``). Only :func:`summarize` may call the LLM.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

from ..config import load_paths


def find_judgement(case_id: str, conn: sqlite3.Connection | None = None, raw_dir: str | Path | None = None) -> Any:
    """Crawled judgement for a case number in any spelling (``8C_7/2025``, ``8C_7_2025``), or None.

    Joined cases (``1D_2/2025`` belongs to ``1D_1/2025``) are resolved through
    ``case_numbers`` when a connection is given.
    """
    from . import store
    from .judgement import load_judgements, same_case

    folder = Path(raw_dir or load_paths().raw_dir)
    wanted = [case_id]
    if conn is not None:
        slash_form = case_id if "/" in case_id else "/".join(case_id.rsplit("_", 1))  # 8C_7_2025 -> 8C_7/2025
        main = store.resolve_case_number(conn, slash_form)
        if main and main not in wanted:
            wanted.append(main)
    for number in wanted:
        safe_id = number.replace("/", "_")
        for path in sorted(folder.rglob(f"*{safe_id}.json")):
            for judgement in load_judgements(path):
                if same_case(judgement.case_id, number):
                    return judgement
    return None


def summarize(conn: sqlite3.Connection, case_id: str) -> dict[str, Any]:
    """DE and FR summary of one judgement; generated with Apertus only if none is stored yet.

    Wraps ``run.summarize_case`` (store first: an existing summary never triggers a new
    LLM call). Returns the displayed ``store.Summary`` per language (``{"de", "fr"}``).
    Used by the batch for green judgements and by ``POST /api/judgements/{id}/summary``.
    """
    from .run import summarize_case, summarizer_factory

    judgement = find_judgement(case_id, conn)
    if judgement is None:
        raise LookupError(f"judgement {case_id} not found in {load_paths().raw_dir}")
    return summarize_case(judgement, conn, summarizer_factory())
