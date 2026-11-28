# Architecture

How the solution is built: data flow, modules, interfaces, configuration, API and deployment.
Why it is built this way: `docs/DECISIONS.md`.

## Data flow

```
bger.ch / entscheidsuche.ch
        │  crawl.ensure_period (crawl + ingest, missing days only)
        ▼
data/raw/<YYYY-MM>/*.json ──────────────► Vector-DB  data/processed/vectordb/
  one JSON per judgement                    metadata, e5 embeddings, FTS5 full text
        │                                   (read by the analyses in eval/ and notebooks/)
        ▼
triage (relevance_period.run) ── Apertus 70B, two passes per candidate ──► calibration ──► triage results
        │   precedents: earlier Suva cases picked by keyword similarity        results/triage_<period>.json
        │   (relevance.fingerprint)                                            (rank, score, ampel,
        │                                                                       reason, criteria, quotes)
        ▼  relevant judgements queued after a search, others on request
summary (generate.Summarizer) ── Apertus 70B: core question → DE / FR → check ──► Summaries-DB  SUMMARY_DB_PATH
                                                                                   summaries (versions), cases,
                                                                                   case_numbers, feedback
        │
        ▼
summary/api.py  (/api/..., stdlib http.server)  ◄──►  UI  src/presentation/ (JavaScript)
```

The UI talks only to `/api/...` in `summary/api.py`, never to Python modules or a database.
Apertus runs behind the configurable gateway (`LLM_BASE_URL`): the summaries use
`SUMMARY_MODEL` (default `apertus-v1-5-70b`), the relevance scoring `apertus-v1-5-70b`
unless `--model` is given. The embedding model (`EMBEDDING_MODEL`, open weights) runs locally;
`crawl.ensure` embeds newly fetched judgements with it into the Vector-DB.

New judgements arrive in two ways:
- **`crawl.ensure`** (pipeline): all divisions into `data/raw/`, embedded into the Vector-DB.
- **Web app, period without results** (`present/period_jobs.py`): only the social-law divisions
  into `data/processed/triage/`, scored by Apertus right away, not embedded.

When the list is served (`present/judgements.py`), the stored triage results are read from
`results/triage_<period>.json` and `data/processed/triage/periods/*.json`, and the lawyers'
relevance decisions are learned from (`triage/feedback_learning.py`): one correction per legal
area and criterion on top of the unchanged calibration, so judgements similar to a decided
one move with it. The files in `results/` are never changed by that.

## Modules

| Stage | Folder | Stores | Main files |
|---|---|---|---|
| Crawl | `src/hack_apertus_coffee_exe/crawl/` | `data/raw/`, Vector-DB | `crawl.py` (sources, `fetch_single_case`), `ensure.py` (missing days, ingest), `vectordb.py` |
| Triage | `src/hack_apertus_coffee_exe/triage/` | `results/triage_<period>.json`, `data/processed/triage/`, `results/tables/relevance_scores_*.json` | `relevance.py` (prompts, two passes), `relevance_period.py` (one period), `calibration.py`, `feedback_learning.py`, `cli.py` |
| Summary | `src/hack_apertus_coffee_exe/summary/` | Summaries-DB | `generate.py` (`Summarizer`), `align.py`, `check.py`, `examples.py`, `prompts.py` with the files in `prompts/`, `store.py`, `run.py`, `api.py` |
| Present | `src/hack_apertus_coffee_exe/present/`, `src/presentation/` | `data/processed/triage/` (periods fetched from the UI) | judgement list and config for the UI, background jobs for new periods, translation of subjects and quotes; the web UI |
| Evaluation | `src/hack_apertus_coffee_exe/eval/`, `notebooks/` | `results/tables/`, `results/*.md`, `results/figures/` | metrics of the relevance list; for the summaries an Apertus 70B judge with a fixed rubric and a rule-based style measure; not part of the running system |

## Interfaces

The functions the stages and the backend call on each other.

| Module | Function | Delivers | Called by |
|---|---|---|---|
| `crawl` | `ensure_period(date_from, date_to, *, source=None, refresh=False, …) -> PeriodStatus` | missing days crawled into `data/raw/`, ingested into the Vector-DB, `cases` / `case_numbers` filled | `present/period_jobs.py` |
| `crawl` | `fetch_single_case(case_id, raw_dir)` | one judgement by case number | `present/period_jobs.py` |
| `triage.relevance_period` | `run(period, label_periods, …)` | both Apertus passes for every candidate, calibration on the labelled periods, triage results | `triage.cli relevance`, `present/period_jobs.py` |
| `triage.feedback_learning` | `FeedbackLearner.learned(decisions, rows) -> Learned` | corrections per area and criterion learned from all decisions; `Learned.score(row)` | `present.judgements.JudgementList`, `GET /api/config` |
| `summary` | `summarize(conn, case_id) -> dict[str, Summary]` | DE + FR summary, generated only if none is stored | `summary.run batch`, `POST …/summary`, summary queue |
| `summary.generate` | `Summarizer.summarize(judgement)`, `align_edit(target_lang, target_text, source_lang, source_text, source_before, judgement, …)` | DE + FR summary (`SummaryResult`); a lawyer's edit of one language carried over to the other (`Alignment`: text and notes; logic in `summary/align.py`) | `summary.run.summarizer_factory`, `PUT …/summary` |
| `summary.store` | `get_case`, `get_display` / `get_history`, `replace_edit` / `discard_edit`, `search` | read, edit (one edited version per language), reset to the generated text, full-text search | API |
| `summary.store` | `save_feedback`, `get_latest_feedback`, `feedback_examples` | the lawyer's decision per judgement, appended to table `feedback` | `POST …/feedback`, judgement list, precedents of the next scoring run |

