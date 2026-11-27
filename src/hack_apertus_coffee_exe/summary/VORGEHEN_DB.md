# Summaries-DB – Vorgehen

Owner: Mike (DB mit Abi)

**Ziel:** Summaries liegen in einer zentralen, durchsuchbaren SQLite-DB statt nur im JSON/Speicher.
**Fertig wenn:** Neustart der App zeigt alle bisherigen Summaries – ohne neuen LLM-Aufruf.

## Rahmenbedingungen

- `sqlite3` ist Standardbibliothek → **keine neue Dependency**.
- Pfad zur DB per Env-Variable konfigurierbar (z. B. `SUMMARY_DB_PATH`), Default unter `data/processed/`, nicht ins Git (öffentliches Repo).
- Alle DB-Zugriffe in **einem** Modul (z. B. `summary/store.py`), Tests in `tests/test_store.py` – ohne Netzwerk, LLM gemockt.
- Ort: DB-Code liegt in `src/hack_apertus_coffee_exe/summary/` (`store.py`). `summary/__init__.py` anlegen und `"hack_apertus_coffee_exe.summary"` in `packages` (`pyproject.toml`) eintragen → Import `from hack_apertus_coffee_exe.summary import store`.

## Schema-Entwurf (Diskussionsgrundlage)

> Verbindliche Fassung: [`docs/SCHEMA.md`](../../../docs/SCHEMA.md). Bei Änderungen dort pflegen.

```sql
CREATE TABLE summaries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id        TEXT    NOT NULL,          -- z. B. "8C_129/2025"
    language       TEXT    NOT NULL CHECK (language IN ('de','fr')),  -- Sprache der Summary, nicht des Urteils
    version        INTEGER NOT NULL,          -- 1, 2, 3 … pro (case_id, language)
    text           TEXT    NOT NULL,
    model          TEXT    NOT NULL,          -- apertus-v1-5-8b / -70b / "human"
    prompt_version TEXT,
    created_at     TEXT    NOT NULL,          -- ISO-8601 UTC
    status         TEXT    NOT NULL CHECK (status IN ('generated','edited','approved')),
    coherence_ok   INTEGER,                   -- 1 / 0 / NULL (= noch nicht geprüft)
    UNIQUE (case_id, language, version)
);
```

```sql
-- Alle Verfahrensnummern eines Urteils (z. B. "1D_1/2025, 1D_2/2025")
-- zeigen auf eine Haupt-case_id (= erste Nummer). Auch die Hauptnummer selbst steht hier.
CREATE TABLE case_numbers (
    case_number TEXT PRIMARY KEY,   -- jede einzelne Nummer
    case_id     TEXT NOT NULL       -- Haupt-ID, unter der die Summaries liegen
);
```

- **Verlauf:** Nie `UPDATE` – jede Änderung ist eine neue Zeile mit `version + 1`.
  Entschieden: Generieren → Bearbeiten → Freigeben ergibt 3 Zeilen (`generated` v1, `edited` v2, `approved` v3 als Kopie von v2).
- **Aktuelle Version** = höchste `version` pro `(case_id, language)`.
- **Mehrere Nummern:** Summaries nur einmal unter der Haupt-`case_id` speichern (keine Duplikate, sonst laufen Bearbeitungen/Freigaben auseinander). Suche nach irgendeiner Nummer → über `case_numbers` zur Haupt-`case_id` auflösen.
- Schema zusätzlich in `docs/SCHEMA.md` dokumentieren.

## Schritte

> **Stand: Issue #26 umgesetzt.** Alle Schritte erledigt, ausser FTS5 (einfache Suche reicht vorerst).
>
> | Schritt | Umsetzung |
> |---|---|
> | 1 Schema | `docs/SCHEMA.md`, Teil 2 |
> | 2 Store | `summary/store.py` |
> | 3 Tests | `tests/test_summary_store.py` (inkl. Neustart-Test), `tests/test_summary_api.py` |
> | 4 Batch + On-demand | `summary/run.py` → `store.get_or_generate_languages` (kein `--force` mehr) |
> | 5 UI-Backend | `summary/api.py` (stdlib `http.server`), Endpunkte siehe Modul-Docstring |
> | 6 Suche | `store.search` (Teilstring), `GET /api/knowledge-base?q=` |
>
> Starten:
> ```
> python -m hack_apertus_coffee_exe.summary.run batch --triage results/triage.json --input data/raw
> python -m hack_apertus_coffee_exe.summary.api --input data/raw --static src/presentation
> ```
> Der ursprüngliche Plan unten bleibt zur Nachvollziehbarkeit stehen (Funktionsnamen weichen leicht ab).

