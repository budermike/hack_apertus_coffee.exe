# AGENTS.md — hack_apertus_coffee.exe

Primer for coding agents working in this repository.

## Project

A short description of the project.

## Layout

- Reusable code lives in `src/hack_apertus_coffee.exe/` and is imported from notebooks and scripts — do not copy logic into notebooks.
- `data/raw/` is immutable: never modify or overwrite files there. Write cleaned data to `data/processed/`, derived data for presentation to `data/synthesized/`.
- Outputs go to `results/figures/`, `results/tables/` or `results/reports/`.
- Every module in `src` gets a matching `tests/test_<module>.py`.

## Setup & checks

This project uses **uv**. Never call `pip` directly and never edit `uv.lock` by hand.

```bash
uv sync               # install / update environment
uv run pytest         # run tests
uv add <pkg>          # add a dependency (uv add --group test <pkg> for test-only)
```

## Priorities

1. Correctness and reproducibility (fixed seeds, no hidden state in notebooks).
2. Readability over cleverness.
3. Keep changes small; ask before adding new dependencies.

## Collaboration

- Prefer minimal scaffolding; the author writes core logic himself unless asked otherwise.
- TODO: add project-specific conventions.
