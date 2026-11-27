"""Summary stage: Suva-style DE/FR summaries of Federal Court judgements with Apertus.

Issues #14 (style + glossary in ``prompts/``), #15 (generation), #16 (check),
#17 (automatic for green, on demand otherwise). See ``run.py`` for the CLI.
Storing summaries (summaries DB, #26) is not part of this package.
"""

from .generate import Summarizer, SummaryResult
from .judgement import Judgement, load_judgements
from .run import run_batch, summarize_case

__all__ = ["Judgement", "Summarizer", "SummaryResult", "load_judgements", "run_batch", "summarize_case"]
