"""Hard time-range filter ensuring only in-range judgements are embedded.

The period is always explicit (``--period 2026-Q2`` or ``--since/--until``);
there is no rolling "last N months" default, so runs are reproducible.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Sequence

_MONTHS: dict[str, int] = {
    # German
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
    # French
    "janvier": 1,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,  # French and Italian
    "décembre": 12,
    "decembre": 12,
    # Italian
    "gennaio": 1,
    "febbraio": 2,
    "marzo": 3,
    "aprile": 4,
    "maggio": 5,
    "giugno": 6,
    "luglio": 7,
    "agosto": 8,
    "settembre": 9,
    "ottobre": 10,
    "dicembre": 12,
}

# decision_date first: the shared schema (issue #3) filters on it until Suva
# says which date applies; publication_date is usually null.
_DATE_METADATA_KEYS = (
    "decision_date",
    "date",
    "datum",
    "publication_date",
    "entscheiddatum",
    "date_decision",
    "urteil_datum",
)
_URL_METADATA_KEYS = ("url", "link", "bger_url", "source_url")

_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DOT_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_SLASH_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_AZA_URL_DATE_RE = re.compile(r"aza://(\d{2})-(\d{2})-(\d{4})-")
_NAMED_MONTH_RE = re.compile(
    r"\b(\d{1,2})(?:\.|er)?\s+([A-Za-zäöüÄÖÜéèêàâîïôùûç]+)\s+(\d{4})\b"
)


class OutOfDateRangeError(ValueError):
    """Raised when a judgement has no valid date or falls outside the allowed window."""


_QUARTER_RE = re.compile(r"(\d{4})-?Q([1-4])")
_MONTH_PERIOD_RE = re.compile(r"(\d{4})-(\d{2})")
_YEAR_PERIOD_RE = re.compile(r"(\d{4})")


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


@dataclass(frozen=True)
class DateRange:
    """Inclusive decision-date window ``[since, until]``."""

    since: date
    until: date

    def __post_init__(self) -> None:
        if self.since > self.until:
            raise ValueError(f"Invalid date range: {self.since} > {self.until}")

    @classmethod
    def from_period(cls, period: str) -> "DateRange":
        """Parse ``2026-Q2``, ``2026-06`` or ``2026`` into an inclusive range."""
        raw = period.strip().upper()
        if match := _QUARTER_RE.fullmatch(raw):
            year, quarter = int(match.group(1)), int(match.group(2))
            return cls(since=date(year, 3 * quarter - 2, 1), until=_month_end(year, 3 * quarter))
        if match := _MONTH_PERIOD_RE.fullmatch(raw):
            year, month = int(match.group(1)), int(match.group(2))
            if not 1 <= month <= 12:
                raise ValueError(f"Invalid month in period {period!r}")
            return cls(since=date(year, month, 1), until=_month_end(year, month))
        if match := _YEAR_PERIOD_RE.fullmatch(raw):
            year = int(match.group(1))
            return cls(since=date(year, 1, 1), until=date(year, 12, 31))
        raise ValueError(f"Unrecognised period {period!r}; use e.g. 2026-Q2, 2026-06 or 2026")

    def label(self) -> str:
        """File-name label: ``q2_2026`` for a full quarter, else ``<since>_<until>``."""
        year = self.since.year
        for quarter in range(1, 5):
            if DateRange.from_period(f"{year}-Q{quarter}") == self:
                return f"q{quarter}_{year}"
        return f"{self.since.isoformat()}_{self.until.isoformat()}"

    def contains(self, value: date) -> bool:
        return self.since <= value <= self.until


@dataclass(frozen=True)
class SkippedItem:
    """Record of an item rejected by the hard date-range filter."""

    case_id: str
    parsed_date: str | None
    reason: str


def parse_date_string(raw: str) -> date | None:
    """Parse common ISO, Swiss dot, slash, aza://, or DE/FR/IT named-month dates."""
    s = raw.strip()
    if not s:
        return None

    for pattern, order in (
        (_ISO_DATE_RE, "ymd"),
        (_AZA_URL_DATE_RE, "dmy"),
        (_DOT_DATE_RE, "dmy"),
        (_SLASH_DATE_RE, "dmy"),
    ):
        match = pattern.search(s)
        if match:
            a, b, c = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
            year, month, day = (a, b, c) if order == "ymd" else (c, b, a)
            try:
                return date(year, month, day)
            except ValueError:
                pass

    named_match = _NAMED_MONTH_RE.search(s)
    if named_match:
        day = int(named_match.group(1))
        month_name = named_match.group(2).lower()
        year = int(named_match.group(3))
        month = _MONTHS.get(month_name)
        if month:
            try:
                return date(year, month, day)
            except ValueError:
                pass
    return None


def extract_judgement_date(text: str, metadata: dict[str, Any] | None = None) -> date | None:
    """Resolve judgement date from metadata, bger.ch URL, or judgement header text."""
    meta = metadata or {}
    for key in _DATE_METADATA_KEYS:
        val = meta.get(key)
        if isinstance(val, datetime):
            return val.date()
        if isinstance(val, date):
            return val
        if isinstance(val, str):
            parsed = parse_date_string(val)
            if parsed is not None:
                return parsed

    for key in _URL_METADATA_KEYS:
        val = meta.get(key)
        if isinstance(val, str):
            match = _AZA_URL_DATE_RE.search(val)
            if match:
                try:
                    return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
                except ValueError:
                    pass

    return parse_date_string(text[:1500])


def check_date_in_range(
    text: str,
    metadata: dict[str, Any] | None,
    date_range: DateRange,
) -> date:
    """Validate that an item has a parseable date inside ``date_range`` or raise."""
    parsed = extract_judgement_date(text, metadata)
    if parsed is None:
        raise OutOfDateRangeError("Missing or unparseable judgement date.")
    if not date_range.contains(parsed):
        raise OutOfDateRangeError(
            f"Judgement date {parsed.isoformat()} is outside allowed range "
            f"[{date_range.since.isoformat()}, {date_range.until.isoformat()}]."
        )
    return parsed


def filter_items_in_date_range(
    items: Sequence[Any],
    date_range: DateRange,
) -> tuple[list[Any], list[SkippedItem]]:
    """Return ``(accepted_items, skipped_items)`` strictly enforcing ``date_range``."""
    accepted: list[Any] = []
    skipped: list[SkippedItem] = []
    for item in items:
        parsed = extract_judgement_date(item.text, item.metadata)
        if parsed is None:
            skipped.append(
                SkippedItem(
                    case_id=item.case_id,
                    parsed_date=None,
                    reason="Missing or unparseable judgement date",
                )
            )
            continue
        if not date_range.contains(parsed):
            skipped.append(
                SkippedItem(
                    case_id=item.case_id,
                    parsed_date=parsed.isoformat(),
                    reason=(
                        f"Outside range [{date_range.since.isoformat()}, "
                        f"{date_range.until.isoformat()}]"
                    ),
                )
            )
            continue
        accepted.append(item)
    return accepted, skipped
