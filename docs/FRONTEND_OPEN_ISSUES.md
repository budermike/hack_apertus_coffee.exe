# Front end: open issues (drafts to file on GitHub)

These are issue drafts from the front-end audit of 27 Nov 2026. They are **not filed yet**:
copy each section into a new GitHub issue (title = heading). "Temporary front-end
solution" says what the UI does today so the feature is usable before the backend exists.

Where things are: UI in `src/presentation/` (`api.js` is the only file that talks to the
backend, `mock.js` the simulated backend), HTTP server in
`src/hack_apertus_coffee_exe/summary/api.py`, judgement list in
`src/hack_apertus_coffee_exe/present/judgements.py`.

| # | Issue | Needs backend | Temporary front-end solution |
|---|---|---|---|
| 1 | Store relevance feedback | yes | yes: kept in the browser, marked "nur lokal" |
| 2 | "Anpassen": refine one summary language | yes | no: button does nothing (decided) |
| 3 | Criteria with quotes, legal question, practical rule | yes | yes: section shows triage signals and a note |
| 4 | French triage reasoning | yes | yes: note "nur auf Deutsch" |
| 5 | Summarize relevant judgements on the server | yes | yes: queue in the browser |
| 6 | Search a period that has not been triaged yet | yes | no |
| 7 | Add one judgement by case number | yes | no |
| 8 | Evaluation and "how it works" pages | yes | no |
| 9 | Evidence behind a summary and extra case facts | yes | no |
| 10 | Decide the approval step for edited summaries | decision | yes: "Speichern" + "Freigeben" |
| 11 | Case number spelling in the API | small | yes: UI converts it |
| 12 | Suva-format export from the server | optional | yes: built in the browser |

---

## 1. Store relevance feedback ("Für Suva relevant" / "Nicht relevant") — extends #40

**Missing:** `POST /api/judgements/{id}/feedback` is a placeholder
(`present/judgements.py: record_feedback`): it logs the body and answers `{"stored": false}`.
`GET /api/judgements` always returns `adjustment: 0`.

**Expected:** a lawyer's decision is stored, returned with the judgement in every later
search (also for colleagues), and used by triage as an example for similar judgements.

**Current behaviour:** the UI sends the decision, sees `stored: false`, keeps it in this
browser's localStorage (`localFeedback` in `api.js`) and says so: toast and note
"Entscheidung nur in diesem Browser gespeichert", list tag "korrigiert (nur lokal)". The
decision survives a new search or reload in the same browser only.

**Backend changes:**
- Store the body `{adjustment, direction, level, decision}` (`decision` = `"high"`, `"low"`
  or `null` when taken back; `direction` 0 = level confirmed). The table design is drafted in
  `summary/VORGEHEN_DB.md` ("Triage-Feedback").
- Answer `{"stored": true}`.
- Return `adjustment` and `userLevel` (= `decision`) per judgement in `GET /api/judgements`.
- Use stored decisions as positive/negative examples in the triage prompt.

**Front end afterwards:** nothing to change; `api.js` drops the local copy as soon as the
backend answers `stored: true`. The `localFeedback` block can then be deleted.

## 2. "Anpassen": refine the summary in one language with Apertus

**Missing:** there is no route for it, and what "Anpassen" should do is not specified yet.

**Expected (to be confirmed by the product owner):** per language, the user gives an
instruction (for example "kürzer fassen"); Apertus rewrites only that language's text; the
other language stays untouched; the result is an unsaved edit the user can save.

**Current behaviour:** each summary box has an "Anpassen" button that deliberately does
nothing (`onAdjust` in `script.js`), as decided on 27 Nov. An earlier version opened an
instruction field and called `POST …/summary/adjust`, which answers 404.

**Backend changes:** `POST /api/judgements/{id}/summary/adjust`, body
`{lang, text, instruction}` → `{lang, text}`; Apertus does the rewriting (rule 1); nothing
is stored (saving goes through the existing `PUT`).

**Temporary front-end solution:** none, on purpose.

## 3. Triage output: criteria with quotes, legal question, practical rule

**Missing:** triage produces `relevance_reason`, `keyword_hits`, `key_topics` and
`similar_cases`, but not the criteria (K1–K5, N1–N3) with a quote and its location, the
legal question, or the practical rule that the detail view is designed for.

**Expected:** "Kriterien und Belege aus dem Urteil" lists each criterion with a verbatim
quote, its consideration (`E3.1`) and whether the quote was found in the judgement;
"Rechtsfrage" and "Merksatz für die Praxis" are shown above it.

**Current behaviour:** the section is always there and folds by clicking its title. Without
criteria it shows the triage signals (`signals`, `topics`, now passed on by
`present/judgements.py`) and the note "Kriterien mit Belegzitaten liefert die Triage für
dieses Urteil noch nicht." "Rechtsfrage" and "Merksatz" are hidden while empty.

