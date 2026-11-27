"""SQLite knowledge base for court-judgement summaries.

All database access for summaries goes through this module. Schema and rules
are documented in ``docs/SCHEMA.md`` (section "Summaries-Datenbank").

Key rules:
- Summary rows are never updated or deleted; every change is a new version.
- A summary is generated at most once per (case_id, language).
- Editing always stores ``model = "human"`` and ``prompt_version = NULL``.
- Only an edited version can be approved; a generated summary needs no approval.
- Any case number (also secondary numbers of joined cases) resolves to the
  main ``case_id`` via the ``case_numbers`` table.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DB_PATH_ENV = "SUMMARY_DB_PATH"
DEFAULT_DB_PATH = Path("data/processed/summaries.db")

HUMAN = "human"
LANGUAGES = ("de", "fr")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cases (
    case_id          TEXT PRIMARY KEY,
    decision_date    TEXT NOT NULL,
    publication_date TEXT,
    url              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS summaries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id        TEXT    NOT NULL REFERENCES cases (case_id),
    language       TEXT    NOT NULL CHECK (language IN ('de', 'fr')),
    version        INTEGER NOT NULL,
    text           TEXT    NOT NULL,
    model          TEXT    NOT NULL,
    prompt_version TEXT,
    created_at     TEXT    NOT NULL,
    status         TEXT    NOT NULL CHECK (status IN ('generated', 'edited', 'approved')),
    coherence_ok   INTEGER CHECK (coherence_ok IN (0, 1)),
    UNIQUE (case_id, language, version),
    CHECK (
        (status = 'generated' AND model <> 'human')
        OR (status IN ('edited', 'approved') AND model = 'human' AND prompt_version IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS case_numbers (
    case_number TEXT PRIMARY KEY,
    case_id     TEXT NOT NULL REFERENCES cases (case_id)
);

CREATE INDEX IF NOT EXISTS idx_summaries_case_lang
    ON summaries (case_id, language, version);
"""


class SummaryExistsError(Exception):
    """Raised when a summary for (case_id, language) has already been generated."""


class InvalidTransitionError(Exception):
    """Raised for a status change the rules do not allow (e.g. approve without edit)."""


@dataclass(frozen=True)
class Case:
    case_id: str
    decision_date: str
    publication_date: str | None
    url: str


@dataclass(frozen=True)
class Summary:
    id: int
    case_id: str
    language: str
    version: int
    text: str
    model: str
    prompt_version: str | None
    created_at: str
    status: str
    coherence_ok: bool | None


@dataclass(frozen=True)
class GeneratedSummary:
    """What the summary pipeline returns: model output plus coherence-check result."""

    text: str
    model: str
    prompt_version: str | None
    coherence_ok: bool | None


# --- Connection --------------------------------------------------------------


def db_path_from_env() -> Path:
    """DB path from ``SUMMARY_DB_PATH``, falling back to ``data/processed/summaries.db``."""
    return Path(os.environ.get(DB_PATH_ENV) or DEFAULT_DB_PATH)


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open the database (creating file, folder and tables if needed).

    ``path=":memory:"`` gives a throw-away in-memory DB.
    """
    target = db_path_from_env() if path is None else path
    if str(target) != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(target) != ":memory:":
        # Lets the UI read while a batch job writes.
        conn.execute("PRAGMA journal_mode = WAL")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they do not exist (idempotent)."""
    conn.executescript(SCHEMA_SQL)


# --- Cases -------------------------------------------------------------------