### Rules
- **`case_id`** is accepted in every spelling (`8C_7/2025`, `8C_7_2025`). The judgement is found
  by its file name in `data/raw/`; joined cases are resolved through `case_numbers` in the
  Summaries-DB.
- **Connections:** `conn` comes from `summary.store.connect()`; the Summaries-DB is only
  accessed through `summary/store.py`.
- **Apertus calls:** only scoring (`relevance_period`), summarising (`Summarizer`,
  `align_edit`) and translating subjects and quotes (`present/translation.py`) call the model;
  reading and listing never do.
- **Knowledge base:** generated summaries are never changed or deleted; an edit is stored next
  to the generated text.

## Configuration

| Where | What | Read with |
|---|---|---|
| `.env` (template `.env.example`) | `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_NAME` (names required by the organisers; `APERTUS_*` are read as aliases), `SUMMARY_MODEL`, `SUMMARY_DB_PATH`, `EMBEDDING_MODEL`, `HF_TOKEN` | `hack_apertus_coffee_exe.config.env(...)`, `triage.llm.GatewayConfig.from_env()` |
| `configs/paths.json` | shared folders (`raw_dir`, `vectordb_dir`, `judgements_dir`, `vectordb_report`) and the default period | `hack_apertus_coffee_exe.config.load_paths()` |
| `configs/crawl.json` | crawl source, period, `out_dir`, source settings | `crawl` |
| `configs/triage.json` | thresholds, `label_periods`, `list_factor`, `cache_dir`, `export_dir` | `triage.config.load_config()` |
| `prompts/` | prompts of the summaries: core question, writing DE / FR, style profiles, glossary | `summary.prompts`, `summary.generate.Summarizer` |

Folders, periods and thresholds come from `configs/*.json`, secrets and model choices from `.env`.

## API routes (`summary/api.py`)

| Route | Does |
|---|---|
| `GET /api/config` | thresholds, period with stored results, what was learned from decisions |
| `GET /api/judgements?from&to&lang` | judgement list with score, reason, criteria and quotes |
| `POST /api/periods/ensure`, `GET /api/periods/status` | fetch and score a period without stored results (background job) |
| `POST /api/judgements/add` | fetch and score one judgement by case number |
| `GET /api/judgements/{id}/original` | the stored judgement text as an HTML page |
| `POST /api/judgements/{id}/reason-fr` | French version of reason, question and rule (Apertus, stored) |
| `POST /api/subjects/localize`, `POST /api/judgements/{id}/localize` | subject, quotes and the basis of the summary in the language of the UI (Apertus, legal wording, stored after the first time) |
| `POST /api/judgements/{id}/feedback` | store the lawyer's relevance decision |
| `GET`, `POST /api/judgements/{id}/summary` | stored summary; `POST` generates German and French first if missing |
| `PUT /api/judgements/{id}/summary` | save an edit; the other language follows through Apertus |
| `DELETE /api/judgements/{id}/summary/edit` | back to the generated text |
| `POST /api/judgements/{id}/summary/approve` | release pending edits |
| `POST /api/summaries/queue`, `GET /api/summaries/status`, `DELETE /api/summaries/queue/{id}` | summaries generated on the server one after the other |
| `GET /api/knowledge-base?q` | full-text search over all stored summaries (`DELETE` answers 405) |
| `GET /api/evaluation` | evaluation figures for the UI |

## Deployment

One container holds everything, backend, UI and pipeline (`Dockerfile`, `docker-compose.yml`); everything is set
through environment variables; state lives in the mounted folder `data/`. Commands for local
use, on-premise / air-gapped and Azure are in `README.md` ("Deployment").

| Dependency | Build time | Runtime |
|---|---|---|
| Apertus v1.5 endpoint, OpenAI-compatible (`LLM_BASE_URL`) | – | yes; inside the network for on-premise and air-gapped use |
| Python packages | PyPI | – |
| Embedding model (open weights) | Hugging Face, once, into the image | local (CPU); when `crawl.ensure` embeds new judgements into the Vector-DB |
| entscheidsuche.ch / bger.ch | – | only to fetch new judgements |
| Proprietary AI services | none | none |

- **On-premise, air-gapped, Swiss cloud:** all three work with the same image. Without any
  network the stored period is fully usable (list, reasons, summaries, editing, decisions,
  search, stored judgement texts).
- **Azure (not tested):** no provider-specific code; container registry, one container with a
  persistent volume for `data/`, the three `LLM_*` variables as secrets.
- **Everything in the image:** PyTorch (CPU build) and `sentence-transformers` with the embedding
  model downloaded at build time, so `crawl.ensure`, scoring, summaries and evaluation run in the
  same container as the web app (`docker compose exec app python -m …`).
