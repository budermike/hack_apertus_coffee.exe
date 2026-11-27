"""SQLite store for summaries (issue #26).

One row per (case, language, version). A new generation or a lawyer's edit adds
a row instead of overwriting, so the history is kept. The UI reads the latest
row per case and language and only calls Apertus when none exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

DEFAULT_DB = Path("data/processed/summary/summaries.sqlite")
STATUSES = ("generated", "edited", "approved")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS summaries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id        TEXT NOT NULL,
    language       TEXT NOT NULL CHECK (language IN ('de', 'fr')),
    title          TEXT NOT NULL,
    text           TEXT NOT NULL,
    model          TEXT,
    prompt_version TEXT,
    created_at     TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('generated', 'edited', 'approved')),
    coherence_ok   INTEGER,
    issues         TEXT,
    facts          TEXT,
    examples       TEXT,
    parent_id      INTEGER REFERENCES summaries(id)
);
CREATE INDEX IF NOT EXISTS idx_summaries_case ON summaries (case_id, language, id);
"""


@dataclass(frozen=True)
class StoredSummary:
    id: int
    case_id: str
    language: str
    title: str
    text: str
    model: str | None
    prompt_version: str | None
    created_at: str
    status: str
    coherence_ok: bool | None
    issues: list[dict[str, Any]]
    facts: dict[str, Any]
    examples: list[str]
    parent_id: int | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(row: sqlite3.Row) -> StoredSummary:
    return StoredSummary(
        id=row["id"],
        case_id=row["case_id"],
        language=row["language"],
        title=row["title"],
        text=row["text"],
        model=row["model"],
        prompt_version=row["prompt_version"],
        created_at=row["created_at"],
        status=row["status"],
        coherence_ok=None if row["coherence_ok"] is None else bool(row["coherence_ok"]),
        issues=json.loads(row["issues"] or "[]"),
        facts=json.loads(row["facts"] or "{}"),
        examples=json.loads(row["examples"] or "[]"),
        parent_id=row["parent_id"],
    )


class SummaryStore:
    def __init__(self, path: str | Path = DEFAULT_DB) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def add(
        self,
        case_id: str,
        language: str,
        title: str,
        text: str,
        *,
        model: str | None = None,
        prompt_version: str | None = None,
        status: str = "generated",
        coherence_ok: bool | None = None,
        issues: list[dict[str, Any]] | None = None,
        facts: dict[str, Any] | None = None,
        examples: list[str] | None = None,
        parent_id: int | None = None,
    ) -> int:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        cur = self._conn.execute(
            "INSERT INTO summaries (case_id, language, title, text, model, prompt_version, created_at,"
            " status, coherence_ok, issues, facts, examples, parent_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                case_id, language, title, text, model, prompt_version, _now(), status,
                None if coherence_ok is None else int(coherence_ok),
                json.dumps(issues or [], ensure_ascii=False),
                json.dumps(facts or {}, ensure_ascii=False),
                json.dumps(examples or [], ensure_ascii=False),
                parent_id,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def add_result(self, result: Any) -> dict[str, int]:
        """Store both languages of a ``SummaryResult``; returns the new row ids."""
        ids = {}
        for lang in ("de", "fr"):
            issues = [i for i in result.check.to_dicts() if lang in i["language"].split("/")]
            ids[lang] = self.add(
                result.case_id, lang,
                result.title_de if lang == "de" else result.title_fr,
                result.text(lang),
                model=result.model, prompt_version=result.prompt_version,
                coherence_ok=result.coherence_ok, issues=issues,
                facts=result.facts, examples=result.examples,
            )
        return ids

    def latest(self, case_id: str, language: str) -> StoredSummary | None:
        row = self._conn.execute(
            "SELECT * FROM summaries WHERE case_id = ? AND language = ? ORDER BY id DESC LIMIT 1",
            (case_id, language),
        ).fetchone()
        return _row(row) if row else None

    def history(self, case_id: str, language: str | None = None) -> list[StoredSummary]:
        sql, args = "SELECT * FROM summaries WHERE case_id = ?", [case_id]
        if language:
            sql += " AND language = ?"
            args.append(language)
        return [_row(r) for r in self._conn.execute(sql + " ORDER BY id", args)]

    def has(self, case_id: str) -> bool:
        return all(self.latest(case_id, lang) for lang in ("de", "fr"))

    def edit(self, summary_id: int, new_text: str) -> int:
        """A lawyer's edit: new row with status 'edited', linked to the old one."""
        old = self.get(summary_id)
        return self.add(
            old.case_id, old.language, old.title, new_text,
            model=old.model, prompt_version=old.prompt_version, status="edited",
            coherence_ok=None, issues=[], facts=old.facts, examples=old.examples, parent_id=old.id,
        )

    def approve(self, summary_id: int) -> None:
        self._conn.execute("UPDATE summaries SET status = 'approved' WHERE id = ?", (summary_id,))
        self._conn.commit()

    def get(self, summary_id: int) -> StoredSummary:
        row = self._conn.execute("SELECT * FROM summaries WHERE id = ?", (summary_id,)).fetchone()
        if not row:
            raise KeyError(summary_id)
        return _row(row)

    def case_ids(self) -> list[str]:
        return [r[0] for r in self._conn.execute("SELECT DISTINCT case_id FROM summaries ORDER BY case_id")]
