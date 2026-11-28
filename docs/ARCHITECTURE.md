# Architecture

Interfaces and data flow of the solution (issue #48). Decisions behind it: `docs/DECISIONS.md`
(entries "#48 decision 1–4").

## Data flow

```
bger.ch / entscheidsuche.ch
        │  crawl.ensure_period (crawl + ingest, missing days only)
        ▼
data/raw/<YYYY-MM>/*.json ──────────────► Vector-DB  data/processed/vectordb/
  one JSON per judgement                    metadata + aliases (source of truth),
                                            e5 embeddings, FTS5 full text
        │                                          │
        │                 Stage 1: VectorDB.score_candidates (similarity to earlier Suva cases)
        ▼                                          ▼
triage.score_period ── Stage 2: Apertus, two passes + calibration (#62/#63) ──► triage results
        │                                                                         (rank, score, ampel,
        │                                                                          reason, criteria)
        ▼  green judgements automatically, others on request
summary.summarize ── Apertus: facts → DE / FR → check ──► Summaries-DB  SUMMARY_DB_PATH
                                                            summaries (versions), cases,
                                                            case_numbers, later triage, feedback
        │
        ▼
summary/api.py  (/api/..., stdlib http.server)  ◄──►  UI  src/presentation/ (JavaScript)
```

The UI never talks to Python modules or a database directly, only to `/api/...` in
`summary/api.py`. Apertus runs behind the configurable gateway (`LLM_BASE_URL`);
the embedding model (`EMBEDDING_MODEL`, open weights) runs locally.

Two things happen when the list is served, not when it is scored (`present/judgements.py`):
the stored triage results are read from `results/triage_<period>.json` and
`data/processed/triage_periods/*.json`, and the lawyers' relevance decisions are learned
from (`triage/feedback_learning.py`): one correction per legal area and criterion on top
of the unchanged calibration, so judgements similar to a decided one move with it. The
files in `results/` are never changed by that.

## Components

| Stage | Folder | Stores | Notes |
|---|---|---|---|
| Crawl | `src/hack_apertus_coffee_exe/crawl/` | `data/raw/`, Vector-DB | `crawl.py`, `ensure.py`, `vectordb.py` |
| Triage | `src/hack_apertus_coffee_exe/triage/` | triage results (`results/`, later SQLite `triage`) | contract in `triage/service.py` |
| Summary | `src/hack_apertus_coffee_exe/summary/` | Summaries-DB | contract in `summary/service.py`, store in `store.py` |
| Present | `summary/api.py`, `present/`, `src/presentation/` | – | backend routes, judgement list for the UI, period jobs, UI |
| Evaluation | `src/hack_apertus_coffee_exe/eval/` | `results/tables/`, `results/*.md` | metrics only, no LLM judge |

## Contract

Each module offers exactly these functions; other modules and the backend call only these.

| Module | Function | Status | Delivers | Used by |
|---|---|---|---|---|
| `crawl` | `ensure_period(date_from, date_to, *, source=None, refresh=False, …) -> PeriodStatus` | ✅ (#35, #49) | missing days crawled into `data/raw/`, ingested into the Vector-DB, `cases`/`case_numbers` filled; counts | `score_period`, pipeline |
| `crawl.vectordb` | `VectorDB.get` / `query(date_from, date_to, …)` | ✅ | metadata of one judgement / of a period | `get_cases`, API |
| `crawl.vectordb` | `VectorDB.score_candidates(date_from, date_to, …)` | ✅ | similarity score against earlier references (no leak) | `score_period` |
| `crawl.vectordb` | `VectorDB.similar_to_case` / `fulltext_search` | ✅ | similar judgements / FTS5 search (e.g. "Art. 6 UVG") | triage reason, style examples, UI search |
| `crawl.vectordb` | `VectorDB.set_reference_flags(id, is_reference=…, is_negative_reference=…)` | ✅ | judgement marked as positive / negative reference | `apply_feedback` |
| `triage` | `score_period(date_from, date_to, *, model=None) -> dict` | ✅ wrapper (#48) around `relevance_period.run` (#63) | runs `ensure_period`, then scores the period; returns period, ensure status, candidates, calibration, results | pipeline, `POST /api/periods` |
| `triage` | `get_cases(conn, date_from, date_to, *, ampel=None) -> list` | stub (#50) | judgements with displayed score, rank, ampel, `summarized` from SQLite `triage` | `GET /api/judgements` |
| `triage` | `apply_feedback(conn, case_id, ampel, *, comment=None, user) -> dict` | stub; the running system does it differently, see next two rows | feedback row, override, reference flag, new score / rank | – |
| `summary.store` | `save_feedback`, `get_latest_feedback`, `feedback_examples` | ✅ (#64) | the lawyer's decision per judgement, appended to table `feedback` | `POST /api/judgements/{id}/feedback`, list, precedent pool of the next scoring run |
| `triage.feedback_learning` | `FeedbackLearner.learned(decisions, rows) -> Learned` | ✅ (#29, corrections of 28.11.) | corrections per area and criterion learned from all decisions; `Learned.score(row)` | `present.judgements.JudgementList.items`, `GET /api/config` (`learning`) |
| `summary` | `summarize(conn, case_id) -> dict[str, Summary]` | ✅ wrapper (#48) around `run.summarize_case` | DE + FR in `summaries`, generated only if none stored | batch, `POST …/summary` |
| `summary.store` | `get_case`, `get_display` / `get_history`, `replace_edit` / `discard_edit`, `search` | ✅ | read, edit (one edited version per language, no approval), reset to the generated text, search | API |
| `summary.store` | `save_edit` / `approve` / `get_pending_edit` | ✅, no longer used by the UI | every edit a new version, shown after approval (flow until 27.11.) | `POST …/summary/approve` |

Callers import from the package: `hack_apertus_coffee_exe.triage.score_period`,
`hack_apertus_coffee_exe.summary.summarize`.

### Rules
- **`case_id`** is accepted in every spelling (`8C_7/2025`, `8C_7_2025`) and resolved through the
  Vector-DB (`case_lookup`); the Summaries-DB resolves joined cases through `case_numbers`.
- **Connections:** `conn` comes from `summary.store.connect()`, the Vector-DB from **one** shared
  `VectorDB(...)` instance. No other module opens its own database connections.
- **No LLM calls when reading:** reading functions never call the LLM; only `score_period` and
  `summarize` may.

## Configuration

| Where | What | Read with |
|---|---|---|
| `.env` (template `.env.example`) | secrets and environment: `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_NAME` (names required by the organisers; `APERTUS_*` are read as aliases), `HF_TOKEN`, `EMBEDDING_MODEL`, `SUMMARY_DB_PATH` | `hack_apertus_coffee_exe.config.env(...)`, `triage.llm.GatewayConfig.from_env()` |
| `configs/paths.json` | shared folders (`raw_dir`, `vectordb_dir`, `judgements_dir`, `vectordb_report`) and the default period | `hack_apertus_coffee_exe.config.load_paths()` |
| `configs/crawl.json` | crawl source, period, `out_dir`, source settings | `crawl` |
| `configs/triage.json` | thresholds (derived per #75), weights, `label_periods`, `list_factor`, `cache_dir`, `export_dir` | `triage.config.load_config()` |

No paths or thresholds are hard-coded in modules; defaults live only in `configs/*.json` and
`.env.example`.

## API routes (`summary/api.py`)

| Route | Does |
|---|---|
| `GET /api/config` | thresholds, period with stored results, what was learned from decisions |
| `GET /api/judgements?from&to&lang` | judgement list with score, reason, criteria and quotes |
| `POST /api/periods/ensure`, `GET /api/periods/status` | fetch and score a period without stored results (background job) |
| `GET /api/judgements/{id}/original` | the stored judgement text as an HTML page |
| `POST /api/judgements/{id}/reason-fr` | French version of reason, question and rule (Apertus, cached) |
| `POST /api/subjects/localize`, `POST /api/judgements/{id}/localize` | subject, quotes under the criteria and the basis of the summary in the language of the UI (`present/translation.py`): carried over by Apertus in legal wording, not word for word, once, then stored; a carried-over quote is marked and the original stays one click away |
| `POST /api/judgements/{id}/feedback` | store the lawyer's relevance decision |
| `GET`, `POST /api/judgements/{id}/summary` | stored summary; `POST` generates German and French first if missing |
| `PUT /api/judgements/{id}/summary` | save an edit; the other language follows through Apertus |
| `DELETE /api/judgements/{id}/summary/edit` | back to the generated text |
| `POST /api/summaries/queue`, `GET /api/summaries/status`, `DELETE /api/summaries/queue/{id}` | summaries of the relevant judgements, generated on the server one after the other |
| `GET /api/knowledge-base?q` | search over all stored summaries |
| `GET /api/evaluation`, `POST /api/judgements/add`, `POST …/summary/approve` | still served, no longer used by the UI since 28.11. |

## Deployment

One container holds backend and UI (`Dockerfile`, `docker-compose.yml`); everything is set
through environment variables; state lives in the mounted folder `data/`. Commands for
local use, on-premise / air-gapped and Azure are in `README.md` ("Deployment").

| Dependency | Build time | Runtime |
|---|---|---|
| Apertus v1.5 endpoint, OpenAI-compatible (`LLM_BASE_URL`) | – | yes; inside the network for on-premise and air-gapped use |
| Python packages | PyPI | – |
| Embedding model (open weights) | Hugging Face, once | local; only when new judgements are taken into the Vector-DB |
| entscheidsuche.ch / bger.ch | – | only to fetch new judgements |
| Proprietary AI services | none | none |

- **On-premise, air-gapped, Swiss cloud:** all three work with the same image. Without any
  network the stored period is fully usable (list, reasons, summaries, editing, decisions,
  search, stored judgement texts); verified with `docker run --network none`.
- **Azure (bonus, not tested):** no provider-specific code; container registry, one
  container with a persistent volume for `data/`, the three `LLM_*` variables as secrets.
- **Not in the image:** PyTorch and `sentence-transformers`. Taking a new period into the
  Vector-DB therefore has to happen outside the container; the result is copied in with `data/`.

## Open points (28.11.2026)

- Resolved since the first version of this file: lawyer feedback (#64, learned from since
  #98), thresholds from the data (#75, `triage/calibration.py`), a period without triage
  results (#69, #91, `present/period_jobs.py`).
- The SQLite table `triage` (#50, `get_cases`) was never built: triage results stay JSON
  files, read by `present/judgements.py`.
- Reusing scored judgements across overlapping periods (#96).
- A new period cannot be ingested inside the container (see Deployment).
