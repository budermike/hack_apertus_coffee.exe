# Datenbank-Schema: Summaries

Zentrale, durchsuchbare Wissensbasis für alle erzeugten und bearbeiteten Urteils-Summaries (SQLite).
Batch- und On-demand-Generierung schreiben hier hinein; die UI liest von hier und generiert nicht neu, wenn bereits eine Summary existiert.

- Engine: SQLite (`sqlite3`, Python-Standardbibliothek)
- Pfad: konfigurierbar über `SUMMARY_DB_PATH` (Default unter `data/processed/`, nicht im Git)
- Code: `src/hack_apertus_coffee.exe/summary/store.py` (Import: `hack_apertus_coffee_exe.summary.store`)

## Tabelle `summaries`

Eine Zeile = eine Version einer Summary in einer Sprache. Zeilen werden **nie geändert oder gelöscht**; jede Änderung erzeugt eine neue Zeile (Verlauf).

```sql
CREATE TABLE IF NOT EXISTS summaries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id        TEXT    NOT NULL,
    language       TEXT    NOT NULL CHECK (language IN ('de', 'fr')),
    version        INTEGER NOT NULL,
    text           TEXT    NOT NULL,
    model          TEXT    NOT NULL,
    prompt_version TEXT,
    created_at     TEXT    NOT NULL,
    status         TEXT    NOT NULL CHECK (status IN ('generated', 'edited', 'approved')),
    coherence_ok   INTEGER CHECK (coherence_ok IN (0, 1)),
    UNIQUE (case_id, language, version)
);
```

| Spalte | Bedeutung |
|---|---|
| `id` | Technischer Schlüssel |
| `case_id` | Haupt-Verfahrensnummer des Urteils, z. B. `8C_129/2025` (siehe `case_numbers`) |
| `language` | Sprache der **Summary** (`de` / `fr`), nicht die Sprache des Urteils |
| `version` | Laufende Nummer pro `(case_id, language)`, beginnend bei 1 |
| `text` | Text der Summary |
| `model` | Herkunft des Texts: `apertus-v1-5-8b`, `apertus-v1-5-70b` usw. bei `generated`; `human` bei `edited`. `approved` übernimmt den Wert der freigegebenen Version |
| `prompt_version` | Prompt-Fassung, mit der generiert wurde (z. B. `v2`); für Evaluation und gezieltes Neu-Generieren. `NULL` bei bearbeiteten Texten |
| `created_at` | Zeitpunkt der Erstellung, ISO-8601 in UTC (`2026-11-27T14:16:14+00:00`) |
| `status` | `generated` (vom Modell), `edited` (von Jurist:in geändert), `approved` (freigegeben) |
| `coherence_ok` | Ergebnis des Coherence-Checks: `1` = bestanden, `0` = Problem gefunden, `NULL` = nicht geprüft |

### Versionierung

- **Aktuelle Version** = Zeile mit der höchsten `version` pro `(case_id, language)`.
- Ablauf Generieren → Bearbeiten → Freigeben ergibt drei Zeilen:

| version | status | model | prompt_version | text |
|---|---|---|---|---|
| 1 | `generated` | `apertus-v1-5-70b` | `v2` | Modell-Output |
| 2 | `edited` | `human` | `NULL` | bearbeiteter Text |
| 3 | `approved` | `human` | `NULL` | Kopie von v2 |

- Wird eine Summary ohne Bearbeitung freigegeben, kopiert `approved` die `generated`-Zeile (inkl. `model` und `prompt_version`).

- Neu-Generieren (z. B. mit neuem Prompt) erzeugt ebenfalls eine neue Version mit Status `generated`.

## Tabelle `case_numbers`

Ein Urteil kann mehrere Verfahrensnummern haben (vereinigte Verfahren, z. B. `1D_1/2025, 1D_2/2025`). Summaries liegen nur **einmal** unter der Haupt-`case_id` (= erste Nummer); jede Nummer verweist darauf.

```sql
CREATE TABLE IF NOT EXISTS case_numbers (
    case_number TEXT PRIMARY KEY,
    case_id     TEXT NOT NULL
);
```

| case_number | case_id |
|---|---|
| `1D_1/2025` | `1D_1/2025` |
| `1D_2/2025` | `1D_1/2025` |

Jede Nummer, auch die Hauptnummer selbst, steht in dieser Tabelle. Eine Suche nach beliebiger Nummer wird über `case_numbers` auf die `case_id` aufgelöst.

## Index

```sql
CREATE INDEX IF NOT EXISTS idx_summaries_case_lang
    ON summaries (case_id, language, version);
```

## Offene Punkte

- **Coherence-Check:** Was genau geprüft wird, ist noch nicht festgelegt. Bis dahin bleibt `coherence_ok` = `NULL`. Mögliche Erweiterung: `coherence_notes TEXT` für die Begründung in der UI.
- **Volltextsuche:** optional per SQLite FTS5 über `text` und `case_id`.
