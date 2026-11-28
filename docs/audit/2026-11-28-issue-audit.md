# Repository and issue audit, 28 November 2026

Audit of `budermike/hack_apertus_coffee.exe`, carried out on 28.11.2026 between 00:10 and 01:39 CET. The submission deadline is 28.11.2026, 14:30.

**Baseline.** The audit started on `main` = `ad072f4`. At 01:09 CET, while it ran, PR #63 was merged (`main` = `7218ca5`), and at 01:03 a commit for #48 was pushed to the branch `triage`. Everything below is stated for `7218ca5` unless it says otherwise.

**How to read the verdicts.** Every requirement was checked against the code, not against issue status or PR text. "Run" means a command was executed and its output read; "read" means the code was read only. Four lines of work were examined: `main`, PR #63 (`bdad63a`, now merged), the branch `feat/present-judgements` (`707d4e7`, no pull request) and the branch `triage` (`2c29860`, no pull request). No Apertus call was made during the audit, nothing was merged, and nothing was pushed to `main`.

## 1. Executive summary

**Condition.** The code base is sound: 197 unit tests pass on `main` without network access, a lint pass finds two unused imports, and no proprietary AI API, no secret (also not in the history) and no hard-coded service URL outside the configuration was found. The Q2/2026 results that PR #63 brought in are reproducible: replaying the stored Apertus answers through the real command line gives all seven committed result files byte for byte.

But `main` is still not the product. On `main` the web UI runs on in-browser mock data (`USE_MOCK = true`) and the backend has no route for the judgement list. The connected UI lives on a branch without a pull request:

| Where | What it holds | State |
|---|---|---|
| `main` (`7218ca5`) | Crawler, vector DB, triage (`score` and, since 01:09, `relevance` with the Q2 list of 116 judgements), summary pipeline, summaries DB, summary API, mock UI, evaluation | 197 tests pass |
| `feat/present-judgements` (no PR) | `GET /api/judgements`, `GET /api/config`, UI connected to the real backend | 152 tests pass; conflicts with `main` in 4 files |
| `triage` (no PR, pushed 01:03) | Work on #48: decisions, contract functions, central configuration | 208 tests pass; not audited in depth |

A trial merge of `main` and the front-end branch (throwaway checkout, never pushed) needs four small conflict resolutions and then passes 205 tests. Started with the Q2 results it serves 410 judgements, 116 of them green, 29 of those from June 2026. That merged state is the first one in which a judge sees real data in the UI.

