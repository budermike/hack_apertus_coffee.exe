# Triage (`triage/`)

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
   └──► 4. Final Ranking & Output JSON
           Blend apertus_score (75%) + cosine_max_similarity (25%)
           (or heuristic fallback in --no-llm mode), sorted descending
           ─► Ready for Summary and Present (UI)
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

### `triage/pipeline.py`
Orchestrates ingestion, scoring, blending, and sorting:
- `load_crawl_items()` & `_normalize_crawl_dict()`: Reads Crawl output from a single JSON object, a JSON array, or a directory of `.json` files, accepting either nested `metadata` dicts or flat fields and common text keys (`judgement_text`, `judgment_text`, `text`, `full_text`, `content`, `body`).
- `detect_language()`: Reads language from metadata or infers `de` / `fr` / `it` from lightweight lexical cues.
- `extract_keyword_hits()`: Flags Suva/accident-insurance signals (`Suva`, `UVG/LAA/LAINF`, `Unfall/Accident`, `Berufskrankheit`, `ATSG/LPGA`, `Körperschädigung`, and `Chamber:8C`).
- `TriagePipeline`: Precomputes language-specific reference indices (`de`, `fr`, `all`), runs Stage 1 (`score_against_corpus`) + optional Stage 2 (`ApertusTriageClient`), blends scores (`0.75 * apertus_score + 0.25 * (cosine_max_similarity * 100)` when LLM is enabled, or `_heuristic_relevance_score()` in `--no-llm` mode), and returns `TriageResult` objects sorted descending by `relevance_score`.

### `triage/cli.py`
Command-line interface (`python3 -m triage.cli <command>`):
- `check-gateway`: Lists available models on the configured gateway and probes `/embeddings` if supported.
- `score`: Runs triage on Crawl JSON input(s) and writes sorted results to stdout or `--output`. Supports `--no-llm`, `--embedder`, `--model`, `--top-k`.
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

**Triage output** (JSON array sorted descending by `relevance_score`):
- `case_id`, `relevance_score` (`0`–`100`), `relevant` (`bool`), `reason` (`str`)
- `cosine_max_similarity`, `cosine_topk_similarity`, `apertus_score` (`float | null`)
- `keyword_hits`, `key_topics`, `top_reference_matches`, `metadata`, `language`, `embedder`, `llm_model`

---

## Quick Commands

```bash
# Run offline unit tests
python3 -m unittest -v tests/test_triage.py

# Check Keera gateway models
.venv/bin/python -m triage.cli check-gateway

# Evaluate leave-one-out cosine similarity on the 55 Suva reference summaries
python3 -m triage.cli eval-corpus

# Run offline triage (TF-IDF cosine similarity + keyword signals only)
python3 -m triage.cli score --input path/to/crawl_output.json --output results/tables/triage.json --no-llm

# Run full triage with Apertus v1.5
.venv/bin/python -m triage.cli score --input path/to/crawl_output.json --output results/tables/triage.json
```
