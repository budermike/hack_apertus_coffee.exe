# Decision log

Short entries, newest on top. Format: date/time · decision · why · who.

## Challenge choice
- Options: ti&m Trust Room / Suva legal summaries / Digitec Galaxus image classification
- Criteria: fit with Apertus, feasibility in ~24 h, team skills, demo impact, sponsor clarity
- Decision: <TBD>

## Decisions
- 2026-11-27 · Crawler (`crawl/crawl.py`) has two sources: `bger` (official, final target) and `entscheidsuche` (open mirror, same 2096 judgements for Q2/2026). · bger.ch's bot protection (Incapsula) blocked us during development; the mirror lets us test end to end now. Switch with `"source"` in `configs/crawl.json` or `--source bger`. · Abi + Claude Code
- 2026-11-27 · Unit of crawling and caching is the day: `data/raw/_index.json` lists every completely saved day; one JSON per judgement in `data/raw/<YYYY-MM>/`. · bger.ch shows at most 90 hits per query, so a span must be split anyway; the index lets a second run make no requests (#7) and is what `ensure_period` (#35) needs. Caveat: judgements published later for an already covered day are only found with `--refresh`. · Abi + Claude Code
- 2026-11-27 · Summary in two steps (facts JSON, then DE and FR written separately from the facts, the cited consideration passages and 2 Suva examples of the same language). · FR must not be a translation (challenge); the facts step keeps DE and FR consistent. · Mike + Claude Code
- 2026-11-27 · Consistency check (#16) is rule-based, not a second model call: cited considerations and articles must exist in the judgement, the closing paragraph must match the operative part, DE and FR must cite the same. One regeneration with the errors as feedback, then flag in the UI. · Reproducible, cheap, catches the typical hallucinations (invented E. numbers, articles, wrong outcome). Outcome detection checked on all 6030 crawled judgements: not detected for 39 (0.6 %), for 8C/9C 2 of 1084. · Mike + Claude Code
- 2026-11-27 · Summaries stored in SQLite (`data/processed/summary/summaries.sqlite`), one row per case, language and version; edits add a row (`status` edited/approved). · #26: central store with history, stdlib only, no new dependency. · Mike + Claude Code
- 2026-11-27 · Summary reuses triage's `GatewayConfig`, reference corpus and TF-IDF embedder (`hack_apertus_coffee_exe.triage`) instead of copying them. · One place for gateway config and the Suva reference data. · Mike + Claude Code
