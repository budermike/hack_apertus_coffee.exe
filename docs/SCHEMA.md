# Datenschema (Issue #3)

Ein JSON pro Bundesgerichtsurteil unter `data/raw/<YYYY-MM>/<decision_date>_<safe_case_id>.json`.

## Konventionen & Annahmen

- **ID-Normalisierung (`/` → `_`):** In Dateinamen, Vector-DB-IDs und UI-URLs wird `/` in der Geschäftsnummer durch `_` ersetzt (z. B. `1D_1/2025` → `1D_1_2025`, Datei `2026-04-01_1D_1_2025.json`). In den Feldern `case_id` und `case_ids` bleibt die Originalschreibweise (`1D_1/2025`) für den Abgleich mit Suva erhalten.
- **Datumsfilter (`decision_date` vs. `publication_date`):** `publication_date` ist aktuell `null`, da weder `entscheidsuche.ch` noch die Ergebnisliste von `search.bger.ch` das Publikationsdatum liefern. **Annahme:** Bis Suva klärt, ob für Q2/2026 das Urteils- oder das Publikationsdatum zählt, wird nach `decision_date` gefiltert.
- **Kein HTML gespeichert:** `content_html` wird nicht gespeichert, um unnötige Daten zu vermeiden. Die Integritätsprüfung erfolgt über `fetch.text_sha256` auf `text`.

## Felder im Rohdaten-JSON (`data/raw/`)

| Feld | Typ | Beschreibung |
|---|---|---|
| `id` | `string` | Normalisierte interne ID (`/` → `_`, z. B. `"1D_1_2025"`), direkt nutzbar für Vector-DB und UI-URLs. Hinweis: Bei mehreren Entscheiden zum selben Dossier an verschiedenen Tagen ist `<decision_date>_<id>` eindeutig. |
| `case_id` | `string` | Primäre Geschäftsnummer in Originalschreibweise, z. B. `"1D_1/2025"` (für den Abgleich mit Suva). |
| `case_ids` | `list[string]` | Alle Geschäftsnummern aus dem Urteilskopf (z. B. `["1D_1/2025", "1D_2/2025"]` bei vereinigten Verfahren). |
| `decision_date` | `string` | Urteilsdatum im ISO-Format `YYYY-MM-DD`. |
| `publication_date` | `string \| null` | Publikationsdatum im ISO-Format oder `null` (aktuell `null`, siehe Annahme oben). |
| `department` | `string \| null` | Abteilung des Bundesgerichts (gemäss Issue `#3`), z. B. `"I. Öffentlich-rechtliche Abteilung"`. |
| `division` | `string \| null` | Identisch mit `department` (Alias für Vector-DB & Triage gemäss Issue `#37`). |
| `subject` | `string \| null` | Rechtsgebiet / Gegenstand (bei `bger` aus der Trefferliste, bei `entscheidsuche` aus dem `Gegenstand` / `Objet` / `Oggetto`-Block im Text geparst). |
| `language` | `string \| null` | Urteilssprache: `"de"`, `"fr"`, `"it"` oder `null`. |
| `url` | `string` | Link auf das Originalurteil bei `search.bger.ch`. |
| `text` | `string` | Volltext des Urteils (ein Absatz pro Zeile, ohne HTML). |
| `operator_metadata` | `object` | Unveränderte Metadaten der jeweiligen Quelle (`bger` oder `entscheidsuche`). |
| `fetch` | `object` | Herkunfts- und Integritätsmetadaten (`fetched_at`, `fetched_by`, `source`, `url`, `hash_algorithm`, `text_sha256`). |

### Unterfelder von `fetch`

| Feld | Typ | Beschreibung |
|---|---|---|
| `fetched_at` | `string` | UTC-Zeitstempel des Abrufs (ISO 8601). |
| `fetched_by` | `string` | User-Agent des Crawlers. |
| `source` | `string` | Datenquelle (`"entscheidsuche"` oder `"bger"`). |
| `url` | `string` | Abgerufene Quell-URL. |
| `hash_algorithm` | `string` | Hash-Verfahren (`"sha256"`). |
| `text_sha256` | `string` | SHA-256-Hash über `text` (UTF-8) zur Integritätsprüfung. |

## Ergebnisfelder (Triage & Summary)

Nachgelagerte Pipeline-Schritte ergänzen gemäss Issue `#3` folgende Felder:

| Feld | Typ | Beschreibung |
|---|---|---|
| `score` | `number` | Relevanz-Score für Suva. |
| `ampel` | `string` | Ampel-Einstufung (z. B. `"rot"`, `"gelb"`, `"grün"`). |
| `relevance_reason` | `string` | Begründung der Triage-Einstufung. |
| `similar_cases` | `list[string]` | Ähnliche Fälle (z. B. über Vektorsuche). |
| `summary_de` | `string` | Zusammenfassung auf Deutsch. |
| `summary_fr` | `string` | Zusammenfassung auf Französisch. |
