# Triage (`src/hack_apertus_coffee_exe/triage/`, import `hack_apertus_coffee_exe.triage`)

## High-Level Overview

**Triage** filters and ranks newly scraped Swiss Federal Supreme Court (`bger.ch`) judgements by how relevant they are to **Suva** (accident insurance under UVG/LAA, occupational diseases, accident-like bodily injuries, and related ATSG/LPGA procedure), and produces a clear legal explanation for why each case was selected or rejected.

### How the pipeline works

Triage learns what Suva lawyers care about from the **55 human-written Suva summaries** in `judgements/Export_*.html` and combines **embedding cosine similarity** with **Apertus v1.5** reasoning:

```text
Crawl JSON (metadata + judgement text)
   │
   ├──► 1. Signal & Language Detection
   │       Detect DE / FR / IT and extract Suva / UVG / LAA / 8C_ signals
   │
   ├──► 2. Stage 1 — Cosine Similarity against Suva Precedents
   │       Chunk judgement ─► Embed ─► Compare (cosine similarity) against
   │       language-matched summaries from judgements/*.html
   │       ─► Produces max / top-k similarity + top-k nearest precedents
   │
   ├──► 3. Stage 2 — Apertus v1.5 Core Evaluation
   │       Send judgement excerpt + metadata + keywords + top-k nearest Suva
   │       summaries as context to Apertus v1.5 (temperature=0)
   │       ─► Produces apertus_score (0–100), relevant (bool), reason, key_topics
   │
   └──► 4. Ranking, Ampel & Exports
           cosine_score = top-k mean cosine * 100; final_score = blend of
           apertus_score (llm_weight, default 75%) + cosine_score (or heuristic
           in --no-llm mode). Then
           rank 1..n and ampel gruen/gelb/rot are assigned from configs/triage.json
           ─► results/ranking_<period>.csv + results/recall_<period>.txt
```

Why two stages?
- **Stage 1 (Cosine Similarity)** handles long judgements via chunking, grounds the scoring in Suva's real historical selection of cases, and surfaces the most similar precedent summaries for explainability.
- **Stage 2 (Apertus v1.5)** satisfies the competition rule that Apertus performs the core classification/scoring task and generates the human-readable legal justification required by the UI.

---

## What Every Part Does

### `triage/__init__.py`
Exposes the public API (`TriagePipeline`, `TriageResult`, `load_crawl_items`, `load_reference_corpus`, `TfidfEmbedder`, `GatewayEmbedder`, `SentenceTransformerEmbedder`, `chunk_text`, `cosine_similarity_matrix`, `score_against_corpus`) so other workflow stages can import Triage cleanly.

### `triage/reference_corpus.py`
Loads Suva's positive precedent corpus from `judgements/Export_*.html`:
- `parse_summary_html()`: Uses Python's built-in `html.parser` (zero external dependencies) to extract the case title, normalized `case_id` (e.g., `8C_177/2025`), court chamber (`8C`, `9C`, `4A`), German summary (`Text DE`), French summary (`Text FR`), original `bger.ch` link (`Link zum Urteil`), and author (`Autor`).
- `ReferenceCase`: Immutable record with `text_for_language()` to return language-matched summary text (`de`, `fr`, or combined DE+FR).
- `load_reference_corpus()`: Reads and validates all `Export_*.html` files in `judgements/`.

### `triage/embeddings.py`
Handles text chunking, vectorization, and cosine-similarity scoring against the reference corpus:
- `chunk_text()`: Splits long court judgements into overlapping word chunks (default `350` words, `50`-word overlap) so key legal passages are not diluted by procedural boilerplate.
- `l2_normalize()` & `cosine_similarity_matrix()`: NumPy-based L2 normalization and safe cosine similarity matrix calculation.
- `TfidfEmbedder`: Default zero-dependency multilingual word + character n-gram (4–5 char) TF-IDF embedder. Works offline/air-gapped and fits deterministically on the reference summaries.
- `GatewayEmbedder`: Optional OpenAI-compatible `/embeddings` client for gateways or self-hosted vLLM/TEI servers.
- `SentenceTransformerEmbedder`: Optional lazy wrapper for local open-weights models (e.g., `BAAI/bge-m3` or `intfloat/multilingual-e5-small`).
- `build_reference_index()` & `score_against_corpus()`: Pre-embeds reference summaries and scores candidate chunks against every reference case (taking the strongest chunk match per precedent), returning `SimilarityScore` (`max_similarity`, `top_k_mean_similarity`, and the top-$k$ `ReferenceMatch` objects).