**Backend changes:** extend the Apertus triage prompt and `TriageResult` with
`criteria: [{id, at, quote, status}]`, `question`, `rule`; verify quotes against the
judgement text (`status`: `exakt` / `ungefähr` / `fehlt`); pass them on in
`present/judgements.py`. The UI renders them as soon as they arrive (tested with the mock).

## 4. Triage reasoning in French

**Missing:** `relevance_reason` exists in German only; `present/judgements.py` sends the
same text as `explanation.de` and `explanation.fr`. The subject exists only in the language
of the judgement.

**Expected:** French users read the reasoning in French.

**Current behaviour:** in the French UI the section carries the note "Cette partie n'existe
qu'en allemand."

**Backend changes:** let Apertus write the reason in both languages (or translate it) and
send it as `explanation.fr`.

## 5. Summarize the relevant judgements on the server

**Missing:** summaries are only generated when the UI asks for them, one judgement per
request, one generation at a time (`Backend._generate_lock`).

**Expected:** every judgement at or above the threshold has its summary without the user
keeping a browser tab open.

**Current behaviour:** after a search the UI queues all judgements at or above the
threshold (best first, two requests at a time, the opened case first) and shows the
progress ("Zusammenfassungen: 12 von 37", list tags "wartet" / "wird zusammengefasst").
A new search drops what is still waiting. For a whole quarter this can mean more than a
hundred summaries, each with several Apertus calls, and it stops when the tab is closed.

**Backend changes:** generate the summaries of relevant judgements as a batch after triage
(`summary/run.py` already has the building blocks), or offer a server-side queue with a
status route. The UI queue can then be reduced to fetching stored entries.

## 6. Search a period that has not been triaged yet — related to #35

**Missing:** `GET /api/judgements` filters one triage results file given at start
(`--triage`). A period outside that file returns an empty list; without `--triage` every
search answers 503.

**Expected:** searching a date range crawls and triages what is missing for that range, or
at least tells the user which periods are available.

**Current behaviour:** the UI's default range (last three months) shows "Keine Urteile in
diesem Zeitraum gefunden" when the loaded file covers another period.

**Backend changes:** run crawl + triage for the requested period on demand (with progress),
or add the available date range to `GET /api/config` so the UI can preset it.

## 7. Add one judgement by case number

**Missing:** the design reference had a field "Urteil hinzufügen" (`POST /api/add`) that
fetched, triaged and summarized one judgement by its case number. There is no such route.

**Expected:** entering `8C_123/2026` adds that judgement to the list with score, reasoning
and summary.

**Current behaviour:** not in the UI. A judgement inside the searched period can be found
with the keyword filter.

**Backend changes:** a route that crawls, triages and summarizes one case.
**Temporary front-end solution:** none; the browser cannot score a judgement.

## 8. Evaluation page and "how the system works" page

**Missing:** the design reference had the tabs "Auswertung" (hit list against Suva's
selection, F2, completeness, token usage) and "So arbeitet das System". The backend has no
route for evaluation results (#21).

**Current behaviour:** not in the UI (the requirements asked for "Urteile" and "Datenbank").

**Backend changes:** `GET /api/evaluation` serving the tables from `results/tables/`.
**Temporary front-end solution:** none; showing invented evaluation numbers would mislead.

## 9. Evidence behind a summary and extra case facts

**Missing in the backend data:** the plan points a summary is based on ("Worauf sich die
Zusammenfassung stützt"), the scores for principle content and usefulness, the single-judge
flag, and whether Suva chose a similar earlier case.

**Current behaviour:** the UI shows what exists: the result of the automatic check
("Text geprüft" / "Text prüfen" with the findings from `coherenceOk` / `issues`), the
chamber (`court`), the outcome and the similar cases with their similarity.

**Backend changes:** add these fields to the summary entry and the judgement item.

## 10. Decide the approval step for edited summaries

**Situation:** `PUT …/summary` stores an edit as a version that waits for approval; the
shown text and the database search keep using the generated text until
`POST …/summary/approve` is called. An empty text is ignored silently.

**Current behaviour:** the UI shows the saved edit, states "Gespeichert · noch nicht
freigegeben" and offers "Freigeben", which calls the approve route.

**To decide:** should "Speichern" approve immediately (one step for the lawyer), or is the
second step wanted? Should the database search also find edits that wait for approval?
Should an emptied text be rejected with an error instead of being ignored?

## 11. Case number spelling in the API

**Situation:** the API sends `caseNumber` as `8C 328/2025` (space); the design reference
and the triage texts use `8C_328/2025`.

**Current behaviour:** `api.js` converts to the underscore form for display; the keyword
filter finds both spellings.

**Backend change (small):** send the `case_id` as it is and adapt
`tests/test_present_judgements.py`; then `formatCaseNumber` in `api.js` can go.

## 12. Suva-format export from the server (optional)

**Current behaviour:** "Im Suva-Format öffnen" builds the page in the browser from the
texts in the editor, in the layout of `judgements/Export_*.html` (title, "Text DE",
"Text FR", "Link zum Urteil").

**Possible backend change:** an export route that also fills "Autor" and exports several
judgements at once.
