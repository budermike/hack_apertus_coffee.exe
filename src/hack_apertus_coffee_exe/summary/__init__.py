"""Summary stage: Suva-style DE/FR summaries of Federal Court judgements with Apertus.

Issues #14 (style + glossary in ``prompts/``), #15 (generation), #16 (check),
#17 (batch + on demand), #26 (SQLite store). See ``run.py`` for the CLI.
"""

from .generate import Summarizer, SummaryResult
from .judgement import Judgement, load_judgements
from .store import SummaryStore

__all__ = ["Judgement", "Summarizer", "SummaryResult", "SummaryStore", "load_judgements"]
