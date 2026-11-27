"""SQLite knowledge base for court-judgement summaries (issue #26).

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

import json
import os
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH_ENV = "SUMMARY_DB_PATH"
DEFAULT_DB_PATH = Path("data/processed/summaries.db")

HUMAN = "human"
LANGUAGES = ("de", "fr")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cases (
    case_id          TEXT PRIMARY KEY,
    decision_date    TEXT,
    publication_date TEXT,
    url              TEXT
);

CREATE TABLE IF NOT EXISTS summaries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id        TEXT    NOT NULL REFERENCES cases (case_id),
    language       TEXT    NOT NULL CHECK (language IN ('de', 'fr')),
    version        INTEGER NOT NULL,
    title          TEXT,
    text           TEXT    NOT NULL,
    model          TEXT    NOT NULL,
    prompt_version TEXT,
    created_at     TEXT    NOT NULL,
    status         TEXT    NOT NULL CHECK (status IN ('generated', 'edited', 'approved')),
    coherence_ok   INTEGER CHECK (coherence_ok IN (0, 1)),
    issues         TEXT,
    facts          TEXT,
    examples       TEXT,
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
    decision_date: str | None
    publication_date: str | None
    url: str | None


@dataclass(frozen=True)
class Summary:
    id: int
    case_id: str
    language: str
    version: int
    title: str | None
    text: str
    model: str
    prompt_version: str | None
    created_at: str
    status: str
    coherence_ok: bool | None
    issues: list[dict[str, Any]] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    examples: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GeneratedSummary:
    """What the summary pipeline returns for one language.

    ``coherence_ok`` and ``issues`` come from the coherence check (summary
    compared with the judgement); ``facts`` and ``examples`` document how the
    text was produced.
    """

    text: str
    model: str
    prompt_version: str | None
    coherence_ok: bool | None
    title: str | None = None
    issues: Sequence[dict[str, Any]] = ()
    facts: Mapping[str, Any] | None = None
    examples: Sequence[str] = ()


# --- Connection --------------------------------------------------------------


def db_path_from_env() -> Path:
    """DB path from ``SUMMARY_DB_PATH``, falling back to ``data/processed/summaries.db``."""
    return Path(os.environ.get(DB_PATH_ENV) or DEFAULT_DB_PATH)


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open the database (creating file, folder and tables if needed).

    ``path=":memory:"`` gives a throw-away in-memory DB.
    """
    target = db_path_from_env() if path is None else path
    in_memory = str(target) == ":memory:"
    if not in_memory:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not in_memory:
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
    decision_date: str | None,
    url: str | None,
    publication_date: str | None = None,
    case_numbers: Iterable[str] = (),
) -> Case:
    """Insert or update a case and register all its case numbers.

    ``case_id`` is the main number; it is always registered as a case number
    itself. Known values are not overwritten with ``None``. Raises
    ``ValueError`` if a number already belongs to another case.
    """
    numbers = [case_id, *(n for n in case_numbers if n != case_id)]
    with conn:
        conn.execute(
            """
            INSERT INTO cases (case_id, decision_date, publication_date, url)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (case_id) DO UPDATE SET
                decision_date    = COALESCE(excluded.decision_date, decision_date),
                publication_date = COALESCE(excluded.publication_date, publication_date),
                url              = COALESCE(excluded.url, url)
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
    return get_case(conn, case_id)


def save_case_from_crawl(conn: sqlite3.Connection, record: dict) -> Case:
    """Store a case from a crawl JSON record (see docs/SCHEMA.md, part 1)."""
    return save_case(
        conn,
        case_id=record["case_id"],
        decision_date=record.get("decision_date"),
        url=record.get("url"),
        publication_date=record.get("publication_date"),
        case_numbers=record.get("case_ids") or (),
    )


def resolve_case_number(conn: sqlite3.Connection, case_number: str) -> str | None:
    """Return the main case_id for any case number, or None if unknown.

    Accepts the original spelling (``8C_7/2025``) and the normalized one
    used in file names and URLs (``8C_7_2025``).
    """
    candidates = [case_number]
    if "/" not in case_number and "_" in case_number:
        head, _, year = case_number.rpartition("_")
        candidates.append(f"{head}/{year}")
    for candidate in candidates:
        row = conn.execute(
            "SELECT case_id FROM case_numbers WHERE case_number = ?", (candidate,)
        ).fetchone()
        if row:
            return row["case_id"]
    return None


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
            title=generated.title,
            text=generated.text,
            model=generated.model,
            prompt_version=generated.prompt_version,
            status="generated",
            coherence_ok=generated.coherence_ok,
            issues=list(generated.issues),
            facts=dict(generated.facts or {}),
            examples=list(generated.examples),
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
            title=latest.title,
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
            title=latest.title,
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
    """Return the displayed summary; call ``generate`` only if none exists yet."""
    return get_or_generate_languages(
        conn, case_number, lambda: {language: generate()}, languages=(language,)
    )[language]


def get_or_generate_languages(
    conn: sqlite3.Connection,
    case_number: str,
    generate: Callable[[], Mapping[str, GeneratedSummary]],
    languages: Sequence[str] = LANGUAGES,
) -> dict[str, Summary]:
    """Displayed summary per language; ``generate`` runs once if any language is missing.

    Entry point for batch and on-demand generation. ``generate`` returns one
    ``GeneratedSummary`` per language (the pipeline writes DE and FR together);
    only the missing languages are stored, existing ones are never replaced.
    The LLM call happens outside any DB transaction.
    """
    case_id = _require_case(conn, case_number)
    result = {lang: get_display(conn, case_id, lang) for lang in languages}
    missing = [lang for lang, summary in result.items() if summary is None]
    if missing:
        generated = generate()
        for lang in missing:
            result[lang] = save_generated(conn, case_id, lang, generated[lang])
    return result


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


def list_case_ids(conn: sqlite3.Connection) -> list[str]:
    """Main case_ids that have at least one summary."""
    return [r[0] for r in conn.execute("SELECT DISTINCT case_id FROM summaries ORDER BY case_id")]


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


def search(conn: sqlite3.Connection, query: str) -> list[str]:
    """Main case_ids whose case numbers or displayed DE/FR summaries contain all terms.

    Simple case-insensitive substring search, newest summary first.
    """
    terms = query.lower().split()
    texts: dict[str, list[str]] = {}
    newest: dict[str, str] = {}
    for summary in list_display(conn):
        texts.setdefault(summary.case_id, []).extend([summary.title or "", summary.text])
        newest[summary.case_id] = max(newest.get(summary.case_id, ""), summary.created_at)
    for number, case_id in conn.execute("SELECT case_number, case_id FROM case_numbers"):
        if case_id in texts:
            texts[case_id].append(number)
    found = [
        case_id for case_id, parts in texts.items()
        if all(term in " ".join(parts).lower() for term in terms)
    ]
    return sorted(found, key=newest.__getitem__, reverse=True)


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
    title: str | None,
    text: str,
    model: str,
    prompt_version: str | None,
    status: str,
    coherence_ok: bool | None,
    issues: list[dict[str, Any]] | None = None,
    facts: dict[str, Any] | None = None,
    examples: list[str] | None = None,
) -> Summary:
    if language not in LANGUAGES:
        raise ValueError(f"language must be one of {LANGUAGES}, got {language!r}")
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    coherence = None if coherence_ok is None else int(coherence_ok)
    cursor = conn.execute(
        """
        INSERT INTO summaries
            (case_id, language, version, title, text, model, prompt_version,
             created_at, status, coherence_ok, issues, facts, examples)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            case_id, language, version, title, text, model, prompt_version,
            created_at, status, coherence,
            _to_json(issues), _to_json(facts), _to_json(examples),
        ),
    )
    return Summary(
        id=cursor.lastrowid,
        case_id=case_id,
        language=language,
        version=version,
        title=title,
        text=text,
        model=model,
        prompt_version=prompt_version,
        created_at=created_at,
        status=status,
        coherence_ok=coherence_ok,
        issues=issues or [],
        facts=facts or {},
        examples=examples or [],
    )


def _to_json(value: Any) -> str | None:
    return None if not value else json.dumps(value, ensure_ascii=False)


def _to_summary(row: sqlite3.Row) -> Summary:
    data = dict(row)
    if data["coherence_ok"] is not None:
        data["coherence_ok"] = bool(data["coherence_ok"])
    data["issues"] = json.loads(data["issues"] or "[]")
    data["facts"] = json.loads(data["facts"] or "{}")
    data["examples"] = json.loads(data["examples"] or "[]")
    return Summary(**data)
