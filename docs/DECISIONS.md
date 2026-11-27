# Decision log

Short entries, newest on top. Format: date/time · decision · why · who.

## Challenge choice
- Options: ti&m Trust Room / Suva legal summaries / Digitec Galaxus image classification
- Criteria: fit with Apertus, feasibility in ~24 h, team skills, demo impact, sponsor clarity
- Decision: <TBD>

## Decisions
- 2026-11-27 · Multilingual Embeddings (`embed.py`, issue #5) + Local Vector-DB & FTS5 (`crawl/vectordb.py`, issues #8, #9, #30, #37) use `intfloat/multilingual-e5-small` (MIT open-weights, 384-dim, DE/FR/IT, MPS/CPU) stored in SQLite (`metadata.sqlite3` with FTS5 full-text search and `is_reference` / `is_negative_reference` flags for #28/#40) + compressed NumPy archives (`vectors/*.npz`, `search_index.npz`) on Python 3.13. · Probing `https://gateway.keera.ch/api/v1/models` confirmed no `/v1/embeddings` endpoint is served (404). `multilingual-e5-small` gives strong cross-lingual DE/FR/IT semantic search (#30, #15, #21) while keeping all 6,030 judgements (~50k chunks × 384 dims) compact (~75 MB) and 100 % self-hosted / air-gapped. · Abi + Antigravity
- 2026-11-27 · Crawler (`crawl/crawl.py`) has two sources: `bger` (official, final target) and `entscheidsuche` (open mirror, same 2096 judgements for Q2/2026). · bger.ch's bot protection (Incapsula) blocked us during development; the mirror lets us test end to end now. Switch with `"source"` in `configs/crawl.json` or `--source bger`. · Abi + Claude Code
- 2026-11-27 · Unit of crawling and caching is the day: `data/raw/_index.json` lists every completely saved day; one JSON per judgement in `data/raw/<YYYY-MM>/`. · bger.ch shows at most 90 hits per query, so a span must be split anyway; the index lets a second run make no requests (#7) and is what `ensure_period` (#35) needs. Caveat: judgements published later for an already covered day are only found with `--refresh`. · Abi + Claude Code
