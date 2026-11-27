"""Load Suva lawyer reference summaries from judgements/*.html."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import re


@dataclass(frozen=True)
class ReferenceCase:
    """A human-summarized Federal Court judgement used as a positive Suva precedent."""

    case_id: str
    chamber: str
    title: str
    text_de: str
    text_fr: str
    bger_url: str
    author: str
    source_file: str

    def text_for_language(self, language: str | None = None) -> str:
        """Return language-matched summary text, or DE+FR combined if unknown."""
        lang = (language or "").strip().lower()
        if lang.startswith("de") and self.text_de:
            return self.text_de
        if lang.startswith("fr") and self.text_fr:
            return self.text_fr
        parts = [p for p in (self.text_de, self.text_fr) if p]
        return "\n\n".join(parts)


class _SummaryHTMLParser(HTMLParser):
    """Minimal stdlib HTML parser for Suva Export_*.html summary files."""

    def __init__(self) -> None:
        super().__init__()
        self._current_tag: str | None = None
        self._current_section: str | None = None
        self._paragraph_parts: list[str] = []
        self.title_parts: list[str] = []
        self.h1_parts: list[str] = []
        self.sections: dict[str, list[str]] = {}
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._current_tag = tag
        if tag == "p":
            self._paragraph_parts = []
        elif tag == "a":
            for key, value in attrs:
                if key == "href" and value:
                    self.links.append(value.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and self._current_section:
            paragraph = " ".join("".join(self._paragraph_parts).split())
            if paragraph:
                self.sections.setdefault(self._current_section, []).append(paragraph)
            self._paragraph_parts = []
        if tag in ("title", "h1", "h2"):
            self._current_tag = None

    def handle_data(self, data: str) -> None:
        if self._current_tag == "title":
            self.title_parts.append(data)
        elif self._current_tag == "h1":
            self.h1_parts.append(data)
        elif self._current_tag == "h2":
            heading = " ".join(data.split())
            if heading:
                self._current_section = heading
        elif self._current_section:
            self._paragraph_parts.append(data)


_CASE_ID_IN_TITLE_RE = re.compile(r"\b(\d+[A-Z]_\d+/\d{4})\b")
_CASE_ID_IN_FILENAME_RE = re.compile(r"Export_(\d+[A-Z])_(\d+)_(\d{4})")


def _extract_case_id(title: str, path: Path) -> str:
    match = _CASE_ID_IN_TITLE_RE.search(title)
    if match:
        return match.group(1)
    fname_match = _CASE_ID_IN_FILENAME_RE.search(path.name)
    if fname_match:
        return f"{fname_match.group(1)}_{fname_match.group(2)}/{fname_match.group(3)}"
    return path.stem


def _extract_chamber(case_id: str) -> str:
    return case_id.split("_", 1)[0] if "_" in case_id else ""


def parse_summary_html(path: Path) -> ReferenceCase:
    """Parse a single Suva Export_*.html summary file."""
    content = path.read_text(encoding="utf-8")
    parser = _SummaryHTMLParser()
    parser.feed(content)
    parser.close()

    h1_title = " ".join("".join(parser.h1_parts).split())
    head_title = " ".join("".join(parser.title_parts).split())
    title = h1_title or head_title or path.stem

    case_id = _extract_case_id(title, path)
    chamber = _extract_chamber(case_id)
    text_de = "\n\n".join(parser.sections.get("Text DE", []))
    text_fr = "\n\n".join(parser.sections.get("Text FR", []))
    link_text = " ".join(parser.sections.get("Link zum Urteil", []))
    bger_url = parser.links[0] if parser.links else link_text
    author = " ".join(parser.sections.get("Autor", []))

    return ReferenceCase(
        case_id=case_id,
        chamber=chamber,
        title=title,
        text_de=text_de,
        text_fr=text_fr,
        bger_url=bger_url,
        author=author,
        source_file=str(path),
    )


def load_reference_corpus(judgements_dir: str | Path = "judgements") -> list[ReferenceCase]:
    """Load all Export_*.html reference summaries from ``judgements_dir``."""
    base = Path(judgements_dir)
    if not base.exists():
        raise FileNotFoundError(f"Judgements directory not found: {base}")
    html_files = sorted(base.glob("Export_*.html"))
    if not html_files:
        raise ValueError(f"No Export_*.html summary files found in {base}")
    return [parse_summary_html(path) for path in html_files]