def save_case(
    conn: sqlite3.Connection,
    case_id: str,
    decision_date: str,
    url: str,
    publication_date: str | None = None,
    case_numbers: Iterable[str] = (),
) -> Case:
    """Insert or update a case and register all its case numbers.

    ``case_id`` is the main number; it is always registered as a case number
    itself. Raises ``ValueError`` if a number already belongs to another case.
    """
    numbers = [case_id, *(n for n in case_numbers if n != case_id)]
    with conn:
        conn.execute(
            """
            INSERT INTO cases (case_id, decision_date, publication_date, url)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (case_id) DO UPDATE SET
                decision_date    = excluded.decision_date,
                publication_date = excluded.publication_date,
                url              = excluded.url
            """,
            (case_id, decision_date, publication_date, url),
        )
        for number in numbers:
            existing = resolve_case_number(conn, number)
            if existing is not None and existing != case_id:
                raise ValueError(
                    f"Case number {number!r} already belongs to case {existing!r}"
                )
            conn.execute(
                "INSERT OR IGNORE INTO case_numbers (case_number, case_id) VALUES (?, ?)",
                (number, case_id),
            )
    return Case(case_id, decision_date, publication_date, url)


def save_case_from_crawl(conn: sqlite3.Connection, record: dict) -> Case:
    """Store a case from a crawl JSON record (see docs/SCHEMA.md, part 1)."""
    return save_case(
        conn,
        case_id=record["case_id"],
        decision_date=record["decision_date"],
        url=record["url"],
        publication_date=record.get("publication_date"),
        case_numbers=record.get("case_ids") or (),
    )


def resolve_case_number(conn: sqlite3.Connection, case_number: str) -> str | None:
    """Return the main case_id for any case number, or None if unknown."""
    row = conn.execute(
        "SELECT case_id FROM case_numbers WHERE case_number = ?", (case_number,)
    ).fetchone()
    return row["case_id"] if row else None


def get_case(conn: sqlite3.Connection, case_number: str) -> Case | None:
    """Return the case (with source URL) for any of its case numbers."""
    case_id = resolve_case_number(conn, case_number)
    if case_id is None:
        return None
    row = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    return Case(**dict(row))


# --- Summaries: write ----------------------------------------------------------


def save_generated(
    conn: sqlite3.Connection,
    case_number: str,
    language: str,
    generated: GeneratedSummary,
) -> Summary:
    """Store a model-generated summary as version 1.

    Raises ``SummaryExistsError`` if any summary already exists for this
    case and language (summaries are generated at most once).
    """
    case_id = _require_case(conn, case_number)
    if generated.model == HUMAN:
        raise ValueError("Generated summaries must name the model, not 'human'")
    with conn:
        if _latest(conn, case_id, language) is not None:
            raise SummaryExistsError(f"Summary for {case_id} ({language}) already exists")
        return _insert(
            conn,
            case_id=case_id,
            language=language,
            version=1,
            text=generated.text,
            model=generated.model,
            prompt_version=generated.prompt_version,
            status="generated",
            coherence_ok=generated.coherence_ok,
        )


def save_edit(conn: sqlite3.Connection, case_number: str, language: str, text: str) -> Summary:
    """Store a human edit as a new version (status 'edited', model 'human')."""
    case_id = _require_case(conn, case_number)
    with conn:
        latest = _latest(conn, case_id, language)
        if latest is None:
            raise InvalidTransitionError(f"No summary to edit for {case_id} ({language})")
        return _insert(
            conn,
            case_id=case_id,
            language=language,
            version=latest.version + 1,
            text=text,
            model=HUMAN,
            prompt_version=None,
            status="edited",
            coherence_ok=None,
        )


def approve(conn: sqlite3.Connection, case_number: str, language: str) -> Summary:
    """Approve the pending edit: new version that copies the latest 'edited' text."""
    case_id = _require_case(conn, case_number)
    with conn:
        latest = _latest(conn, case_id, language)
        if latest is None or latest.status != "edited":
            raise InvalidTransitionError(
                f"Only an edited summary can be approved ({case_id}, {language})"
            )
        return _insert(
            conn,
            case_id=case_id,
            language=language,
            version=latest.version + 1,
            text=latest.text,
            model=HUMAN,
            prompt_version=None,
            status="approved",
            coherence_ok=None,
        )


