# Datenschema

Dieses Dokument beschreibt, wie Urteile und Summaries gespeichert werden.

| Teil | Format | Ort | Inhalt |
|---|---|---|---|
| [1. Rohdaten](#1-rohdaten-crawl-json) | JSON, eine Datei pro Urteil | `data/raw/<YYYY-MM>/<decision_date>_<safe_case_id>.json` | Gecrawlte Urteile (Volltext + Metadaten), Issue #3 |
| [2. Summaries-DB](#2-summaries-datenbank-sqlite) | SQLite | `SUMMARY_DB_PATH` (Default unter `data/processed/`) | Zentrale, durchsuchbare Wissensbasis aller Summaries |
| [3. Vector-DB & FTS5](#3-persistente-vector-db--fts5) | SQLite + NumPy `.npz` | `data/processed/vectordb/` | Chunk-Embeddings (`multilingual-e5-small`), Metadaten, Referenz-Flags & FTS5-Volltextindex (#5, #8, #30, #37) |

Datenfluss: Crawler → Rohdaten-JSON → Vector-DB → (Triage) → Summary-Pipeline → Summaries-DB → UI

## Gemeinsame Konventionen

- **Geschäftsnummer (`case_id`):** überall in Originalschreibweise, z. B. `1D_1/2025`, für den Abgleich mit Suva.
- **ID-Normalisierung (`/` → `_`):** Nur in Dateinamen, Vector-DB-IDs und UI-URLs wird `/` durch `_` ersetzt (z. B. `1D_1/2025` → `1D_1_2025`, Datei `2026-04-01_1D_1_2025.json`). In den Feldern `case_id` und `case_ids` sowie in der Datenbank bleibt die Originalschreibweise erhalten.
- **Vereinigte Verfahren:** Ein Urteil kann mehrere Geschäftsnummern haben (z. B. `1D_1/2025, 1D_2/2025`). Die erste ist die Haupt-`case_id`; alle stehen in `case_ids` (JSON) bzw. `case_numbers` (DB).
- **Datumsfilter (`decision_date`):** Empirisch gegen Suvas Referenzliste (`BG_Urteile_2025_Q4_2026_Q1.xlsx`) verifiziert: **59 von 59 (100 %)** der Suva-Referenzfälle für Q4/2025 (28) und Q1/2026 (31) liegen mit ihrem `decision_date` exakt in `2025-10-01`..`2026-03-31`. Der Zeitfilter arbeitet daher verbindlich auf `decision_date`. `publication_date` bleibt `null`, da weder `entscheidsuche.ch` noch die Trefferliste von `search.bger.ch` dieses Feld liefern.
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

Die Rohdaten bleiben unverändert. Die Triage schreibt eine eigene Ergebnisdatei (`triage.cli score --output <datei>`): eine JSON-Liste mit einem Objekt pro Urteil. Die wichtigsten Felder (die in Issue `#3` vorgesehenen Namen `score`, `summary_de` und `summary_fr` gibt es so nicht):

| Feld | Typ | Beschreibung |
|---|---|---|
| `case_id` | `string` | Geschäftsnummer in Originalschreibweise. |
| `final_score` | `number` | Relevanz-Score für Suva (`0`–`100`), Alias `relevance_score`. Gereiht wird nach `rank_score` (`rank_by` in `configs/triage.json`). |
| `rank` | `integer` | Rang im bewerteten Zeitraum, 1 = höchster Score. |
| `ampel` | `string` | Ampel-Einstufung: `"gruen"`, `"gelb"` oder `"rot"` (ASCII ohne Umlaut gemäss `#12`/`#28`/`#40`). |
| `relevance_reason` | `string` | Begründung der Triage-Einstufung, Alias `reason`. |
| `similar_cases` | `list[object]` | Ähnlichste Suva-Referenzfälle (`case_id`, `title`, `similarity`, `summary_snippet`, `bger_url`), Alias `top_reference_matches`. |
| `judgement_date`, `language`, `metadata` | | Urteilsdatum, Sprache und die Metadaten aus dem Crawl-JSON. |

`triage.cli relevance` (#62) schreibt dieselben Felder. `similar_cases` enthält dort nur `case_id` und `similarity`; die Einzelheiten der Bewertung (Rechtsfrage, Kriterien mit Zitaten, Merksatz) stehen in `metadata.relevance`.

> Summaries stehen nicht in dieser Datei. Sie werden verbindlich in der [Summaries-DB](#2-summaries-datenbank-sqlite) gespeichert (mit Verlauf und Status).

---

## 3. Persistente Vector-DB & FTS5

- **Metadaten (`data/processed/vectordb/metadata.sqlite3`, ~2 MB, im Git):** Tabelle `judgements` (`doc_key`, `id`, `case_id`, `case_ids`, `decision_date`, `language`, `division`, `subject`, `is_reference`, `is_negative_reference`, `manual_reference`, `url`, `text_sha256`, `fingerprint`, `n_chunks`, `vector_file`) + Alias-Tabelle `case_lookup`.
- **Chunk-Vektoren (`data/processed/vectordb/vectors/<YYYY-MM>/<doc_key>.npz`, ~79 MB, im Git):** L2-normalisierte `float32`-Chunk-Embeddings (`intfloat/multilingual-e5-small`, 384 Dim.).
- **Lokale Such-Indizes (gitignored, automatisch in ~3 s lokal aufgebaut):** `search_index.npz` (konsolidierte Matrix für schnelle Cosine-Suche) und `fts.sqlite3` (SQLite FTS5 Volltextindex `judgements_fts` über `case_id`, `subject`, `text` für exakte Gesetzes-/Begriffssuche `#30`).

---

## 2. Summaries-Datenbank (SQLite)

Zentrale, durchsuchbare Wissensbasis für alle erzeugten und bearbeiteten Urteils-Summaries. Batch- und On-demand-Generierung schreiben hier hinein; die UI liest von hier und generiert nicht neu, wenn bereits eine Summary existiert.

### Technik

- Engine: SQLite (`sqlite3`, Python-Standardbibliothek)
- Pfad: konfigurierbar über `SUMMARY_DB_PATH` (Default unter `data/processed/`, nicht im Git)
- Code: `src/hack_apertus_coffee_exe/summary/store.py` (Import: `hack_apertus_coffee_exe.summary.store`)
- Schreibende Stellen: Batch und On-demand (`summary/run.py`) über `store.get_or_generate_languages`; die UI-API (`summary/api.py`) über `store.replace_edit` und `store.discard_edit` (seit 28.11.2026, siehe «Bearbeiten und Zurücksetzen»)
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

Eine Zeile = eine Version einer Summary in einer Sprache. **Generierte Zeilen werden nie geändert oder gelöscht.** Daneben gibt es pro Sprache höchstens **eine** bearbeitete Fassung: Eine neue Bearbeitung ersetzt die vorherige, «Zurücksetzen» löscht sie (siehe Regeln).

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
    aligned_by     TEXT,
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
| `status` | `generated` (vom Modell, gilt ohne weiteren Schritt als gültig), `approved` (die bearbeitete Fassung, sofort angezeigt), `edited` (nur noch in Datenbanken von vor dem 28.11.2026: Bearbeitung, die damals auf eine Freigabe wartete) |
| `coherence_ok` | Ergebnis des Coherence-Checks: `1` = bestanden, `0` = Problem gefunden, `NULL` = nicht geprüft (siehe Regeln) |
| `issues` | JSON-Liste der Befunde des Coherence-Checks für diese Sprache (z. B. erfundene Erwägung), damit die UI zeigen kann, was nicht passt; `NULL` bei Bearbeitungen |
| `facts` | JSON-Objekt der extrahierten Fakten (Zwischenschritt der Pipeline); `NULL` bei Bearbeitungen |
| `examples` | JSON-Liste der Suva-Beispiele (Geschäftsnummern), die im Prompt verwendet wurden; `NULL` bei Bearbeitungen |
| `aligned_by` | Apertus-Modell-ID, wenn der Text dieser bearbeiteten Fassung nicht getippt, sondern von Apertus an die Bearbeitung der anderen Sprache angeglichen wurde; sonst `NULL`. Die Zeile bleibt `model = 'human'` (der `CHECK` verlangt das), `aligned_by` sagt, wer den Text geschrieben hat. Fehlt die Spalte in einer älteren Datenbank, ergänzt `store.py` sie beim Öffnen |

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

- **Pro `(case_id, language)` wird höchstens einmal generiert.** Existiert bereits eine Zeile (egal welcher Status), ruft weder Batch noch UI das LLM erneut auf. Danach ändert nur noch eine Bearbeitung den angezeigten Text.
- **Coherence-Check** ist Teil der Summary-Pipeline: Beim Generieren wird die Summary inhaltlich mit dem Originalurteil verglichen. `coherence_ok` wird beim Einfügen gesetzt und danach nie geändert. Bei `edited`/`approved` bleibt es `NULL` (kein Check auf bearbeiteten oder angeglichenen Text).

**Bearbeiten und Zurücksetzen** (Stand 28.11.2026)

- **Keine Freigabe mehr:** Eine gespeicherte Bearbeitung wird sofort angezeigt. `generated` gilt wie bisher ohne weiteren Schritt als gültig.
- **Eine bearbeitete Fassung pro Sprache:** `store.replace_edit` löscht die bisherige bearbeitete Fassung dieser Sprache und legt die neue an (`status = 'approved'`, `model = 'human'`, `prompt_version = NULL`). Es entstehen also keine immer neuen Versionen. Die generierte Zeile bleibt unangetastet.
- **Die andere Sprache zieht nach:** Wird nur eine Sprache geändert, gleicht Apertus die andere an (`Summarizer.align_language`). Der angeglichene Text wird als bearbeitete Fassung der anderen Sprache gespeichert, mit der Modell-ID in `aligned_by`.
- **Von Hand Geschriebenes überschreibt Apertus nie:** Ist die andere Sprache von Hand bearbeitet (`aligned_by IS NULL`), bleibt sie stehen; die Oberfläche sagt das.
- **Kann Apertus nicht angleichen** (kein Schlüssel, Gateway nicht erreichbar), wird die Bearbeitung trotzdem gespeichert; die andere Sprache bleibt unverändert und die Oberfläche meldet es.
- **Zurücksetzen:** `store.discard_edit` löscht die bearbeitete Fassung (eine Sprache oder beide). Angezeigt wird wieder der generierte Text. Das geht immer, weil generierte Zeilen nie gelöscht werden.

Ablauf Generieren → Deutsch bearbeiten → (Französisch zieht nach) ergibt pro Sprache höchstens zwei Zeilen:

| language | version | status | model | aligned_by | text |
|---|---|---|---|---|---|
| `de` | 1 | `generated` | `apertus-v1-5-70b` | `NULL` | Modell-Output |
| `de` | 2 | `approved` | `human` | `NULL` | von Hand bearbeitet |
| `fr` | 1 | `generated` | `apertus-v1-5-70b` | `NULL` | Modell-Output |
| `fr` | 2 | `approved` | `human` | `apertus-v1-5-70b` | von Apertus an die deutsche Bearbeitung angeglichen |

**Anzeigen**

- **Angezeigte Version:** die `approved`-Zeile, sonst die `generated`-Zeile.
- **Herkunft in der Oberfläche:** «generiert» (`generated`), «bearbeitet» (`approved`, `aligned_by IS NULL`), «angeglichen» (`approved`, `aligned_by` gesetzt).

**Datenbanken von vor dem 28.11.2026**

- Damals war jede Bearbeitung eine neue Zeile (`edited`) und wurde erst nach einer Freigabe (`approved`, Kopie) angezeigt. `store.save_edit`, `store.approve` und `POST …/summary/approve` gibt es weiter, die Oberfläche benutzt sie nicht mehr.
- Eine Bearbeitung, die noch auf die Freigabe wartete, geht nicht verloren: Die Oberfläche zeigt sie im Editor mit einem Hinweis; «Speichern» übernimmt sie als bearbeitete Fassung, «Zurücksetzen» verwirft sie. Ältere bearbeitete Zeilen derselben Sprache werden dabei entfernt.

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
