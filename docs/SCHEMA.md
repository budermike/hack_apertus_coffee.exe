# Datenschema

Dieses Dokument beschreibt, wie Urteile und Summaries gespeichert werden.

| Teil | Format | Ort | Inhalt |
|---|---|---|---|
| [1. Rohdaten](#1-rohdaten-crawl-json) | JSON, eine Datei pro Urteil | `data/raw/<YYYY-MM>/<decision_date>_<safe_case_id>.json` | Gecrawlte Urteile (Volltext + Metadaten), Issue #3 |
| [2. Summaries-DB](#2-summaries-datenbank-sqlite) | SQLite | `SUMMARY_DB_PATH` (Default unter `data/processed/`) | Zentrale, durchsuchbare Wissensbasis aller Summaries |

Datenfluss: Crawler → Rohdaten-JSON → (Triage) → Summary-Pipeline → Summaries-DB → UI

## Gemeinsame Konventionen

- **Geschäftsnummer (`case_id`):** überall in Originalschreibweise, z. B. `1D_1/2025`, für den Abgleich mit Suva.
- **ID-Normalisierung (`/` → `_`):** Nur in Dateinamen, Vector-DB-IDs und UI-URLs wird `/` durch `_` ersetzt (z. B. `1D_1/2025` → `1D_1_2025`, Datei `2026-04-01_1D_1_2025.json`). In den Feldern `case_id` und `case_ids` sowie in der Datenbank bleibt die Originalschreibweise erhalten.
- **Vereinigte Verfahren:** Ein Urteil kann mehrere Geschäftsnummern haben (z. B. `1D_1/2025, 1D_2/2025`). Die erste ist die Haupt-`case_id`; alle stehen in `case_ids` (JSON) bzw. `case_numbers` (DB).
- **Datumsfilter (`decision_date` vs. `publication_date`):** `publication_date` ist aktuell `null`, da weder `entscheidsuche.ch` noch die Ergebnisliste von `search.bger.ch` das Publikationsdatum liefern. **Annahme:** Bis Suva klärt, ob für Q2/2026 das Urteils- oder das Publikationsdatum zählt, wird nach `decision_date` gefiltert.
- **Datumsformate:** Datum als `YYYY-MM-DD`, Zeitstempel als ISO-8601 in UTC (`2026-11-27T14:16:14+00:00`).

---

## 1. Rohdaten (Crawl-JSON)

Ein JSON pro Bundesgerichtsurteil unter `data/raw/<YYYY-MM>/<decision_date>_<safe_case_id>.json`.

- **Kein HTML gespeichert:** `content_html` wird nicht gespeichert, um unnötige Daten zu vermeiden. Die Integritätsprüfung erfolgt über `fetch.text_sha256` auf `text`.

### Felder

| Feld | Typ | Beschreibung |
|---|---|---|
| `id` | `string` | Normalisierte interne ID (`/` → `_`, z. B. `"1D_1_2025"`), direkt nutzbar für Vector-DB und UI-URLs. Hinweis: Bei mehreren Entscheiden zum selben Dossier an verschiedenen Tagen ist `<decision_date>_<id>` eindeutig. |
| `case_id` | `string` | Primäre Geschäftsnummer in Originalschreibweise, z. B. `"1D_1/2025"` (für den Abgleich mit Suva). |
| `case_ids` | `list[string]` | Alle Geschäftsnummern aus dem Urteilskopf (z. B. `["1D_1/2025", "1D_2/2025"]` bei vereinigten Verfahren). |
| `decision_date` | `string` | Urteilsdatum im ISO-Format `YYYY-MM-DD`. |
| `publication_date` | `string \| null` | Publikationsdatum im ISO-Format oder `null` (aktuell `null`, siehe Datumsfilter oben). |
| `department` | `string \| null` | Abteilung des Bundesgerichts (gemäss Issue `#3`), z. B. `"I. Öffentlich-rechtliche Abteilung"`. |
| `division` | `string \| null` | Identisch mit `department` (Alias für Vector-DB & Triage gemäss Issue `#37`). |
| `subject` | `string \| null` | Rechtsgebiet / Gegenstand (bei `bger` aus der Trefferliste, bei `entscheidsuche` aus dem `Gegenstand` / `Objet` / `Oggetto`-Block im Text geparst). |
| `language` | `string \| null` | Urteilssprache: `"de"`, `"fr"`, `"it"` oder `null`. |
| `url` | `string` | Link auf das Originalurteil bei `search.bger.ch`. |
| `text` | `string` | Volltext des Urteils (ein Absatz pro Zeile, ohne HTML). |
| `operator_metadata` | `object` | Unveränderte Metadaten der jeweiligen Quelle (`bger` oder `entscheidsuche`). |
| `fetch` | `object` | Herkunfts- und Integritätsmetadaten, siehe unten. |

### Unterfelder von `fetch`

| Feld | Typ | Beschreibung |
|---|---|---|
| `fetched_at` | `string` | UTC-Zeitstempel des Abrufs (ISO 8601). |
| `fetched_by` | `string` | User-Agent des Crawlers. |
| `source` | `string` | Datenquelle (`"entscheidsuche"` oder `"bger"`). |
| `url` | `string` | Abgerufene Quell-URL. |
| `hash_algorithm` | `string` | Hash-Verfahren (`"sha256"`). |
| `text_sha256` | `string` | SHA-256-Hash über `text` (UTF-8) zur Integritätsprüfung. |

### Ergebnisfelder (Triage & Summary)

Nachgelagerte Pipeline-Schritte ergänzen gemäss Issue `#3` folgende Felder:

| Feld | Typ | Beschreibung |
|---|---|---|
| `score` | `number` | Relevanz-Score für Suva. |
| `ampel` | `string` | Ampel-Einstufung (z. B. `"rot"`, `"gelb"`, `"grün"`). |
| `relevance_reason` | `string` | Begründung der Triage-Einstufung. |
| `similar_cases` | `list[string]` | Ähnliche Fälle (z. B. über Vektorsuche). |
| `summary_de` | `string` | Zusammenfassung auf Deutsch. |
| `summary_fr` | `string` | Zusammenfassung auf Französisch. |

> Summaries werden verbindlich in der [Summaries-DB](#2-summaries-datenbank-sqlite) gespeichert (mit Verlauf und Status).

---

## 2. Summaries-Datenbank (SQLite)

Zentrale, durchsuchbare Wissensbasis für alle erzeugten und bearbeiteten Urteils-Summaries. Batch- und On-demand-Generierung schreiben hier hinein; die UI liest von hier und generiert nicht neu, wenn bereits eine Summary existiert.

### Technik

- Engine: SQLite (`sqlite3`, Python-Standardbibliothek)
- Pfad: konfigurierbar über `SUMMARY_DB_PATH` (Default unter `data/processed/`, nicht im Git)
- Code: `src/hack_apertus_coffee_exe/summary/store.py` (Import: `hack_apertus_coffee_exe.summary.store`)
- Schreibende Stellen: Batch und On-demand (`summary/run.py`) sowie die UI-API (`summary/api.py`), alle über `store.get_or_generate_languages`, `save_edit` und `approve`
- Fremdschlüssel sind nur aktiv mit `PRAGMA foreign_keys = ON` (setzt `store.py` bei jeder Verbindung).

### Tabellen im Überblick

| Tabelle | Eine Zeile pro … | Beziehung |
|---|---|---|
| `cases` | Urteil | – |
| `summaries` | Version einer Summary in einer Sprache | viele Zeilen pro Urteil, verknüpft über `case_id` |
| `case_numbers` | Geschäftsnummer | eine oder mehrere pro Urteil, verknüpft über `case_id` |

### Tabelle `cases`

Verweist auf die Originalquelle, damit das Urteil jederzeit nachgelesen werden kann, z. B. beim manuellen Bearbeiten einer Summary oder wenn nicht neu zusammengefasst wird. Wird aus dem Crawl-JSON befüllt, **bevor** Summaries gespeichert werden.

```sql
CREATE TABLE IF NOT EXISTS cases (
    case_id          TEXT PRIMARY KEY,
    decision_date    TEXT,
    publication_date TEXT,
    url              TEXT
);
```

| Spalte | Bedeutung | Quelle im Crawl-JSON |
|---|---|---|
| `case_id` | Haupt-Geschäftsnummer in Originalschreibweise, z. B. `1D_1/2025` | `case_id` |
| `decision_date` | Urteilsdatum, `YYYY-MM-DD`; `NULL` nur falls im Crawl-Datensatz nicht lesbar | `decision_date` |
| `publication_date` | Publikationsdatum, `YYYY-MM-DD`; aktuell meist `NULL` (siehe Datumsfilter) | `publication_date` |
| `url` | Link auf das Originalurteil bei `search.bger.ch`; `NULL` nur falls im Crawl-Datensatz fehlend | `url` |

Ein erneutes Speichern desselben Urteils überschreibt bekannte Werte nicht mit `NULL`.

### Tabelle `summaries`

Eine Zeile = eine Version einer Summary in einer Sprache. Zeilen werden **nie geändert oder gelöscht**; jede Änderung erzeugt eine neue Zeile (Verlauf).

```sql
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
    -- Modell-Text vs. menschlich bearbeiteter Text
    CHECK (
        (status = 'generated' AND model <> 'human')
        OR (status IN ('edited', 'approved') AND model = 'human' AND prompt_version IS NULL)
    )
);
```

| Spalte | Bedeutung |
|---|---|
| `id` | Technischer Schlüssel |
| `case_id` | Haupt-Geschäftsnummer des Urteils, z. B. `8C_129/2025` (verweist auf `cases`) |
| `language` | Sprache der **Summary** (`de` / `fr`), nicht die Sprache des Urteils |
| `version` | Laufende Nummer pro `(case_id, language)`, beginnend bei 1 |
| `title` | Titel in der Sprache der Summary, z. B. `BGer-Urteil 8C_901/2026 vom 14. Oktober 2026`; bei Bearbeitungen übernommen |
| `text` | Text der Summary |
| `model` | Herkunft des Texts: Apertus-Modell-ID (z. B. `apertus-v1-5-70b`) bei `generated`; immer `human` bei `edited` und `approved` (wird vom Code automatisch gesetzt) |
| `prompt_version` | Prompt-Fassung, mit der generiert wurde (z. B. `v2`); für die Evaluation. `NULL` bei bearbeiteten Texten |
| `created_at` | Zeitpunkt der Erstellung (ISO-8601, UTC) |
| `status` | `generated` (vom Modell, gilt ohne weiteren Schritt als freigegeben), `edited` (von Jurist:in geändert, wartet auf Freigabe), `approved` (bearbeitete Fassung freigegeben) |
| `coherence_ok` | Ergebnis des Coherence-Checks: `1` = bestanden, `0` = Problem gefunden, `NULL` = nicht geprüft (siehe Regeln) |
| `issues` | JSON-Liste der Befunde des Coherence-Checks für diese Sprache (z. B. erfundene Erwägung), damit die UI zeigen kann, was nicht passt; `NULL` bei Bearbeitungen |
| `facts` | JSON-Objekt der extrahierten Fakten (Zwischenschritt der Pipeline); `NULL` bei Bearbeitungen |
| `examples` | JSON-Liste der Suva-Beispiele (Geschäftsnummern), die im Prompt verwendet wurden; `NULL` bei Bearbeitungen |

### Tabelle `case_numbers`

Summaries liegen nur **einmal** unter der Haupt-`case_id` (= erste Nummer); jede Geschäftsnummer verweist darauf. Befüllt aus `case_ids` im Crawl-JSON.

```sql
CREATE TABLE IF NOT EXISTS case_numbers (
    case_number TEXT PRIMARY KEY,
    case_id     TEXT NOT NULL REFERENCES cases (case_id)
);
```

| case_number | case_id |
|---|---|
| `1D_1/2025` | `1D_1/2025` |
| `1D_2/2025` | `1D_1/2025` |

Jede Nummer, auch die Hauptnummer selbst, steht in dieser Tabelle. Eine Suche nach beliebiger Nummer wird über `case_numbers` auf die `case_id` aufgelöst.

### Index

```sql
CREATE INDEX IF NOT EXISTS idx_summaries_case_lang
    ON summaries (case_id, language, version);
```

### Regeln für Summaries

**Generieren**

- **Pro `(case_id, language)` wird höchstens einmal generiert.** Existiert bereits eine Zeile (egal welcher Status), ruft weder Batch noch UI das LLM erneut auf. Neue Versionen entstehen nur durch Bearbeiten und Freigeben.
- **Coherence-Check** ist Teil der Summary-Pipeline: Beim Generieren wird die Summary inhaltlich mit dem Originalurteil verglichen. `coherence_ok` wird beim Einfügen gesetzt und danach nie geändert. Bei `edited`/`approved` bleibt es `NULL` (kein Check auf menschlich bearbeiteten Text).

**Bearbeiten und Freigeben**

- **Modell-Summaries brauchen keine Freigabe:** `generated` gilt direkt als freigegeben.
- **Bearbeiten setzt automatisch** `status = 'edited'`, `model = 'human'`, `prompt_version = NULL`; der Aufrufer übergibt nur den neuen Text. Der `CHECK` in der Tabelle verhindert inkonsistente Zeilen.
- **Nur menschlich bearbeitete Fassungen werden freigegeben:** `approved` ist nur erlaubt, wenn die neueste Version `edited` ist (geprüft im Code, `store.py`).

Ablauf Generieren → Bearbeiten → Freigeben ergibt drei Zeilen:

| version | status | model | prompt_version | text |
|---|---|---|---|---|
| 1 | `generated` | `apertus-v1-5-70b` | `v2` | Modell-Output (bereits gültig) |
| 2 | `edited` | `human` | `NULL` | bearbeiteter Text |
| 3 | `approved` | `human` | `NULL` | Kopie von v2 |

**Anzeigen**

- **Neueste Version** = Zeile mit der höchsten `version` pro `(case_id, language)`.
- **Angezeigte Version:** neueste `approved`-Zeile, sonst die `generated`-Zeile.
- **Offene Bearbeitung** (neueste Zeile ist `edited`): wird als Entwurf „wartet auf Freigabe“ angezeigt, ersetzt die angezeigte Version aber erst nach `approved`.

### Beispiel-Abfrage: alle Infos zu einer Summary

```sql
-- Alle Versionen zu einer beliebigen Geschäftsnummer, inkl. Originalquelle
SELECT s.case_id, s.language, s.version, s.status, s.model, s.text,
       c.decision_date, c.publication_date, c.url
FROM case_numbers n
JOIN cases     c ON c.case_id = n.case_id
JOIN summaries s ON s.case_id = n.case_id
WHERE n.case_number = '1D_2/2025'
ORDER BY s.language, s.version;
```

---

## Offene Punkte

- **Volltextsuche:** Aktuell einfache Teilstring-Suche (`store.search`, alle Begriffe müssen vorkommen, Akzente zählen). Bei Bedarf SQLite FTS5.
- **Triage-Feedback / Ampel:** zurückgestellt, Entwurf in `src/hack_apertus_coffee_exe/summary/VORGEHEN_DB.md`.
