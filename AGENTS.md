# AGENTS.md — hack_apertus_coffee.exe

Primer for coding agents working in this repository. Read this fully before changing anything.

## Context

Hackathon project for **Hack Apertus Zürich** (27–28 Nov 2026). Submission deadline: **Sat 28 Nov 2026, 14:30** — time is short, so a small, working, well-tested core beats many half-finished features.

Project: <ONE SENTENCE — fill in after the challenge is assigned: who has which problem, what we build>.
Challenge: <ti&m Trust Room | Suva legal summaries | Digitec Galaxus image classification>.

Team workflow, roles and the submission checklist: see `CONTRIBUTING.md` and `docs/LEITFADEN.md`.

## Hard rules (competition rules — violating them disqualifies the team)

1. **Apertus v1.5 performs the core AI task** of the solution (e.g. the classification or summarisation itself). Never delegate the core task to another model.
2. **At runtime use only Apertus and open-weights models** for supporting tasks (embeddings, OCR, speech-to-text, reranking). Open-weights = weights are publicly downloadable and self-hostable.
3. **No proprietary AI APIs at runtime.** Never call api.openai.com, api.anthropic.com, Google/Gemini, Cohere, ElevenLabs etc. from project code. The `openai` Python package may be used **only as a client** with `base_url` set to the Apertus gateway (see below).
4. **Must be deployable** on-premise, air-gapped or on a Swiss sovereign cloud: no hard dependency on foreign closed services, no hard-coded external URLs except the configurable Apertus endpoint.
5. **Every model must be logged** in `docs/MODELS.md` (model, version, role, runtime or development, open-weights yes/no, licence). If you add or change a model, update that file in the same change. Mention in the PR if you (the agent) are a model used in development.

If a request would break one of these rules, stop and say so instead of implementing it.

## Apertus access

Configuration comes **only** from environment variables:

```
APERTUS_BASE_URL   # CSCS/Keera gateway endpoint
APERTUS_API_KEY    # team key — never commit, never print, never log
APERTUS_MODEL      # default: apertus-v1-5-8b
```

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

> Package name note: `hack_apertus_coffee.exe` is not a valid Python identifier. Use a valid name such as `hack_apertus_coffee_exe` for the package directory and imports.

## Setup & checks


## Data, secrets and the public repo

- The repository is **public**. Never commit `.env`, API keys, tokens, customer/partner data or personal data.
- Data supplied by challenge partners goes into the repo **only if the partner allows it**. When unsure, keep it local (`data/raw/` stays out of git) and use synthetic or public sample data in the repo.
- Do not print secrets or full private documents into logs, notebooks or test output.

## Tests and evaluation

- Unit tests must **not** call the real gateway: mock the LLM client. Mark tests that need the network (`@pytest.mark.integration`) and keep them out of the default run.
- Evaluation is part of the deliverable. Evaluation code lives in `src/<package>/eval/`; datasets, metric, and results go to `results/tables/`. Report honest numbers, including failure cases.
- Reproducibility first: fixed seeds, pinned versions via uv, no hidden state in notebooks.

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
- TODO: add project-specific conventions after the challenge is assigned.