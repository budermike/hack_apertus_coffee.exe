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
`summary/api.py`. Apertus runs behind the configurable gateway (`APERTUS_BASE_URL`);
the embedding model (`EMBEDDING_MODEL`, open weights) runs locally.

## Components

| Stage | Folder | Stores | Notes |
|---|---|---|---|
| Crawl | `src/hack_apertus_coffee_exe/crawl/` | `data/raw/`, Vector-DB | `crawl.py`, `ensure.py`, `vectordb.py` |
| Triage | `src/hack_apertus_coffee_exe/triage/` | triage results (`results/`, later SQLite `triage`) | contract in `triage/service.py` |
| Summary | `src/hack_apertus_coffee_exe/summary/` | Summaries-DB | contract in `summary/service.py`, store in `store.py` |
| Present | `summary/api.py`, `src/presentation/` | – | backend routes and UI |
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
| `triage` | `apply_feedback(conn, case_id, ampel, *, comment=None, user) -> dict` | stub (#64; thresholds #75) | feedback row, override, reference flag, new score / rank | `POST /api/judgements/{id}/feedback` |
| `summary` | `summarize(conn, case_id) -> dict[str, Summary]` | ✅ wrapper (#48) around `run.summarize_case` | DE + FR in `summaries`, generated only if none stored | batch, `POST …/summary` |
| `summary.store` | `get_case`, `get_display` / `get_pending_edit` / `get_history`, `save_edit` / `approve`, `search` | ✅ | read, edit, approve, search summaries | API |

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
| `.env` (template `.env.example`) | secrets and environment: `APERTUS_API_KEY`, `HF_TOKEN`, `APERTUS_BASE_URL`, `APERTUS_MODEL`, `EMBEDDING_MODEL`, `SUMMARY_DB_PATH` | `hack_apertus_coffee_exe.config.env(...)`, `triage.llm.GatewayConfig.from_env()` |
| `configs/paths.json` | shared folders (`raw_dir`, `vectordb_dir`, `judgements_dir`, `vectordb_report`) and the default period | `hack_apertus_coffee_exe.config.load_paths()` |
| `configs/crawl.json` | crawl source, period, `out_dir`, source settings | `crawl` |
| `configs/triage.json` | thresholds (derived per #75), weights, `label_periods`, `list_factor`, `cache_dir`, `export_dir` | `triage.config.load_config()` |

No paths or thresholds are hard-coded in modules; defaults live only in `configs/*.json` and
`.env.example`.

## Open questions
- SQLite table `triage` and its schema, written by `score_period`, read by `get_cases` (#50).
- Lawyer feedback: table `feedback`, override and reference flags (#64).
- Thresholds derived from the data (#75).
- A period without triage results in the UI (#69).
