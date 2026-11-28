"""Run Abi's two-pass relevance scoring (#62) with a larger token budget, e.g. for thinking models.

The reasoning models spend part of ``max_tokens`` on hidden thinking. With the
budgets of ``triage/relevance.py`` (1600 for pass 1, 400 for pass 2)
``apertus-v1-5-8b-thinking`` often returns empty or cut-off JSON. This runner
multiplies both budgets and otherwise calls the unchanged pipeline
(``relevance_period.run``). The cache key of a request does not contain
``max_tokens``, so answers that already succeeded are reused and only the failed
judgements are asked again.

    python -m hack_apertus_coffee_exe.eval.run_relevance --model apertus-v1-5-8b-thinking \\
        --tables-dir results/tables/thinking_8b --token-factor 4
"""

from __future__ import annotations

import argparse
from typing import Any, Sequence

from dotenv import load_dotenv

from hack_apertus_coffee_exe.triage import relevance, relevance_period
from hack_apertus_coffee_exe.triage.config import load_config
from hack_apertus_coffee_exe.triage.llm import GatewayConfig


class RoomyRelevanceClient(relevance.ApertusRelevanceClient):
    """Same client, every ``max_tokens`` multiplied by ``token_factor``."""

    def __init__(self, *args: Any, token_factor: float = 4.0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.token_factor = token_factor

    def call(self, stage: str, key: str, system: str, user: str, schema: dict[str, Any], max_tokens: int) -> dict[str, Any]:
        return super().call(stage, key, system, user, schema, int(max_tokens * self.token_factor))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--tables-dir", required=True)
    parser.add_argument("--period", default="2026-Q1")
    parser.add_argument("--label-periods", nargs="*", default=["2025-Q4", "2026-Q1"])
    parser.add_argument("--token-factor", type=float, default=4.0)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    load_dotenv()
    config = load_config()
    client = RoomyRelevanceClient(config=GatewayConfig.from_env(model=args.model), cache_dir=config.cache_dir,
                                  token_factor=args.token_factor)
    relevance_period.run(args.period, label_periods=args.label_periods, tables_dir=args.tables_dir, client=client,
                         list_factor=config.list_factor, workers=args.workers, config=config)
    print(f"Apertus calls: {client.calls} (model {args.model}, token factor {args.token_factor})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
