"""Evaluation & system architecture loader for GET /api/evaluation (#71).

Reads real CSV/JSON metrics from ``results/tables/`` and the model register
tables from ``docs/MODELS.md`` using stdlib only. Never invents fallback
numbers when files are missing.
"""

from __future__ import annotations

import csv
import json
import re
from http import HTTPStatus
from pathlib import Path
from typing import Any

# Curated evaluation tables shown in the «Auswertung» tab, in display order.
_CURATED_TABLES: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "gesamtvergleich_relevanz.csv",
        "Gesamtbewertung der Relevanz-Pipeline",
        "Comparaison globale du pipeline de pertinence",
        "Vergleich der zweistufigen Triage (Embedding-Vorfilter + Apertus v1.5) gegen die Suva-Referenzauswahl.",
        "Comparaison du triage en deux étapes (préfiltre vectoriel + Apertus v1.5) par rapport à la sélection de référence de la Suva.",
    ),
    (
        "pitch_varianten.csv",
        "Modellvarianten im Vergleich (8B / 70B / Thinking)",
        "Comparaison des variantes de modèles (8B / 70B / Thinking)",
        "Trefferqualität, Listenlänge und benötigte Apertus-Aufrufe über zwei Referenzquartale.",
        "Qualité de détection, longueur de liste et appels Apertus nécessaires sur deux trimestres de référence.",
    ),
    (
        "relevance_eval_thresholds.csv",
        "Schwellenwert- und Recall-Auswertung (70B)",
        "Évaluation des seuils et du rappel (70B)",
        "Recall und F2-Score bei verschiedenen Listenfaktoren inkl. Konfidenzintervallen.",
        "Rappel et score F2 selon différents facteurs de liste, avec intervalles de confiance.",
    ),
    (
        "relevance_methods_q1_2026.csv",
        "Methodenvergleich Q1 2026",
        "Comparaison des méthodes T1 2026",
        "Vergleich reiner Ähnlichkeit, kombinierter Bewertung und Prompt-Varianten auf Q1 2026.",
        "Comparaison entre similarité seule, évaluation combinée et variantes de prompt sur le T1 2026.",
    ),
    (
        "thinking_vs_normal_relevanz.csv",
        "Reasoning-Modus (Thinking) vs. Standard (70B)",
        "Mode raisonnement (Thinking) vs standard (70B)",
        "Vergleich von apertus-v1-5-70b und apertus-v1-5-70b-thinking.",
        "Comparaison entre apertus-v1-5-70b et apertus-v1-5-70b-thinking.",
    ),
    (
        "crawl_report.csv",
        "Gecrawlte Urteile pro Monat (entscheidsuche.ch)",
        "Arrêts collectés par mois (entscheidsuche.ch)",
        "Monatliche Abdeckung nach Verfahrenssprache (DE, FR, IT).",
        "Couverture mensuelle par langue de procédure (DE, FR, IT).",
    ),
    (
        "vectordb_report.csv",
        "Lokaler Vektor-Index (Multilingual-E5-small)",
        "Index vectoriel local (Multilingual-E5-small)",
        "Abdeckung der Quartale zwischen Rohdaten, Chunks und Suva-Referenzurteilen.",
        "Couverture trimestrielle entre données brutes, segments et arrêts de référence Suva.",
    ),
)

_CALIBRATION_FILENAME = "relevance_calibration_q2_2026.json"
_SEPARATOR_RE = re.compile(r"^[\s|:\-]+$")


def _read_csv_table(path: Path, max_rows: int = 100) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            rows_raw = [row for row in reader if any(cell.strip() for cell in row)]
    except (OSError, csv.Error, UnicodeDecodeError):
        return None
    if len(rows_raw) < 2:
        return None
    columns = [c.strip() for c in rows_raw[0]]
    data_rows = [[c.strip() for c in row[: len(columns)]] for row in rows_raw[1:]]
    for row in data_rows:
        while len(row) < len(columns):
            row.append("")
    return {
        "id": path.stem,
        "filename": path.name,
        "columns": columns,
        "rows": data_rows[:max_rows],
        "totalRows": len(data_rows),
    }


def _read_calibration(tables_dir: Path) -> dict[str, Any] | None:
    path = tables_dir / _CALIBRATION_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _split_md_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    inner = stripped.strip("|")
    return [cell.strip().strip("`") for cell in inner.split("|")]


def _is_placeholder_row(cells: list[str]) -> bool:
    if len(cells) < 2:
        return True
    name = cells[1].strip()
    if not name or (name.startswith("<") and name.endswith(">")):
        return True
    return False


def _parse_models_section(lines: list[str]) -> dict[str, Any]:
    columns: list[str] = []
    rows: list[list[str]] = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table:
                break
            continue
        if _SEPARATOR_RE.match(stripped):
            continue
        cells = _split_md_row(stripped)
        if not cells:
            continue
        if not in_table:
            columns = cells
            in_table = True
            continue
        if _is_placeholder_row(cells):
            continue
        row = cells[: len(columns)]
        while len(row) < len(columns):
            row.append("")
        rows.append(row)
    return {"columns": columns, "rows": rows}


def _load_models_register(models_path: Path) -> dict[str, dict[str, Any]]:
    empty = {"runtime": {"columns": [], "rows": []}, "development": {"columns": [], "rows": []}}
    if not models_path.is_file():
        return empty
    try:
        text = models_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return empty

    sections: dict[str, list[str]] = {"runtime": [], "development": []}
    current: str | None = None
    for line in text.splitlines():
        heading = line.strip()
        if heading.startswith("## "):
            lower = heading.lower()
            if "1." in lower or "laufzeit" in lower:
                current = "runtime"
            elif "2." in lower or "entwicklung" in lower:
                current = "development"
            else:
                current = None
            continue
        if current is not None:
            sections[current].append(line)

    return {
        "runtime": _parse_models_section(sections["runtime"]),
        "development": _parse_models_section(sections["development"]),
    }


def load_evaluation(
    tables_dir: Path | str = Path("results/tables"),
    models_path: Path | str = Path("docs/MODELS.md"),
) -> dict[str, Any]:
    """Load real evaluation CSV/JSON tables and ``docs/MODELS.md`` sections."""
    t_dir = Path(tables_dir)
    m_path = Path(models_path)

    tables: list[dict[str, Any]] = []
    for filename, title_de, title_fr, desc_de, desc_fr in _CURATED_TABLES:
        parsed = _read_csv_table(t_dir / filename)
        if parsed is None:
            continue
        parsed.update({
            "titleDe": title_de,
            "titleFr": title_fr,
            "descriptionDe": desc_de,
            "descriptionFr": desc_fr,
        })
        tables.append(parsed)

    models = _load_models_register(m_path)
    calibration = _read_calibration(t_dir)
    available = bool(tables or models["runtime"]["rows"] or models["development"]["rows"] or calibration)
    return {
        "available": available,
        "tables": tables,
        "calibration": calibration,
        "models": models,
    }


def evaluation_response(
    tables_dir: Path | str = Path("results/tables"),
    models_path: Path | str = Path("docs/MODELS.md"),
) -> tuple[HTTPStatus, dict[str, Any]]:
    """HTTP response tuple for ``GET /api/evaluation``."""
    return HTTPStatus.OK, load_evaluation(tables_dir, models_path)
