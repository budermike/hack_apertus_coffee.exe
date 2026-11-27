# Summaries-DB – Vorgehen

Owner: Mike (DB mit Abi)

**Ziel:** Summaries liegen in einer zentralen, durchsuchbaren SQLite-DB statt nur im JSON/Speicher.
**Fertig wenn:** Neustart der App zeigt alle bisherigen Summaries – ohne neuen LLM-Aufruf.

## Rahmenbedingungen

- `sqlite3` ist Standardbibliothek → **keine neue Dependency**.
- Pfad zur DB per Env-Variable konfigurierbar (z. B. `SUMMARY_DB_PATH`), Default unter `data/processed/`, nicht ins Git (öffentliches Repo).
- Alle DB-Zugriffe in **einem** Modul (z. B. `summary/store.py`), Tests in `tests/test_store.py` – ohne Netzwerk, LLM gemockt.
- Importname: `pyproject.toml` bildet `src/hack_apertus_coffee.exe` bereits auf `hack_apertus_coffee_exe` ab (`pip install -e .`). Wie in `feat/crawl`: `summary/__init__.py` anlegen und `"hack_apertus_coffee_exe.summary"` in `packages` eintragen → Import `from hack_apertus_coffee_exe.summary import store`. **Kein Umbenennen des Ordners** (würde mit `feat/crawl` kollidieren).

## Schema-Entwurf (Diskussionsgrundlage)

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

## Offene Fragen fürs Rubberducking

- ~~Statuswechsel~~ → entschieden: `approved` = neue Zeile.
- ~~Zusammengelegte Verfahren~~ → entschieden: Zuordnungstabelle `case_numbers`.
- **`edited`:** Neue Zeile mit `model = "human"` oder Original-Modell behalten + Feld `edited_by`?
- **Coherence-Check:** Läuft er vor dem Speichern (Wert sofort gesetzt) oder später (Wert nachträglich per `UPDATE`)?
- **Metadaten zum Urteil** (Datum, Abteilung, URL, `text_sha256`): eigene Tabelle `cases`, oder bleibt das im Crawl-JSON?
- **Gleichzeitigkeit:** Schreiben Batch-Job und UI gleichzeitig? → WAL-Modus (`PRAGMA journal_mode=WAL`).
- **Human-Summaries von Suva:** auch in diese Tabelle (für Vergleich/Eval), oder getrennt halten?