### `triage/llm.py`
Centralizes all Keera / Apertus v1.5 gateway communication:
- `GatewayConfig.from_env()`: Loads endpoint, model, and API key from `.env` or environment variables (`LLM_API_KEY` / `APERTUS_API_KEY` / `KEERA_API_KEY`, `LLM_BASE_URL` / `APERTUS_BASE_URL`, `LLM_NAME` / `APERTUS_MODEL`, `EMBEDDING_MODEL`).
- `discover_gateway_models()` & `probe_gateway_embeddings()`: Inspect available models on `/models` and test whether `/embeddings` is supported for a given model ID.
- `_select_judgement_excerpt()`: Keeps head (facts/parties) and tail (legal reasoning/ruling) up to `24,000` characters when a judgement exceeds prompt limits.
- `ApertusTriageClient`: Sends metadata, keyword hits, cosine similarity scores, top-$k$ Suva precedent summaries, and the judgement excerpt to **Apertus v1.5** (`apertus-v1-5-8b` by default, `temperature=0.0`), then parses the structured JSON decision (`TriageDecision`: `relevant`, `apertus_score`, `reason`, `key_topics`).

### `triage/date_filter.py`
Enforces a **hard time-range filter** on an explicit period (no rolling default, so runs are reproducible) so that no incoming judgement outside the window (or missing a valid date) is ever embedded or sent to Apertus. `decision_date` is read first (issue #3):
- `extract_judgement_date()`: Resolves publication dates from `metadata["date"]` (and aliases), `bger.ch` `aza://DD-MM-YYYY-...` URLs, or DE/FR/IT judgement header text.
- `DateRange`: Inclusive `[since, until]` window; `DateRange.from_period("2026-Q2")` (also `2026-06`, `2026`) and `label()` (`q2_2026`) for export file names.
- `check_date_in_range()` & `filter_items_in_date_range()`: Rejects out-of-range or dateless items before embedding (raising `OutOfDateRangeError` on single-item `score_item()` calls and recording `SkippedItem` entries on batch `score_items()`).

### `triage/pipeline.py`
Orchestrates ingestion, hard date filtering, scoring, blending, and sorting:
- `load_crawl_items()` & `_normalize_crawl_dict()`: Reads Crawl output from a single JSON object, a JSON array, or a directory of `.json` files, accepting either nested `metadata` dicts or flat fields and common text keys (`judgement_text`, `judgment_text`, `text`, `full_text`, `content`, `body`).
- `detect_language()`: Reads language from metadata or infers `de` / `fr` / `it` from lightweight lexical cues.
- `extract_keyword_hits()`: Flags Suva/accident-insurance signals (`Suva`, `UVG/LAA/LAINF`, `Unfall/Accident`, `Berufskrankheit`, `ATSG/LPGA`, `Körperschädigung`, and `Chamber:8C`).
- `TriagePipeline`: Applies the hard `DateRange` filter, drops reference summaries that would leak (case is being scored, or decided in/after the period), runs Stage 1 + optional Stage 2 (both through the cache), stores `cosine_score` and `final_score`, and returns `TriageResult` objects ranked 1..n with an `ampel`. `config.rank_by` picks which score drives rank and ampel.
- Crawl records' raw `content_html` is dropped on load, and only whitelisted metadata fields go into the Apertus prompt.

### `triage/config.py` + `configs/triage.json`
All tunables (issue #12): `rank_by`, `llm_weight`, `top_k`, `green_threshold` (60) / `yellow_threshold` (40) — **placeholders until the eval sweep (#21)** — `recall_top_n` (30), `export_dir`, `cache_dir`.

### `triage/cache.py`
Mirror of the crawler's data index in `data/processed/triage/` (`_index.json` + `<YYYY-MM>/<date>_<case>.npy|.json`). Chunk vectors are reused while the text SHA-256 and embedder fingerprint (name, chunking, TF-IDF vocabulary hash) match; Apertus decisions are reused while the request key (model, temperature, max_tokens, full prompt) matches. A changed text invalidates both. The TF-IDF vocabulary is fitted once on all 55 summaries (vocabulary only, no labels), so cached vectors stay valid across periods; leakage is handled at scoring time. Safe to delete.

### `triage/export.py`
Issue #33 deliverables: `ranking_<period>.csv` (`Rang, Geschaeftsnummer, Score, Ampel, Begruendung`) and `recall_<period>.txt` in Suva format (`8C_691/2024, 9C_612/2025, ...`, top `recall_top_n`).

### `triage/cli.py`
Command-line interface (`python -m hack_apertus_coffee_exe.triage.cli <command>`):
- `check-gateway`: Lists available models on the configured gateway and probes `/embeddings` if supported.
- `score`: Runs triage for a required period (`--period 2026-Q2` or `--since`/`--until`), writes the ranking CSV + recall list to `results/` and full JSON to `--output`. Also `--no-llm`, `--embedder`, `--model`, `--top-k`, `--config`, `--no-cache`, `--no-export`.
- `eval-corpus`: Runs a leave-one-out cosine similarity evaluation across the 55 Suva summaries in `judgements/`.

### `tests/test_triage.py`
Offline unit tests covering real HTML corpus parsing (`55` cases), chunking and cosine math, TF-IDF relevance ranking, JSON fence parsing, and end-to-end directory scoring with a mocked Apertus client.

---

## Crawl Input & Triage Output Format

**Expected Crawl input** (`.json` file with one object, a list of objects, or a folder of `.json` files):

```json
{
  "case_id": "8C_123/2025",
  "date": "2025-11-10",
  "language": "de",
  "url": "https://search.bger.ch/...",
  "judgement_text": "..."
}
```

**Triage output** (JSON array ordered by `rank`):
- `case_id`, `rank` (1 = best), `ampel` (`gruen`/`gelb`/`rot`), `relevant` (`true` when green), `rank_score` (score that decided rank/ampel, per `rank_by`)
- `final_score`, `cosine_score` (both `0`–`100`), `relevance_reason` (`str`), `similar_cases` (top-k)
- Aliases kept for PR #34 consumers: `relevance_score` (= `final_score`), `reason`, `top_reference_matches`
- `cosine_max_similarity`, `cosine_topk_similarity`, `apertus_score` (`float | null`)
- `keyword_hits`, `key_topics`, `metadata`, `language`, `embedder`, `llm_model`, `judgement_date`

---

## Quick Commands

```bash
# Run offline unit tests
python3 -m pytest -q tests/test_triage.py tests/test_triage_issues.py

# Check Keera gateway models
.venv/bin/python -m hack_apertus_coffee_exe.triage.cli check-gateway

# Evaluate leave-one-out cosine similarity on the 55 Suva reference summaries
python -m hack_apertus_coffee_exe.triage.cli eval-corpus

# Offline triage for Q2/2026 (TF-IDF + keywords) -> results/ranking_q2_2026.csv, results/recall_q2_2026.txt
python -m hack_apertus_coffee_exe.triage.cli score --input data/raw --period 2026-Q2 --no-llm

# Full triage with Apertus v1.5, also writing the full JSON
.venv/bin/python -m hack_apertus_coffee_exe.triage.cli score --input data/raw --period 2026-Q2 --output results/tables/triage_q2_2026.json

```
