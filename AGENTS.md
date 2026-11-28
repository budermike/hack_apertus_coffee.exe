# AGENTS.md — hack_apertus_coffee.exe

Primer for coding agents working in this repository. Read this fully before changing anything.

## Context

Hackathon project for **Hack Apertus Zürich** (27–28 Nov 2026). Submission deadline: **Sat 28 Nov 2026, 14:30** — time is short, so a small, working, well-tested core beats many half-finished features.

Project: Suva's legal specialists have to find the Federal Supreme Court judgements that matter for accident insurance among about 2000 published per quarter and summarise them in German and French; we crawl the judgements, let Apertus v1.5 rank them and explain their relevance, write the summaries in Suva's style, and show everything in a searchable web UI.
Challenge: Suva legal summaries (`challenge_description.md`).

Team workflow, roles and the submission checklist: see `CONTRIBUTING.md` and `docs/LEITFADEN.md`.

## Hard rules (competition rules — violating them disqualifies the team)

1. **Apertus v1.5 performs the core AI task** of the solution (e.g. the classification or summarisation itself). Never delegate the core task to another model.
2. **At runtime use only Apertus and open-weights models** for supporting tasks (embeddings, OCR, speech-to-text, reranking). Open-weights = weights are publicly downloadable and self-hostable.
3. **No proprietary AI APIs at runtime.** Never call api.openai.com, api.anthropic.com, Google/Gemini, Cohere, ElevenLabs etc. from project code. The `openai` Python package may be used **only as a client** with `base_url` set to the Apertus gateway (see below).
4. **Must be deployable** on-premise, air-gapped or on a Swiss sovereign cloud: no hard dependency on foreign closed services, no hard-coded external URLs except the configurable Apertus endpoint.
5. **Every model must be logged** in `docs/MODELS.md` (model, version, role, runtime or development, open-weights yes/no, licence). If you add or change a model, update that file in the same change. Mention in the PR if you (the agent) are a model used in development.

If a request would break one of these rules, stop and say so instead of implementing it.

## Apertus access

Configuration comes **only** from environment variables. The names are the ones the organisers require (`README.md`, "Environment"):

```
LLM_BASE_URL   # CSCS/Keera gateway endpoint
LLM_API_KEY    # team key — never commit, never print, never log
LLM_NAME       # model ID, default: apertus-v1-5-8b
```

The older names `APERTUS_BASE_URL`, `APERTUS_API_KEY` and `APERTUS_MODEL` are still read as aliases; `LLM_*` wins when both are set (`triage.llm.GatewayConfig.from_env`). One exception: `triage.cli relevance` does not read `LLM_NAME`; it uses `apertus-v1-5-70b` unless `--model` is given.

Available model IDs on the gateway:

| Model ID | Use |
|---|---|
| `apertus-v1-5-8b` | default, **supports tool calls** |
| `apertus-v1-5-70b` | higher quality, **supports tool calls**, slower |
| `apertus-v1-5-8b-thinking` | reasoning mode, **no tool calls** |
| `apertus-v1-5-70b-thinking` | reasoning mode, **no tool calls** |

- Wrap all model calls in one module (e.g. `src/<package>/llm.py`) so the model, endpoint and parameters are changed in one place.
- Make `temperature`, `max_tokens` and the model ID configurable. Set fixed seeds / `temperature=0` for evaluation runs.
- Handle timeouts, retries and rate limits; the gateway is shared infrastructure.

## Layout

- Reusable code lives in `src/<package>/` and is imported from notebooks, scripts and the demo. Do not copy logic into notebooks.
- `data/raw/` is **immutable**: never modify or overwrite files there. Write cleaned data to `data/processed/`, derived data for presentation to `data/synthesized/`.
- Outputs go to `results/figures/`, `results/tables/` or `results/reports/`.
- Every module in `src` gets a matching `tests/test_<module>.py`.
- Documentation: `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/MODELS.md`, `docs/LEITFADEN.md`.
- Each element of the workflow (Crawl, Triage, Summary and Present) get it's own folder.

> Package name: the package is `hack_apertus_coffee_exe` (`src/hack_apertus_coffee_exe/`), because `hack_apertus_coffee.exe` is not a valid Python identifier. `<package>` in this file means that folder.

## Setup & checks

We use **pip only. Do not use uv** (no `uv add`, `uv sync`, `uv run`, no `uv.lock`).

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # installs the package (editable), requirements.txt and the dev tools
cp .env.example .env                  # then fill in LLM_API_KEY
python src/keera_example.py           # smoke test against the Apertus gateway
pytest                                # unit tests (no network)
```

- Dependencies live **only** in `requirements.txt` and `requirements-dev.txt` (the second adds the editable install and `sentence-transformers` for the local embedding model), with exact pins (`package==x.y.z`). Do not add them to `pyproject.toml`.
- To add a dependency (after asking): `pip install <pkg>`, then add the line with the installed version from `pip freeze` to `requirements.txt`.

## Data, secrets and the public repo

- The repository is **public**. Never commit `.env`, API keys, tokens, customer/partner data or personal data.
- Data supplied by challenge partners goes into the repo **only if the partner allows it**. When unsure, keep it local and use synthetic or public sample data in the repo. The crawled Federal Supreme Court judgements in `data/raw/` are public and are tracked in git.
- Do not print secrets or full private documents into logs, notebooks or test output.

## Tests and evaluation

- Unit tests must **not** call the real gateway: mock the LLM client. Mark tests that need the network (`@pytest.mark.integration`) and keep them out of the default run.
- Evaluation is part of the deliverable. Evaluation code lives in `src/<package>/eval/`; datasets, metric, and results go to `results/tables/`. Report honest numbers, including failure cases.
- Reproducibility first: fixed seeds, pinned versions in `requirements.txt`, no hidden state in notebooks.

## Priorities

1. Compliance with the hard rules above.
2. A working end-to-end path (input → Apertus → output) before extra features.
3. Correctness and reproducibility.
4. Readability over cleverness. Simple code that a teammate can review in 5–10 minutes.
5. Keep changes small; **ask before adding new dependencies**.

## Collaboration

- Work on the assigned GitHub issue only: one issue = one small branch = one pull request. Never push to `main`.
- Branch names: `feat/<issue>-short-name`, `fix/...`, `docs/...`. Commit style: `feat: ...`, `fix: ...`, `docs: ...`, `eval: ...`.
- Prefer minimal scaffolding. Implement what the issue asks for, no more. If the task is unclear or the scope grows, stop and ask.
- Do not rewrite or reformat unrelated files. Do not delete other people's work.
- Explain non-obvious decisions in the PR description; larger decisions go to `docs/DECISIONS.md`.
- Summaries database: all access goes through `summary/store.py`; the file is set with `SUMMARY_DB_PATH`. Generated rows are never changed or deleted. Next to them there is at most one edited version per language (`replace_edit`); `discard_edit` returns to the generated text.
- The web UI talks only to `/api/...` in `summary/api.py`. Modules call each other only through the functions listed in `docs/ARCHITECTURE.md` ("Contract").