def get_or_generate(
    conn: sqlite3.Connection,
    case_number: str,
    language: str,
    generate: Callable[[], GeneratedSummary],
) -> Summary:
    """Return the displayed summary; call ``generate`` only if none exists yet.

    This is the entry point for both batch and on-demand generation.
    """
    case_id = _require_case(conn, case_number)
    existing = get_display(conn, case_id, language)
    if existing is not None:
        return existing
    return save_generated(conn, case_id, language, generate())


# --- Summaries: read -----------------------------------------------------------


def get_display(conn: sqlite3.Connection, case_number: str, language: str) -> Summary | None:
    """Summary shown in the UI: newest 'approved', otherwise the 'generated' one."""
    case_id = resolve_case_number(conn, case_number)
    if case_id is None:
        return None
    row = conn.execute(
        """
        SELECT * FROM summaries
        WHERE case_id = ? AND language = ? AND status IN ('approved', 'generated')
        ORDER BY (status = 'approved') DESC, version DESC
        LIMIT 1
        """,
        (case_id, language),
    ).fetchone()
    return _to_summary(row) if row else None


def get_pending_edit(conn: sqlite3.Connection, case_number: str, language: str) -> Summary | None:
    """The newest version if it is an edit waiting for approval, else None."""
    case_id = resolve_case_number(conn, case_number)
    if case_id is None:
        return None
    latest = _latest(conn, case_id, language)
    return latest if latest is not None and latest.status == "edited" else None


def get_history(conn: sqlite3.Connection, case_number: str, language: str) -> list[Summary]:
    """All versions of one summary, oldest first."""
    case_id = resolve_case_number(conn, case_number)
    if case_id is None:
        return []
    rows = conn.execute(
        "SELECT * FROM summaries WHERE case_id = ? AND language = ? ORDER BY version",
        (case_id, language),
    ).fetchall()
    return [_to_summary(r) for r in rows]


def list_display(conn: sqlite3.Connection) -> list[Summary]:
    """Displayed summary for every (case_id, language), e.g. for the UI overview."""
    pairs = conn.execute(
        "SELECT DISTINCT case_id, language FROM summaries ORDER BY case_id, language"
    ).fetchall()
    result = []
    for pair in pairs:
        summary = get_display(conn, pair["case_id"], pair["language"])
        if summary is not None:
            result.append(summary)
    return result


# --- Internal helpers ----------------------------------------------------------


def _require_case(conn: sqlite3.Connection, case_number: str) -> str:
    case_id = resolve_case_number(conn, case_number)
    if case_id is None:
        raise LookupError(f"Unknown case number {case_number!r}; call save_case first")
    return case_id


def _latest(conn: sqlite3.Connection, case_id: str, language: str) -> Summary | None:
    row = conn.execute(
        """
        SELECT * FROM summaries
        WHERE case_id = ? AND language = ?
        ORDER BY version DESC
        LIMIT 1
        """,
        (case_id, language),
    ).fetchone()
    return _to_summary(row) if row else None


def _insert(
    conn: sqlite3.Connection,
    *,
    case_id: str,
    language: str,
    version: int,
    text: str,
    model: str,
    prompt_version: str | None,
    status: str,
    coherence_ok: bool | None,
) -> Summary:
    if language not in LANGUAGES:
        raise ValueError(f"language must be one of {LANGUAGES}, got {language!r}")
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    coherence = None if coherence_ok is None else int(coherence_ok)
    cursor = conn.execute(
        """
        INSERT INTO summaries
            (case_id, language, version, text, model, prompt_version,
             created_at, status, coherence_ok)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (case_id, language, version, text, model, prompt_version, created_at, status, coherence),
    )
    return Summary(
        id=cursor.lastrowid,
        case_id=case_id,
        language=language,
        version=version,
        text=text,
        model=model,
        prompt_version=prompt_version,
        created_at=created_at,
        status=status,
        coherence_ok=coherence_ok,
    )


def _to_summary(row: sqlite3.Row) -> Summary:
    data = dict(row)
    if data["coherence_ok"] is not None:
        data["coherence_ok"] = bool(data["coherence_ok"])
    return Summary(**data)