1. **Schema festlegen** (Rubberducking, offene Fragen unten) → `docs/SCHEMA.md`.
2. **`store.py`** mit kleiner API:
   - `init_db(path)` – Tabelle anlegen, falls nicht vorhanden
   - `save_summary(case_id, language, text, model, prompt_version, status, coherence_ok) -> version`
   - `get_latest(case_id, language) -> Summary | None`
   - `get_history(case_id, language) -> list[Summary]`
   - `list_latest()` – alles für die UI-Übersicht
   - `get_or_generate(case_id, language, generate_fn)` – liest aus DB, ruft LLM nur, wenn nichts da ist
3. **Tests** (`tmp_path`-DB): Speichern/Lesen, Versionierung, Verlauf bleibt erhalten, und der „Neustart-Test“: Verbindung schliessen, neu öffnen, `generate_fn`-Mock darf **nicht** aufgerufen werden.
4. **Batch- und On-demand-Generierung** auf `save_summary` / `get_or_generate` umstellen.
5. **UI** liest über `list_latest` / `get_latest`; „Neu generieren“ / „Bearbeiten“ erzeugt neue Version.
6. **Suche** (falls Zeit): SQLite FTS5 über `text` + `case_id`.

## Zusatz: Triage-Feedback (Entwurf, **zurückgestellt**)

> Ampel-System ist noch nicht definiert → wird später geklärt. Zuerst wird nur die Tabelle `summaries` (+ `case_numbers`) umgesetzt.

Anforderung: Tabelle `feedback` mit `case_id, chosen_ampel, old_ampel, old_score, new_score, comment, user, created_at`.
In der Triage: `manual_override = true`, `model_score` bleibt erhalten. Re-Scoring durch das Modell überschreibt manuelle Werte nicht.

```sql
CREATE TABLE triage (                 -- aktueller Triage-Stand pro Urteil
    case_id         TEXT PRIMARY KEY,
    model_score     REAL NOT NULL,    -- 0–100, nur das Modell schreibt hier
    model_ampel     TEXT NOT NULL,    -- rot / gelb / gruen
    reason          TEXT,
    model           TEXT,
    scored_at       TEXT NOT NULL,
    manual_override INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE feedback (               -- append-only Log jeder manuellen Korrektur
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id      TEXT NOT NULL,
    chosen_ampel TEXT NOT NULL,
    old_ampel    TEXT,                -- angezeigter Wert vor der Korrektur
    old_score    REAL,
    new_score    REAL,
    comment      TEXT,
    user         TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
```

- Angezeigter Wert = letzte `feedback`-Zeile, falls `manual_override = 1`, sonst `model_*`.
- Entschieden: Re-Scoring **darf** `model_score`/`model_ampel` überschreiben (kein Verlauf), ändert aber nie `manual_override` oder `feedback`.
- Entschieden: Ampel und Score hängen zusammen. Nutzer:in wählt die Ampel, `new_score` wird daraus abgeleitet:
  - gleiche Ampel wie angezeigt → nichts passiert (keine `feedback`-Zeile, kein Override)
  - andere Ampel → `new_score` = Durchschnitt der aktuell angezeigten Scores aller Urteile, die gerade diese Ampel haben (zum Zeitpunkt der Korrektur berechnet und in `feedback.new_score` festgehalten)
  - offen: Fallback, wenn noch kein Urteil diese Ampel hat; woher die Ampel eines Urteils kommt (gespeichert oder aus Grenzwerten)
- Feldnamen an Triage-Branch angleichen (`relevance_score`, Ampel-Werte aus `feat/crawl`-Schema).

## Offene Fragen fürs Rubberducking

- ~~Statuswechsel~~ → entschieden: `approved` = neue Zeile.
- ~~Zusammengelegte Verfahren~~ → entschieden: Zuordnungstabelle `case_numbers`.
- ~~`edited`~~ → entschieden: `model = "human"`, `prompt_version = NULL`.
- **Ampel:** Gespeichert (`model_ampel` vom Modell) oder aus Score-Grenzwerten abgeleitet? Fallback für leere Farbe? Wer speichert Triage-Ergebnisse in die DB?
- ~~Coherence-Check~~ → entschieden: läuft beim Generieren (Urteil vs. Summary), Wert wird beim Einfügen gesetzt, nie nachträglich geändert.
- **Metadaten zum Urteil** (Datum, Abteilung, URL, `text_sha256`): eigene Tabelle `cases`, oder bleibt das im Crawl-JSON?
- **Gleichzeitigkeit:** Schreiben Batch-Job und UI gleichzeitig? → WAL-Modus (`PRAGMA journal_mode=WAL`).
- **Human-Summaries von Suva:** auch in diese Tabelle (für Vergleich/Eval), oder getrennt halten?
