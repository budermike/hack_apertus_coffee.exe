# Decision log

Short entries, newest on top. Format: date/time · decision · why · who.

## Challenge choice
- Options: ti&m Trust Room / Suva legal summaries / Digitec Galaxus image classification
- Criteria: fit with Apertus, feasibility in ~24 h, team skills, demo impact, sponsor clarity
- Decision: <TBD>

## Decisions
- 2026-11-27 · Crawler (`crawl/crawl.py`) has two sources: `bger` (official, final target) and `entscheidsuche` (open mirror, same 2096 judgements for Q2/2026). · bger.ch's bot protection (Incapsula) blocked us during development; the mirror lets us test end to end now. Switch with `"source"` in `configs/crawl.json` or `--source bger`. · Abi + Claude Code
- 2026-11-27 · Unit of crawling and caching is the day: `data/raw/_index.json` lists every completely saved day; one JSON per judgement in `data/raw/<YYYY-MM>/`. · bger.ch shows at most 90 hits per query, so a span must be split anyway; the index lets a second run make no requests (#7) and is what `ensure_period` (#35) needs. Caveat: judgements published later for an already covered day are only found with `--refresh`. · Abi + Claude Code
- 2026-11-27 · Summaries DB (#26) is SQLite via stdlib `sqlite3`: tables `cases` (date + source URL), `summaries` (one row per case, language, version), `case_numbers` (all case numbers of joined cases). Path from `SUMMARY_DB_PATH`, default `data/processed/summaries.db`; `*.db` is git-ignored. Schema in `docs/SCHEMA.md`, code in `summary/store.py`. · Central, searchable store without a new dependency; runs on-premise / air-gapped. · Mike + Claude Code
- 2026-11-27 · Summary rows are append-only: never `UPDATE`/`DELETE`. Edit and approval each add a new version; `approved` is a copy of the `edited` text. · Full history for Suva; the original model output stays traceable. · Mike + Claude Code
- 2026-11-27 · A summary is generated at most once per case and language; batch and on-demand both go through `get_or_generate`, no regeneration. · Done criterion of #26 (restart shows all summaries without LLM call); saves gateway calls; regenerated text can never hide a lawyer's edit. · Mike + Claude Code
- 2026-11-27 · Model output (`generated`) counts as released without review; only human edits need approval. An edit always stores `model = 'human'` and `prompt_version = NULL` (enforced by a `CHECK`). UI shows newest `approved`, else `generated`; an open edit is shown as draft. · Keeps the flow short for the demo. Trade-off: unreviewed model text is in the knowledge base, so the UI should mark `generated` as such. · Mike + Claude Code
- 2026-11-27 · `coherence_ok` is set once when the summary is generated (part of the summary pipeline: summary compared with the judgement) and never changed; `NULL` for human edits. · Keeps rows append-only; no check on human text. · Mike + Claude Code
- 2026-11-27 · Joined cases (e.g. `1D_1/2025, 1D_2/2025`) get one summary under the first number; `case_numbers` maps every number to it (filled from crawl `case_ids`). · Duplicates would let edits and approvals drift apart; lookup still works with any number. · Mike + Claude Code
- 2026-11-27 · Triage feedback (manual traffic light, table `feedback`) is deferred until the traffic-light system is defined. Agreed so far: re-scoring may overwrite `model_score`, never a manual override; same colour = no-op, other colour = mean score of the cases currently in that colour. · Ampel thresholds not decided yet. · Mike + Claude Code