**Issues examined.** 51 issues: 26 closed and 25 open when the audit started, 27 and 24 at its end (#62 was closed by the merge of PR #63). Eight further issue numbers (#1, #4, #12, #24, #28, #40, #52, #54) were deleted on GitHub; five of them are still referenced by open issues or code.

**Most important findings.**

1. **Closed does not mean done.** Of the 26 issues that were closed at the start, 5 are complete as specified (#7, #8, #14, #21, #37) and 8 more are complete apart from minor gaps (#3, #5, #9, #10, #11, #15, #26, #49). Nine are partly implemented (#6, #13, #16, #17, #18, #19, #20, #33, #35). #50 and #51 have no implementation on any branch, #53 only on the unmerged front-end branch, #27 was closed as not planned. Eleven issues were closed without a merge between 23:11 and 23:38 CET on Friday, the hour after the 22:00 MVP milestone; follow-up issues #64–#74 pick up part of the gaps, the rest was untracked until this audit (section 7).
2. **The committed Suva list could be replaced without anyone noticing**, in two ways, both reproduced. A `relevance` run without API key or with time-outs overwrote the tracked score files and, with 20 % time-outs, shortened the Q2 list from 116 to 80 judgements while exiting with code 0. And the offline `score` command from the triage README wrote a top-30 TF-IDF list over the same file. Fixed (section 5).
3. **A mandatory rule of the organisers was broken.** The template requires the variables `LLM_NAME`, `LLM_BASE_URL`, `LLM_API_KEY`. The gateway smoke test read only `APERTUS_API_KEY` and crashed with a `KeyError` on a correctly configured machine. Fixed (section 5).
4. **One malformed Apertus answer aborted a whole `score` run**, with no results, and deterministically again on every rerun. Fixed (section 5).
5. **The list endpoint dropped what the new scoring produces.** The relevance scoring delivers legal question, practical rule and criteria with verified quotes for every scored judgement; the list endpoint on the front-end branch dropped all of them, and the UI said that triage delivers none. Fixed as a small change on top of the front-end branch (section 5).
6. **The demo data on the front-end branch is not Apertus output.** `data/processed/triage_2026-01.json` (609 judgements, January 2026) was produced with `--no-llm`: every row has `llm_model: null` and a reason starting with "[Embedding-Only-Modus]". Shown in a demo, it would present a heuristic as the Apertus triage (hard rule 1). `results/triage_q2_2026.json` on `main` is real Apertus output and should replace it.
7. **There was no container setup**, although sovereign deployability is a must. Added and tested from a fresh clone, also with networking disabled (section 5).
8. **The full-text index was silently incomplete on a fresh clone** after the first new day was ingested (2 rows instead of 6055), which also changes the hybrid score. Fixed (section 5).
9. **The single pipeline command has no issue any more.** #52 was deleted, but #55, #56, #59 and one box of #31 depend on `python -m hack_apertus_coffee_exe.pipeline`, which does not exist.
10. **All data come from the mirror.** The challenge names bger.ch; 6053 of 6053 judgements were fetched from entscheidsuche.ch, and the bger.ch path has never run against the real site. The PM accepted this on condition that the report says so; the report is still the template.
11. **Submission documents are still templates**: `technical_report.md`, the project part of `README.md`, the report PDF, the video link and the pitch deck (#59, #25).
12. **Partner data in a public repository.** `judgements/` holds 55 Suva summaries, each with an author section, and `BG_Urteile_2025_Q4_2026_Q1.xlsx`, which contains 60 links to a Suva SharePoint, its creator in the file properties and a sensitivity label that was set by hand (the file does not say which). Whether Suva allows this is open (#59).

**Changes made.** Six pull requests with code and tests, one with this report, one tracking issue. Nothing was merged; no issue was closed or reopened by the audit (reasons in section 3).

**Remaining risks.** Section 11. The largest: the front-end branch is not merged and has no pull request, and every open UI issue depends on it.

## 2. Repository overview

### Architecture as built on `main`

```
bger.ch / entscheidsuche.ch
        │  crawl/crawl.py, crawl/ensure.py      one JSON per judgement, day index
        ▼
data/raw/<YYYY-MM>/*.json  ──►  crawl/vectordb.py + embed.py     e5-small vectors, SQLite metadata,
        │                       (data/processed/vectordb/)        FTS5 full text, reference flags
        │                                 └──► eval/eval.py       recall, precision, F2 of the vector score
        ▼
triage.cli relevance  ──►  results/triage_<period>.json           two Apertus passes per candidate,
  (triage/relevance*.py,    results/recall_<period>.txt           calibrated on earlier labelled quarters
   triage/calibration.py)   results/tables/relevance_scores_*     └──► eval/relevance_eval.py (hold-out)
triage.cli score      ──►  JSON, ranking CSV, recall list         older path: TF-IDF similarity + one Apertus score
        ▼
summary/generate.py   ──►  summary/store.py (SQLite, append-only) facts (Apertus) → DE and FR (Apertus)
  (summary.run)                   ▲                               → rule-based check → one retry
                                  │
                         summary/api.py (stdlib HTTP)  ──►  src/presentation/ (static JS UI, mock data on main)
```

| Component | Code | Notes |
|---|---|---|
| Crawl | `crawl/crawl.py`, `crawl/ensure.py`, `src/crawl/` (shim for `python -m crawl`) | Two sources in `configs/crawl.json`; the configured one is the mirror. `ensure_period` fills gaps and ingests, but nothing on `main` calls it. |
| Vector DB | `crawl/vectordb.py`, `embed.py` | `intfloat/multilingual-e5-small`; 6053 judgements, 49 359 chunks, pre-computed vectors tracked in git; `fts.sqlite3` and the search index are rebuilt locally. |
| Triage | `triage/` | `relevance` (70B, two passes, calibration) produces the Suva list; `score` (8B by default) is the older path. Caches in `data/processed/triage/` are git-ignored. |
| Summary | `summary/` | `summary.run one|batch|show`; prompts in `prompts/`; store in `SUMMARY_DB_PATH`. |
| API + UI | `summary/api.py`, `src/presentation/` | Summary routes only on `main`. The UI has no build step and loads no external file. |
| Evaluation | `eval/eval.py`, `eval/relevance_eval.py`, `results/` | Both reports reproduce offline. |
| Configuration | `.env` (gateway, DB path), `configs/crawl.json`, `configs/triage.json` | All paths are relative to the working directory. |

### Technical weaknesses

- **Three similarity mechanisms that do not meet.** `score` uses TF-IDF against the 55 Suva summaries; `relevance` picks its examples by a keyword fingerprint; the e5 vector DB is used only by the evaluation and by `ensure_period`. `docs/MODELS.md` says e5 serves "Triage-Retrieval"; on `main` it does not.
- **Triage results live in files, not in a store.** #48 and #50 specified a SQLite table `triage`. What exists is a JSON file handed to the server with `--triage`. Feedback (#64), periods without results (#69) and server-side summaries (#68) all need a persistence layer that is not there.
- **`ensure_period` has no caller.** Neither triage nor API nor UI calls it, so "select a period, the system fetches what is missing" does not happen.
- **Paths depend on the working directory.** `configs/`, `prompts/`, `judgements/` and `data/` are found only when the process starts in the project folder; elsewhere defaults apply silently.
- **Model calls sit in four modules** (`triage/llm.py`, `triage/relevance.py`, `summary/llm.py`, `triage/embeddings.py`), not one as `AGENTS.md` asks. `relevance` ignores `LLM_NAME` / `APERTUS_MODEL` (its default is 70B; `--model` overrides). The optional `--embedder sentence-transformers` of `score` defaults to `BAAI/bge-m3`, which is not in `docs/MODELS.md`.
- **Commands that overwrite tracked files with different content.** Besides the two cases fixed here: the plain evaluation command drops the TF-IDF row from `results/eval.md`, `python -m crawl …` adds rows to `results/tables/crawl_report.csv`, and `crawl.vectordb --embedder tfidf` on the default DB replaces `embedder_meta.json`, after which `ensure_period` re-embeds committed e5 vectors with TF-IDF.
- **No CI.** `.github/` held only templates under GitLab folder names; nothing runs the tests on a pull request.
- **`requirements.txt` alone is not enough.** Without `sentence-transformers` (only in `requirements-dev.txt`) one test fails and the vector-DB command line fails on a plain date query; `python -m crawl` needs the editable install.
- **Documentation behind the code.** `technical_report.md` is the untouched template; `docs/ARCHITECTURE.md` is empty on `main` (filled on the branch `triage`); `docs/SCHEMA.md` listed result fields the triage does not write and had a leftover conflict marker.

## 3. Complete issue audit

Classification, after the audit brief: **A** implemented and verified · **B** partly implemented, sound · **C** partly implemented, flawed · **D** valid and specified, not implemented · **E** needs minor correction · **F** underspecified or waiting for a decision · **G** obsolete or redundant.

"Left closed" means: the issue stays closed, and what is missing is listed in the tracking issue and in section 7. No issue was reopened: the closures look like a deliberate sweep after the MVP milestone, many gaps already have follow-up issues, and reopening ten issues thirteen hours before the deadline would bury the short list that still matters. No open issue was closed either: none is complete.

### Closed issues

#### #3 [closed] Gemeinsames Datenschema festlegen
- **Objective:** one JSON per judgement, schema in `docs/SCHEMA.md`, the date field for the time filter decided.
- **History:** review comments by the PM on PR #36; schema written in PR #36 and extended in #44 and #46. Closed in the sweep (23:38 CET).
- **Verified (run):** all 6053 raw files conform: 13 top-level and 6 `fetch` fields, `id` = `case_id` with `/` → `_`, path `<YYYY-MM>/<date>_<id>.json`, SHA-256 correct, no HTML. `case_ids` holds all numbers (202 joined judgements), `subject` is parsed (3 empty). The time filter uses `decision_date`; all 59 Suva judgements of Q4/2025 and Q1/2026 fall into their quarter by that date.
- **Missing:** Suva's answer on which date counts is not recorded. The comment "die Quelle ist entscheidsuche.ch … wir nennen es im Report" is not carried out. The result-field table of `docs/SCHEMA.md` named fields nobody writes.
- **Classification:** A with minor gaps. **Action:** conflict marker and result-field table corrected in the #59 pull request. Left closed.

#### #5 [closed] Embedding-Modell wählen + embed.py
- **Objective:** a local multilingual embedding model, a decision on what is embedded, `embed(texts) -> vectors`, documented in `docs/MODELS.md`.
- **History:** PR #46 (Antigravity / Gemini).
- **Verified (run, offline):** `embed` returns unit vectors of 384 dimensions; German, French and Italian paraphrases score 0.87–0.91 against 0.82 for an unrelated sentence; the stored vectors of a judgement are bit-identical to freshly computed ones. Model, licence and role are in `docs/MODELS.md`.
- **Missing:** the chunking decision (header and footer stripped, 350 words with 50 overlap, a `[division | subject]` prefix) exists only in code. The dependency is only in `requirements-dev.txt`. That the gateway has no embedding endpoint could not be checked offline.
- **Classification:** A with minor gaps. Left closed.

#### #6 [closed] Crawler bger.ch: Zeitraum als Input -> JSON in data/raw/
- **Objective:** `python -m crawl --from … --to …`, result list and judgements from bger.ch only, one JSON per judgement, polite crawling.
- **History:** PR #36 (Claude Code, review fixes with Gemini). The PR says "Not yet tested against the real bger.ch".
- **Verified (run):** the command works (through the shim in `src/crawl/`, with the editable install); files follow the schema; pauses of 2 s (bger) and 0.5 s (mirror) with back-off; the URL comes from the configuration.
- **Missing:** "nur bger.ch". The configured source is `entscheidsuche`; 6053 of 6053 files and all 273 index days come from the mirror; the bger.ch path is tested on synthetic HTML only. The PM accepted the mirror in a comment on #3.
- **Classification:** B, an accepted deviation that has to be disclosed. Left closed; the disclosure belongs to the report (#59).

#### #7 [closed] Cache: bereits gecrawlte Urteile/Zeiträume überspringen
- **Verified (run):** file check before download; index with 273 days from 2025-10-01 to 2026-06-30, every count equal to the files on disk; a second run over 91 and over 273 days makes 0 HTTP calls and takes 0.0 s; after a simulated outage only the missing documents are fetched.
- **Classification:** A. Left closed.

#### #8 [closed] Ingest: JSON -> Embeddings -> Vector-DB (mit Metadaten)
- **Verified (run):** persisted (SQLite plus 6053 vector files, integrity check ok, reopened across processes); metadata identical to the raw JSON for all 6053 rows; a second ingest embeds 0 and takes 0.97 s; "alle Urteile 2026-06 mit Vektoren" returns 773 rows, each with vectors.
- **Classification:** A. Left closed. Two hazards in the same module are listed under #30 and in section 2.

#### #9 [closed] Backfill: Q4/2025, Q1/2026 und Q2/2026 crawlen + ingesten
- **Verified (run):** 1930 / 2004 / 2096 files, every index day matches, the same counts in the vector DB; all 59 Suva-listed judgements of Q4 and Q1 are present; `results/tables/crawl_report.csv` matches the data.
- **Could not be verified:** completeness against the source (needs the network).
- **Classification:** A with that reservation. Left closed.

#### #10 [closed] Referenzbasis aus Suva-Labels aufbauen
- **Objective:** read Suva's list, flag the judgements in the vector DB, report missing ones, make the reference base selectable by period.
- **History:** closed 16:56 CET with the merge of PR #34, before the vector DB existed; the flags came with PR #46.
- **Verified (run):** 82 references (55 summaries ∪ 59 list entries) are flagged, none missing, none wrongly flagged; the evaluation selects the 28 references of Q4 by period.
- **Missing:** the report of missing judgements exists only as a method (`missing_reference_cases`) that no command calls and no test covers; the one cantonal entry of the list is skipped silently. Triage does not read these flags.
- **Classification:** A with minor gaps. Left closed.

#### #11 [closed] Score: Cosine Similarity zu Referenzbasis + Zeitfilter
- **Objective:** score every candidate of a period against Suva references from before that period; store the three most similar cases.
- **History:** PR #34 (Gemini), #38 → #41 (Claude Code). Closed 16:56 CET with the merge of #34, before #41 added `similar_cases` under that name and the leakage rule.
- **Verified (run):** mean of the top-k cosine similarities; maximum over chunks per reference; three similar cases for 2096 of 2096 Q2 judgements; judgements outside the period are skipped ("2096 scored, 3957 outside period"); references decided in or after the period are dropped.
- **Missing:** "k testen" was never done; no Q2 result of this path is committed (the offline run takes 18 s, so it is reproducible).
- **Classification:** A with minor gaps. Left closed.

#### #13 [closed] Relevanz-Begründung via Apertus
- **Objective:** 2–3 sentences per judgement on why it is (not) relevant, as JSON, stored in `relevance_reason`.
- **History:** PR #34, #41. Closed in the sweep after the MVP milestone.
- **Verified:** the prompt names legal area, link to Suva/UVG and the most similar case, also for non-relevant judgements (read); the answer is parsed into `relevance_reason` (run, fake client).
- **Missing / incorrect:** "Jedes Q2-Urteil hat eine Begründung" was not met by this path: no Q2 run with it exists, and the run could not finish. One answer that is not valid JSON stopped the whole run without results, and at temperature 0 the same judgement fails on every rerun (#62 names `9C_449/2025` with 8B). A missing `apertus_score` was silently scored 0.
- **Depends on / conflicts:** for the Suva list this path is superseded by #62, whose results give every scored Q2 judgement a reason.
- **Classification:** C. **Action:** flaw repaired in PR `fix/13-triage-survives-unusable-answer` (section 5). Left closed.

#### #14 [closed] Stilprofil + DE/FR-Glossar aus Suva-Beispielen
- **Verified (run):** `prompts/style_de.md`, `prompts/style_fr.md` and `prompts/glossary.md` exist and are part of both writer prompts.
- **Classification:** A. Left closed.

#### #15 [closed] Summary-Generierung DE und FR mit Apertus
- **Objective:** facts as JSON, then DE and FR written independently, with few-shot examples of the same language, style profile and glossary.
- **History:** PR #43/#44/#45. Both PR texts say that no live call was tested at merge time; commit `2445d2b` then fixed check and prompts "after the first live run (8C_221/2025)".
- **Verified (run, mocked model):** facts step; the FR prompt does not contain the DE text; two examples of the same language, the judgement's own Suva summary excluded; style and glossary in the prompt.
- **Could not be verified:** "Ein Juni-2026-Urteil hat eine Summary DE + FR". The summaries DB is git-ignored and none exists on the audit machine.
- **Classification:** A for the code; done-when unverified. Left closed.

#### #16 [closed] Coherence-Check DE <-> FR und gegen das Urteil
- **Objective:** check DE against FR and both against the judgement; regenerate once on deviation, otherwise mark in the UI.
- **History:** PR #43/#45; the rule-based design is recorded in `docs/DECISIONS.md`.
- **Verified (run):** cited articles and considerations must exist in the judgement; the closing paragraph must match the operative part; one retry per language with the errors as feedback; runs automatically after each generation.
- **Missing / incorrect:** DE↔FR compares only the cited articles and considerations (German text in the FR slot passes). Case number and date inside the text are not checked, although the issue ticks that box; titles come from the data, which covers the heading only. One language's error sets `coherence_ok = False` on both rows. The UI on `main` shows no finding; the front-end branch does.
- **Classification:** B. Left closed; gaps in the tracking issue. Not repaired here: flagging "wrong" case numbers needs a rule for citations of other judgements, which is a design decision.

#### #17 [closed] Batch für grüne Urteile + Funktion für on demand
- **Objective:** summarise all green judgements in a batch; `summarize(case_id)` for the others, callable from the UI.
- **Verified (run):** `run_batch` takes green rows only and skips stored ones; `summarize_case` and `POST /api/judgements/{id}/summary` work for any traffic light; results are stored.
- **Missing:** no function named `summarize(case_id)` on `main` (the branch `triage` adds one); "Alle grünen Q2-Urteile haben Summaries" is not met (no summaries exist); the UI button on `main` calls the mock. A batch in which every judgement fails exits with code 0.
- **Classification:** B. Left closed; the Q2 summaries are in #56.

#### #18 [closed] Dashboard: Liste mit Score, Ampel, sortier- und filterbar
- **Objective:** table with case number, date, area, language, score, traffic light; sortable; filters for period, keywords, traffic light, language; real Q2 data.
- **History:** PR #47 (prototype on mock data). Closed 23:12 CET by the front-end owner while the real connection sat on `feat/present-judgements`.
- **Verified (read):** columns, sorting by score, period and keyword filter, on mock data.
- **Missing:** on `main` "läuft lokal mit echten Q2-Daten" is not met (`GET /api/judgements` → 404, run). No language filter on either branch. The traffic light is a coloured dot; the filter is a numeric range (level buttons on the branch).
- **Classification:** B. Left closed; real data arrives with the merge of the front-end branch.

#### #19 [closed] Detailansicht: Original, Begründung, Summary DE/FR
- **Verified (read):** link, reason, similar cases, DE and FR side by side.
- **Missing:** no collapsible full text anywhere; no copy or export on `main` (the branch has "Im Suva-Format öffnen"); the link is `#` in the mock.
- **Classification:** B. Left closed; the full-text view was untracked until now.

#### #20 [closed] Buttons: Summary on demand + weiteres Urteil hinzufügen
- **Objective:** "Summary erzeugen" for yellow/red; input field for a case number or URL that crawls, scores and summarises live ("Pflicht laut Challenge").
- **Verified:** the summary button exists (mock on `main`, real on the branch, run); loading and error states (read).
- **Missing:** the input field and its route do not exist on any branch (`POST /api/judgements`, `POST /api/add` → 404, run). The challenge text asks for the "possibility to summarize additional court judgements".
- **Classification:** B. Left closed; tracked by #70.

#### #21 [closed] Recall, Precision, F2 auf Q1/2026 + Schwellen-Sweep
- **History:** PR #57, review fixes in #61 (Claude Code, Fable 5.2).
- **Verified (run):** `results/eval.md` and both tables are reproduced byte for byte, also on a fresh-clone layout. An independent re-derivation from the vector files gives the same numbers: threshold ≥ 91.53, recall 74.2 % (23 of 31), precision 46.0 % (23 of 50), F2 0.661; cosine only: 95.31 / 138 selected / 29 found / 0.553. The eight missed judgements are all invalidity insurance.
- **Caveats:** the threshold is chosen on the measured quarter (the report says so); the measured score is `VectorDB.score_candidates`, which no triage or UI path uses; the "optional" comparison of embedding models compares scoring variants instead.
- **Classification:** A. Left closed.

#### #26 [closed] Summary-Datenbank: Summaries persistent speichern
- **History:** PR #44 (store by the PM with Claude Code), merged with the pipeline of #45.
- **Verified (run):** schema in `docs/SCHEMA.md`; batch and on-demand write through the store; versions `generated` → `edited` → `approved` are appended, never overwritten; after a restart on the same file the stored entry comes back without a model call.
- **Missing:** the UI on `main` never calls these routes (mock); the front-end branch does. Search is a substring match, not FTS5 (noted as open in `docs/SCHEMA.md`).
- **Classification:** A with minor gaps. Left closed.

#### #27 [closed, not planned] Feedback-Slider für Juristen
- **History:** closed as not planned, "Ersetzt durch #40". #40 was later deleted; #64 is the successor.
- **Verified (run):** nothing implemented: no table, no route on `main`; the front-end branch has a placeholder that answers `stored: false`.
- **Classification:** G, superseded by #64. Left closed.

#### #33 [closed] Ausgabe als Rangliste nach Score, Ampel nur als Färbung
- **Objective:** rank per judgement; dashboard sorted by rank with a "Rang" column; rows coloured; export of the ranking CSV and the recall list; summaries for the green June judgements.
- **History:** PR #38/#41 (export code). Closed 23:12 CET.
- **Verified (run):** contiguous ranks 1..2096; CSV with `Rang,Geschaeftsnummer,Score,Ampel,Begruendung`; recall list in Suva format. Since PR #63 both export files for Q2 are on `main`.
- **Missing:** no "Rang" column in the UI (the default order is score, which gives the same order); only a dot is coloured; no June summaries.
- **Conflict:** #33 names `results/ranking_q2_2026.csv`, #56 names `results/tables/ranking_2026-Q2.csv`; #33 and #56 want the top 30, the list on `main` holds all 116 green judgements.
- **Classification:** B. Left closed; the open points are in #23 and #56.

#### #35 [closed] ensure_period: automatischer Crawl + Ingest
- **Objective:** `ensure_period(date_from, date_to)` closes gaps; triage calls it before scoring; the UI calls it on a period change and shows progress.
- **History:** PR #58 (Antigravity / Gemini) says the calls from triage and UI are still open. PR #60 closed the issue and was merged six seconds after it was opened. Review fixes in #61.
- **Verified (run):** six of eight tasks. A second call makes 0 HTTP calls and takes 0.44 s without loading PyTorch; a partly covered span fetches only the gap; failed days are not marked and are retried; three threads and three processes at once make each request once; a start in the future is refused, an end in the future is cut to yesterday. A day counts as covered only after crawl and ingest both succeeded.
- **Missing:** "Triage ruft vor dem Scoring immer `ensure_period` auf" and "UI ruft … auf": nothing on `main` calls the function, so "Fertig wenn" (choose a period in the UI, judgements are loaded) is not met. The branch `triage` adds a `score_period` that calls it.
- **Classification:** B. Left closed; the UI part is #69.

#### #37 [closed] Vector-DB befüllen: Q4/2025, Q1/2026, Q2/2026 einbetten
- **Verified (run):** all ten items: three quarters embedded (1930 / 2004 / 2096), metadata equal to the raw files, no `/` in keys, 49 359 chunks each tied to its judgement, second run with 0 embeddings, survives a restart, counts equal to `data/raw` per quarter, both test queries answer.
- **Classification:** A. Left closed.

#### #49 [closed] Crawler -> Vector-DB: ensure_period ruft Crawl und Ingest auf
- **History:** PR #60, fixes in #61.
- **Verified (run):** on a fake site, seven days give 3 files = 3 vector-DB rows = 3 rows in `cases`; a joined case resolves through `case_lookup` and `case_numbers`; `vectordb` and `conn` can be injected; a second call makes 0 HTTP calls and embeds nothing; 19 offline tests pass.
- **Deviations:** the function lives in `crawl/ensure.py` and returns a `PeriodStatus`, not a dict.
- **Missing:** "Defekte oder unvollständige Dateien werden geloggt und gezählt, brechen den Lauf aber nicht ab": a record with a valid hash but without `decision_date` or `case_id` stops the run with a `KeyError`; a truncated file is fetched again but neither logged nor counted. The integration test exists and could not be run.
- **Classification:** A with these gaps. Left closed; the gap is in the tracking issue.

#### #50 [closed] Triage-Ergebnisse pro Zeitraum in SQLite speichern
- **Objective:** `triage.score_period` calls `ensure_period`, scores, and stores rows in a table `triage`; `triage.get_cases` returns the list for the UI; exports are built from it; manual overrides survive a rerun.
- **History:** no PR, no commit. Closed 27.11. 23:24 CET.
- **Verified (run, all refs at the start of the audit):** none of it existed. The branch `triage` now has `score_period` as a wrapper without storage and `get_cases` as a stub that raises `NotImplementedError`. No table `triage`, no `triage/store.py`, no tests for it, nothing in `docs/SCHEMA.md`.
- **Depends on / conflicts:** #51, #53, #56 and #64 were specified against this store. What was built instead is a JSON file read by `present/judgements.py`.
- **Classification:** D, closed without implementation. **Action:** left closed and recorded in the tracking issue. Not implemented here: it would be built across three unmerged lines of work, and whether the store or the files are the way forward is the team's decision.

#### #51 [closed] Batch-Summaries für alle grünen Urteile
- **Objective:** `summary.summarize(conn, case_id)`; `run_batch` reads green judgements from `triage.get_cases`; `AMPEL_GREEN` from the triage config; counters and a non-zero exit code; a result CSV per period.
- **History:** the three ticked boxes were done earlier in PR #44/#45. Closed 23:22 CET without further commits.
- **Verified (run):** none of the four open tasks exists on `main`. The documented command `summary.run batch --from … --to …` stops with "the following arguments are required: --triage". `GREEN = "gruen"` is hard-coded in `summary/run.py`. No `results/tables/summary_batch_*.csv`. The branch `triage` adds `summarize` and takes `GREEN` from the triage config.
- **Classification:** D for the open tasks, closed without implementation. Left closed; #68 covers the server-side run.

#### #53 [closed] UI an echte Pipeline anbinden (Q2-Daten statt Mock)
- **Objective:** every button triggers a real step through `/api/...`; new routes for the list, for scoring a period with progress, and for adding a judgement.
- **History:** implemented in part on `feat/present-judgements` (Claude Code); closed 23:13 CET although that branch has no pull request.
- **Verified on `main` (run):** nothing: `USE_MOCK = true`, the UI makes no HTTP call, no list route.
- **Verified on the branch (run):** summary create, edit as pending, approve, knowledge-base tab, `GET /api/judgements` and `GET /api/config`; all nine routes the UI calls answer 200.
- **Missing on the branch:** `POST /api/periods`, `GET /api/jobs/{id}`, add-a-judgement; the list reads a file given with `--triage`, not `triage.get_cases`; the status `generated` versus `approved` is not shown.
- **Classification:** B on the branch, nothing on `main`. Left closed; the missing routes are #69 and #70. **The branch needs a pull request** (section 8).

#### #62 [closed during the audit] Relevanz-Bewertung in zwei Apertus-Durchgängen mit Kalibrierung
- **History:** PR #63 (Claude Code, Fable 5.2), merged 01:09 CET, which closed the issue through "Closes #62" with three tasks and one done-when still unticked in its own text.
- **Verified (run, twice, by an auditor and again by hand):** replaying the stored answers through `triage.cli relevance` gives all seven committed files byte for byte (three score files, calibration, ranking, list, triage JSON). 197 tests pass. `eval.relevance_eval` runs without gateway and reproduces `results/relevance_eval.md`. Q2/2026: 410 candidates, 187 scored by Apertus, 116 green (39 accident, 76 invalidity, 1 military insurance), 29 of them in June, 30 yellow; the list is one line with 116 distinct case numbers. Hold-out: 27 of 31 forward (F2 0.726), 22 of 28 backward (F2 0.748), 49 of 59 pooled (F2 0.736); with factor 0.3 all 59, list 226, F2 0.638. No proprietary call, no new dependency; the score files hold no label field.
- **What the numbers do not show:** the 100 % is in-sample for the factor: 0.3 was chosen looking at both labelled quarters; choosing it on the calibration quarter alone gives 0.4, which finds 58 of 59 with a list of 206. The prompt states label rates of the evaluated quarters, and the candidate filter was chosen so that all 59 are inside. The printed interval "100,0 % bis 100,0 %" is a degenerate bootstrap; the exact 95 % lower bound for 59 of 59 is 93.9 %. The comparison with "wie gebaut" and with Urteilsradar cannot be checked: no baseline scores are in the repository. That the answers come from `apertus-v1-5-70b` cannot be proven offline.
- **Incorrect:** two defects that could replace the deliverable silently (finding 2 in section 1).
- **Minor:** the hard filter on 8C/9C/8F/9F leaves no trace of what it drops (`4A_106/2025`, a judgement Suva selected, would be invisible); `relevance` ignores `LLM_NAME`; the order of the stored precedents reveals Suva's selection of other judgements; one fixed-point iteration can alternate between two thresholds (no effect on the reported numbers).
- **Open from its own list:** results into the store (#50), summaries for the green judgements (#17, #56), feedback into the calibration (#64), the UI showing the result (front-end branch).
- **Classification:** A for the scoring, C for its robustness. **Action:** both defects repaired in PR `fix/62-protect-suva-list` (section 5).

### Open issues

#### #22 [open] Vergleich 70B vs 8B (Begründung + Summaries)
- **Objective:** five held-out judgements summarised by both models, blind test in the team, runtime per summary, table in `results/model_comparison.md`.
- **Verified:** nothing exists. #62 compares 8B and 70B for the triage score, which this issue explicitly excludes.
- **Classification:** D. Left open: it needs gateway runs and a blind test by people.

#### #23 [open] Export: Recall-Liste Q2/2026 + Juni-Summaries im Abgabeformat
- **Verified:** since PR #63, `results/recall_q2_2026.txt` holds 116 case numbers in Suva's format. No June summaries anywhere.
- **Missing:** "Format/Anzahl mit Suva geklärt (Top 5 oder Top 10?)" is a question for the partner; "auf sprintd hochgeladen" is for people.
- **Classification:** B for the list, F for the count. Left open.

#### #25 [open] Pitch Deck + Demo-Video
- **Classification:** D, work for people. Left open. `docs/PITCH.md` is still the pre-event outline.

#### #29 [open] Feedback-Effekt messen (Simulation)
- **Depends on:** "#40", which was deleted; today that is #64. Nothing stores feedback; the only hook is `VectorDB.set_reference_flags`.
- **Classification:** F, optional by its own text. Left open.

#### #30 [open] Durchsuchbare Wissensbasis: Volltext- und semantische Suche
- **Objective:** one search field over judgements and summaries; FTS5 for exact terms; semantic search over the vector DB; hits lead to the detail view; combinable with filters.
- **Verified (run):** the index side exists in `crawl/vectordb.py`: `fulltext_search` (FTS5 over case number, subject and text; "Art. 6 UVG" gives 63 hits), `similar_to_text` and `similar_to_case` (`8C_464/2025` → `8C_343/2025`, 0.981). Summaries: `store.search` (substring, all terms) behind `GET /api/knowledge-base`.
- **Missing:** no route and no UI for searching judgements; summaries are not in FTS5.
- **Incorrect:** on a fresh clone the full-text index was silently incomplete after the first new day (finding 8).
- **Depends on / conflicts:** semantic search embeds the query at runtime and so needs `sentence-transformers` and the e5 weights in the deployed image, which the container decision of #56 leaves open.
- **Classification:** B (index side) and D (API and UI). **Action:** index defect repaired in PR `fix/30-fulltext-index-complete`. Left open.

#### #31 [open] Deployment: Dockerfile + docker compose
- **Verified before the audit:** nothing existed.
- **Classification:** D. **Action:** implemented and tested in PR `feat/31-docker` (section 5). Two boxes stay open: the embedding model in the image (team decision, #56) and the `--eval` flag of a single pipeline command, which does not exist (#52 deleted). "Auf einem anderen Laptop" was tested as a fresh clone on the same machine only.

#### #32 [open] Juristen können Summaries bearbeiten und freigeben
- **Verified (run):** backend complete on `main` (`PUT …/summary` stores an `edited` version, `POST …/approve` releases it). The UI on the front-end branch saves and approves. A `PUT` with invalid JSON or emptied text answers 200 and stores nothing.
- **Missing:** the pop-up "andere Sprache anpassen"; what it should do is the open question of #65. Three decisions are asked in the issue comment (one step or two, search over pending edits, reject an emptied text).
- **Classification:** B. Left open, correctly.

#### #48 [open] Schnittstellen festlegen: Modul-Funktionen + zentrale Config
- **Objective:** four decisions, a function contract, stubs, central configuration without hard-coded paths.
- **Verified on `main`:** none of it: no decisions in `docs/DECISIONS.md`, `docs/ARCHITECTURE.md` empty, no stubs; `DEFAULT_*` folders in `crawl/vectordb.py`, CLI defaults and `GREEN` hard-coded.
- **In progress:** commit `2c29860` on the branch `triage` (01:03 CET, Claude Code, no pull request) records four decisions, fills `docs/ARCHITECTURE.md`, adds `triage/service.py` (`score_period` as a wrapper, `get_cases` and `apply_feedback` as stubs), `summary/service.py` (`summarize`), `configs/paths.json` and `config.py`. 208 tests pass there (run). It was not audited in depth. Two observations: decision 2 names `VectorDB.score_candidates` as stage 1, but `score_period` does not call it and #62 states that similarity no longer enters the list; and the entries are signed "Team (#48)", which the audit cannot confirm.
- **Classification:** B on that branch, nothing on `main`. Left open. The #59 pull request carries the same `EMBEDDING_MODEL` line in `.env.example`; git still reports a conflict in that file when both are merged, because the neighbouring lines differ, and the version of the #59 pull request is the one to keep. A second small conflict (one import line in `summary/api.py`) arises with the front-end branch.

#### #55 [open] Smoke-Test nach jedem Merge
- **Depends on:** the pipeline command (#52, deleted) for steps 3 and 4, and `GET /api/judgements` (unmerged branch) for step 5.
- **Classification:** F: two prerequisites are missing, so the script cannot be written as specified without inventing the pipeline command. Left open. The checks that work today are listed in section 10.

#### #56 [open] Finale Läufe, Exporte und Demo-Stand
- **Depends on:** #50–#55, two of them deleted, two closed without implementation.
- **Verified:** thresholds: `configs/triage.json` keeps 60/40, onto which the calibrated list threshold is mapped. Ranking and list for Q2 exist in `results/`; no summaries, no secured demo state. The container bullet duplicates #31.
- **Conflict:** "F2-bester Wert" here versus "höchster Recall" as configured (`list_factor` 0.3); "Top 30" here versus 116 on the list; `results/tables/` here versus `results/`; a final run with a pipeline command that does not exist.
- **Classification:** D, mostly work with the gateway and by people. Left open; the container part is covered by the #31 pull request.

#### #59 [open] Repo aufräumen / ergänzen
- **Verified:** the status marks in section 0 of the issue are accurate, with two exceptions: `src/<package>/eval/` exists since PR #57, and the done-criterion `git grep -nE "<[A-Z][^>]*>|TBD|…"` can never be empty, because `<YYYY-MM>` in `docs/SCHEMA.md`, `<Kanton>` in `prompts/style_de.md` and a path pattern in the triage README match it legitimately.
- **Classification:** B. **Action:** the mechanical part is done in PR `fix/59-llm-env-repo-hygiene` (section 5). Left open for everything that needs the team: report (including the mirror as data source), PDF, video, pitch, README project part, authors in `pyproject.toml`, requirements clean-up, the permission question for the Suva data.

#### #64 [open] Relevanz-Entscheidungen der Jurist:innen speichern
- **Verified (run):** the description is accurate for the front-end branch: `record_feedback` logs and answers `stored: false`; `adjustment` is always 0. On `main` there is no feedback route.
- **Depends on:** the front-end branch; a place to store triage state (#50 gap); the table draft sits in `summary/VORGEHEN_DB.md`, a working note that #59 wants deleted.
- **Classification:** D. Left open.

#### #65 [open] «Anpassen» pro Sprache: Funktion festlegen und umsetzen
- **Verified (run):** accurate: `onAdjust` is empty on the branch, the route answers 404.
- **Classification:** F, a question to the team (two readings, one of them is the pop-up of #32). Left open.

#### #66 [open] Kriterien mit Belegzitaten, Rechtsfrage und Merksatz pro Urteil
- **Verified:** boxes 1 and 2 (prompt, result fields, quote check with `exakt` / `ungefähr` / `fehlt`) are on `main` since PR #63, in `metadata.relevance`: 507 criteria on 187 judgements in the Q2 file, 397 exact, 95 approximate, 15 missing. Box 3 was not done: the list endpoint dropped the fields (run on the trial merge: 0 of 410 items had criteria).
- **Classification:** B. **Action:** box 3 implemented in PR `feat/66-criteria-to-ui` against the front-end branch (section 5). Can be closed when that branch and the pull request are on `main`.

#### #67 [open] Relevanz-Begründung auch auf Französisch
- **Verified (run):** accurate: `explanation.de == explanation.fr` for every item.
- **Depends on:** the prompts of #62, which write the reason now; a second Apertus call per judgement.
- **Classification:** D. Left open; needs gateway runs.

#### #68 [open] Zusammenfassungen serverseitig erzeugen statt aus dem Browser
- **Verified:** accurate. One search on the branch queues a summary for every relevant judgement from the browser; with the Q2 list that is 116 judgements with several Apertus calls each.
- **Classification:** F: the issue offers two solutions and asks for a choice. Left open.

#### #69 [open] Suche über einen Zeitraum ohne Triage-Ergebnisse
- **Verified (run):** accurate. The UI's default range (the last three months, today 28.8.–28.11.2026) returns an empty list, because the triage results cover April to June 2026. This is the first screen a judge sees.
- **Classification:** F for the solution (two options); the second option (`GET /api/config` returns the available range) is small. Left open; depends on the front-end branch.

#### #70 [open] Einzelnes Urteil per Geschäftsnummer hinzufügen
- **Verified (run):** accurate; no route on any branch. `crawl.fetch_reference_cases` exists as a building block but needs the decision date, as #53 already noted.
- **Classification:** D with one open design point (finding the date for a case number). Left open. It is a stated requirement of the challenge.

#### #71 [open] Tabs «Auswertung» und «So arbeitet das System»
- **Verified (run):** accurate; `/api/evaluation` → 404.
- **Classification:** D. Left open; depends on the front-end branch.

#### #72 [open] Belege zur Zusammenfassung und weitere Falldaten für die Detailansicht
- **Verified:** principle content and use for accident insurance exist in `metadata.relevance.principle` and `uv_use`; whether a single judge decided is in the score files (`single_judge`) but not in the triage output the UI reads; the other fields do not exist anywhere.
- **Classification:** F: "Felder … hinzufügen; das UI ergänzt die Anzeige" names no shape. Left open.

#### #73 [open] Geschäftsnummer in der API wie bger.ch schreiben
- **Verified (run):** accurate: every `caseNumber` on the branch carries a space; `summary_entry` on `main` sends the same form.
- **Classification:** D, small. Left open: two of its three tasks touch files that exist only on the front-end branch, and the branch owner is changing them.

#### #74 [open] Export im Suva-Format serverseitig (optional)
- **Classification:** D, optional; overlaps #23. Left open.

#### #75 [open] Relevance Threshold
- **Objective:** threshold from the minimum relevance of the known relevant cases; a lower bound for "medium" from the variance.
- **Verified:** the first bullet is what `list_factor` 0.3 does in effect: it is the setting at which all 59 known Suva judgements are on the list. The second bullet has no definition. The branch `triage` cites this issue as the source of the thresholds (its decision 3).
- **Classification:** G for bullet 1 (covered by #62), F for bullet 2. Left open with a note in the tracking issue.

#### #76 [open] Apertus thinking mode
- **Objective:** let the model reason before it assigns the score.
- **Conflict:** the thinking models do not support tool calls (`AGENTS.md`); whether they honour the strict JSON schema that #62 relies on is not known and needs a gateway test. The first pass of #62 already makes the model write evidence and reason before the score.
- **Classification:** F. Left open.

### Deleted issue numbers

| # | What it was, as far as it can be reconstructed | Still referenced by |
|---|---|---|
| 12 | Traffic-light thresholds in `configs/triage.json` (PR #38) | `configs/triage.json`, triage README, #62 |
| 28 | Lawyer feedback changes the ranking (PR #38, removed again in #41) | `crawl/vectordb.py`, tests, #62 |
| 40 | Traffic-light feedback instead of the slider (#27) | #29, #62, `docs/SCHEMA.md` |
| 52 | Pipeline command `python -m hack_apertus_coffee_exe.pipeline --from … --to … [--no-llm]` | #55, #56 |
| 54 | Feedback route `POST …/feedback` | #53, #56 |
| 1, 4, 24 | Unknown; the API answers 410 and nothing refers to them | – |

## 4. Previous agent work

Most pull requests name their coding assistant, as rule 5 asks; #42 and #44 do not, but the assistant is in `docs/MODELS.md` (D5).

| PR | Assistant | Verdict |
|---|---|---|
| #2 setup | Claude Code (Opus 5.5) | Correct at the time. The variable names of the organisers' template were not followed up: the smoke test kept reading `APERTUS_*` only. Repaired here. |
| #34, #38, #41 triage | Gemini (Antigravity), Claude Code | Sound core. Flawed: a single unusable answer stopped the run; a missing score counted as 0. Repaired here. Incomplete: Q2 was never run with this path. |
| #36, #39 crawl | Claude Code, Gemini | Sound: all 6053 files conform, cache and politeness verified by running. Incomplete as the PR says: the bger.ch path never ran against the real site. |
| #42–#45 summary, store, API | Claude Code (Opus 5.5) | Correct against a mocked model. The coherence check is narrower than #16 asks. The PR texts state honestly that no live call was tested. |
| #46 embeddings, vector DB | Gemini (Antigravity) | Sound: every item of #8 and #37 verified by running. Two hazards: the partial full-text index on a fresh clone (repaired here) and `--embedder tfidf` replacing the stored embedder of the default DB (open). |
| #47 front end | Claude | Correct as a prototype on mock data, and described as such. |
| #57, #61 evaluation and review fixes | Claude Code (Fable 5.2) | Correct: the report reproduces byte for byte and was re-derived independently. |
| #58, #60 `ensure_period` | Gemini (Antigravity) | The function is correct (gaps, retry and locking verified). #60 closed #35 although nothing calls the function; an incomplete record stops the run. |
| #63 relevance scoring | Claude Code (Fable 5.2) | Reproducible and carefully documented. Flawed in robustness: two ways to replace the deliverable silently, repaired here. The headline 100 % is in-sample for the factor. |
| `feat/present-judgements` (no PR) | Claude Code (Fable 5.2, Opus 5.5) | Correct for what it claims; its own audit became issues #64–#74, which match the code. Flaw: the committed demo data is heuristic output. |
| `triage` (#48, no PR) | Claude Code (Opus 5.5) | Tests pass; not audited in depth. One decision text does not match the code (see #48). |

Three issues were closed on the strength of work that was never merged or never written: #53 (unmerged branch), #50 and #51 (nothing).

## 5. Issues resolved during this audit

All changes are pull requests (#77–#82); nothing is merged. Each was developed in its own worktree from `origin/main` (or from the front-end branch), with the test suite run before and after. #78, #79, #81 and #82 were then reviewed by a second, independent pass that did not write them, and its findings are worked in; the same review of #77 and #80 was still running when this report was written.

| Pull request | Issue | What | Evidence |
|---|---|---|---|
| #77 `fix/62-protect-suva-list` → `main` | defects in #62 | If Apertus could not be asked for a judgement (no key, no connection, time-out), `relevance` writes nothing and exits with 2; a rerun asks only the missing ones; `--allow-failed` keeps the old behaviour. `score` exports to `ranking_<period>_score.csv` and `recall_<period>_score.txt`. | 207 tests pass (10 new; 7 of them fail on unfixed code). Replay through the real CLI: a complete run still gives all seven committed files byte for byte; without a key and with 20 % time-outs it exits with 2 and writes 0 files (before: three tracked files overwritten, list 116 → 80 with exit code 0). |
| #78 `fix/59-llm-env-repo-hygiene` → `main` | part of #59 | Smoke test reads `LLM_*` first, `APERTUS_*` as aliases, and no longer calls the gateway on import; `.env.example` and `AGENTS.md` use the required names. `.env.example` has its comments on their own lines, because Docker Compose reads a comment after an empty value as the value. Conflict marker and result-field table in `docs/SCHEMA.md`, challenge choice, numbering, licence line, GitHub template folders, five empty stub files, `pyproject.toml` description and licence. | 203 tests pass (6 new). Without a key the script exits with "Missing API key: set LLM_API_KEY …". A wheel builds with `License-Expression: Apache-2.0` and 12 dependencies. python-dotenv and `docker compose config` read the new `.env.example` identically. |
| #79 `fix/13-triage-survives-unusable-answer` → `main` | flaw in closed #13 | An answer that is not a usable decision raises `TriageAnswerError`; the judgement is shown in yellow for a manual check (score = yellow threshold, as the relevance scoring does) with a reason that says so; nothing is cached for it; the run ends with a warning that lists such judgements. Gateway errors still stop the run. | 203 tests pass (6 new, 10 sub-cases). The new tests cannot even be collected on unfixed code. |
| #80 `fix/30-fulltext-index-complete` → `main` | defect behind #30 | `fulltext_search` rebuilds the index once when it has fewer rows than the vector DB has judgements. | 198 tests pass (1 new, fails without the fix). On a copy of the real metadata with a two-row index: 6053 rows after the first search, 63 hits for "Art. 6 UVG". |
| #81 `feat/31-docker` → `main` | part of #31 | `Dockerfile`, `docker-compose.yml`, `.dockerignore`, README section "Deployment". Image from `requirements.txt` only (928 MB, no PyTorch); `data/` mounted; the port is published on `127.0.0.1` by default, because the app has no login. To be merged after #78 (see `.env.example` there). | Fresh clone without `.env` → `docker compose up --build` → container healthy, `GET /` 200. With networking disabled the UI and the stored-data routes still answer. Generating without a key answers 400 with a clear message. The summaries DB is created in the mounted folder. |
| #82 `feat/66-criteria-to-ui` → `feat/present-judgements` | box 3 of #66 | `GET /api/judgements` passes `question`, `rule` and `criteria` from `metadata.relevance` in the shape the UI already renders; the rule, which the scoring also appends to its reason, is not delivered twice. | 156 tests pass on that branch (4 new). With the Q2 file: 187 judgements with criteria, 507 criteria, 0 differences to the source, all 116 green judgements covered, the rule in none of the 124 explanations. Not checked in a browser. |
| `docs/issue-audit-2026-11-28` → `main` | – | This report. | – |

All six code changes together on top of `main` and the front-end branch: no conflict among them, 232 tests pass, and the replay still gives all seven Q2 result files byte for byte.

Issues closed by the audit: none. Issues consolidated: none; overlaps are listed in section 8.

## 6. Already implemented before this audit

Complete as specified, with the evidence in section 3: **#7** (cache: 0 requests on a second run), **#8** (ingest: persistence, metadata, cache and date filter verified on all 6053 judgements), **#14** (style files in the prompts), **#21** (evaluation reproduced byte for byte and re-derived independently), **#37** (vector DB filled and checked against `data/raw`).

Complete apart from minor gaps: **#3** (schema; Suva's answer on the date not recorded), **#5** (embeddings; chunking decision undocumented), **#9** (backfill; completeness against the source unverifiable offline), **#10** (reference flags; gap report not wired), **#11** (score; k never compared), **#15** (summary generation; no stored June summary to show), **#26** (summaries DB; search is substring only), **#49** (`ensure_period` integration; incomplete records stop the run).

During the audit: **#62** (relevance scoring; results reproducible, robustness defects repaired here).

## 7. Remaining backlog

**Open requirements of closed issues that no open issue tracked** (now in the tracking issue):

| From | Requirement | Note |
|---|---|---|
| #50 | Triage results in a store (`get_cases`, table `triage`) | Decision first: store or files. Stubs on the branch `triage` |
| #51 | Batch from the store, counters, exit code, result CSV | Follows #50 |
| #53 | `POST /api/periods` with progress | Close to #69 |
| #52 (deleted) | One pipeline command | Needed by #55, #56, #59, one box of #31 |
| #35 | A caller for `ensure_period` in triage and UI | `score_period` on the branch `triage`; UI part is #69 |
| #49 | Incomplete or corrupt records must not stop `ensure_period`, and must be counted | |
| #19 | Collapsible full text of the judgement; copy | |
| #18 | Language filter | |
| #16 | DE↔FR comparison of content; case number and date in the text | Needs a rule for cited judgements |
| #10 | Report of reference judgements missing from the crawl | Method exists, nothing calls it |
| #6, #3 | Name the mirror as data source in the report; try the bger.ch path once | Report is part of #59 |
| #5 | Document the chunking decision | Minor |
| #11 | Comparison of k | Minor |

**Hazards found and not repaired:** `crawl.vectordb --embedder tfidf` on the default DB replaces the stored embedder, after which `ensure_period` overwrites committed vectors; the plain evaluation and crawl commands rewrite tracked result files with other content; a day once crawled is never refreshed without `--refresh`; the lock of `ensure_period` has no time-out.

**Valid and open, waiting for people or the gateway:** #22, #23, #25, #56, #59 (documents), #67.

**Waiting for a decision:** #48 (confirm the decisions written on the branch `triage`), #65, #68, #69 (two options each), #72 (shape of the fields), #75 (second bullet), #76, the count of #23 (Suva), the embedding model in the image (#31, #56).

**Blocked by something that does not exist:** #55 (pipeline command, list route), #29 (feedback store).

**Blocked only by the unmerged front-end branch:** #64, #70, #71, #73, #74, and the UI parts of #30 and #32.

## 8. Dependency and roadmap overview

**Hard dependencies.**

```
PR for feat/present-judgements ─► real data in the UI ─► #66 (PR ready) ─► #72, #67
        │                                 │
        │                                 ├─► #69 default period ─► #70 add a judgement
        │                                 └─► #68 server-side summaries ─► #56 final run ─► #23 export
        └─► #73, #71, #74, UI of #30 and #32
decision "store or files" (#48, #50) ─► #64 feedback ─► #29
pipeline command (no issue) ─► #55 smoke test ─► #56
#31 container (PR ready) ─► start command needs --triage after the front-end merge
```

**Soft dependencies.** #22 before the report (the "runs with 8B on-prem" claim); #75 after the threshold decision; #76 after a gateway test of the thinking models; the documents of #59 after the final run.

**Conflicting requirements.**

| Topic | Positions |
|---|---|
| List length | #33, #56 and the challenge text: top 30. On `main`: every green judgement (116 for Q2). #23: "Top 5 oder Top 10?" |
| Threshold goal | #56 and #21: best F2. `configs/triage.json`: highest recall, with a statistically secured loss of F2. #75 and decision 3 on the branch `triage`: minimum of the known relevant cases. |
| Export location | #33: `results/`. #56 and `AGENTS.md`: `results/tables/`. On `main`: `results/`. |
| Triage storage | #48, #50: SQLite. Built: JSON file. Branch `triage`: stubs for the store. |
| Stage 1 | Decision 2 on the branch `triage`: vector similarity. #62 as built: similarity does not enter the list. |
| "Anpassen" | #65 reading 1 (refine with Apertus) versus reading 2 (align the other language, #32). |
| Default model | `AGENTS.md`, `docs/MODELS.md` R1: 8B. `relevance` and #56: 70B. |
| Container | #31 and #56 both specify it. |

**Overlaps.** Bullet 1 of #75 ⊂ #62. Boxes 1 and 2 of #66 ⊂ #62. #74 ≈ #23. The container bullet of #56 = #31. #68 ≈ the open tasks of #51. #69 ≈ `POST /api/periods` of #53.

**Recommended order until 14:30.**

1. Merge `fix/62-protect-suva-list` first: until then one wrong command replaces the committed list.
2. Open and merge a pull request for `feat/present-judgements` (conflicts: keep both packages in `pyproject.toml`, renumber one `D6` row in `docs/MODELS.md`, keep both comment blocks in `api.js`, take the branch's side in `script.js`). Start the server with `--triage results/triage_q2_2026.json`. Remove `data/processed/triage_2026-01.json` or label it as offline sample data.
3. Merge the other pull requests of this audit (#66 into the front-end branch before or after step 2).
4. Decide the list length and `list_factor` (one line in `configs/triage.json`), then run the summaries for the judgements that are shown (#56, #23).
5. Make the first screen non-empty (#69, small option).
6. Write the documents of #59 and #25 with the numbers of #62, and name the mirror as data source.
7. Everything else after the deadline.

## 9. Future architectural risks

- **No place for triage state.** Feedback, manual overrides, on-demand periods and server-side jobs all need to write per-judgement state. With a JSON file loaded at start-up, each of these will invent its own storage. Decide once: the `triage` table of #50, or a documented file convention.
- **Embedding at runtime versus a slim image.** Semantic search (#30) and crawling new periods from the UI (#69) embed text at runtime. The image built here has no PyTorch. Either those features stay out of the deployed app, or the image grows by the model and its runtime.
- **Automatic summaries scale with the list.** With `list_factor` 0.3 the list has 116 judgements for one quarter; the browser queue of the front-end branch would start about 350 Apertus calls for one search on a shared gateway.
- **Two score scales.** The UI, the configuration and the exports speak of 0–100 with 60/40; the calibrated probability of #62 is mapped onto that scale. Anything that compares scores across runs or feeds feedback back into them has to know which one it holds.
- **A hard candidate filter.** Only case numbers 8C, 9C, 8F and 9F reach Apertus. A relevant judgement of another division is not scored and leaves no trace.
- **Single-process backend.** `http.server` with one generation lock and SQLite is right for a demo and for one replica. It is the limit for the Azure bonus beyond one instance.
- **Crawl source.** If the mirror changes or lags, there is no tested path to the original.

## 10. Verification results

| Check | Result |
|---|---|
| Unit tests, `main` at `ad072f4` | 179 passed, 4 deselected (integration). Also with sockets blocked: no network attempt. |
| Unit tests, `main` at `7218ca5` | 197 passed, 4 deselected |
| Unit tests, front-end branch | 152 passed, 3 deselected |
| Unit tests, branch `triage` | 208 passed, 4 deselected |
| Unit tests, trial merge of `main` and the front-end branch | 205 passed, 4 deselected |
| Unit tests, the same with the six pull requests of this audit | 232 passed, 4 deselected |
| Lint (`ruff`, rules F and E9, run from an isolated environment) | `main`: one unused import each in `tests/test_ensure.py` and `triage/relevance_period.py`. Front-end branch: clean. |
| JavaScript syntax (`node --check`) | all files pass on `main`, on the branch and on the trial merge |
| Type check, CI | none configured |
| Build | `pip wheel .` succeeds on the #59 branch (metadata and package list inspected) |
| Secrets | no token-like string in tracked files or in the history of any ref; `.env` was never committed |
| Reproduction of `results/eval.md` and `results/relevance_eval.md` | byte-identical |
| Replay of the stored Apertus answers through `triage.cli relevance` | all seven Q2 result files byte-identical |
| Backend on the trial merge with the Q2 file | `GET /api/config` → 60/40; `GET /api/judgements` for June → 132 items, 29 green; default range of the UI → empty list; feedback → `stored: false`; adjust and evaluation → 404 |
| Container | built and started from a fresh clone; healthy; works with networking disabled; the fully merged tree in the container serves 410 Q2 judgements |

Not run: integration tests (`-m integration`), any call to the Apertus gateway, the UI in a browser.

## 11. Outstanding risks

**Could not be verified.**
- Any real Apertus behaviour: the quality of reasons and summaries, how often answers are unusable, that the stored answers come from the 70B model.
- The UI in a browser. Statements about it come from reading the JavaScript and from calling the routes it calls.
- The crawler against the real bger.ch, and the completeness of the three quarters against the source.
- Whether summaries or triage results exist on a teammate's machine.
- "Mit Suva klären" items, and the live demo of #20.
- The Azure path of the container: described, not tested.

**Needs a decision by people.**
- Permission for the Suva material in the public repository.
- List length and threshold goal (recall or F2).
- Store or files for triage results; whether the decisions on the branch `triage` are the team's.
- Whether the embedding model belongs in the image.
- What "Anpassen" does.

**Technical uncertainties.**
- The front-end branch and the branch `triage` are still changing; the merges were tested against their state at 01:10 CET.
- The fixes of this audit were tested offline with mocked or replayed model answers, not against the gateway.
- `main` moved during the audit and may move again; this report describes `7218ca5`.
