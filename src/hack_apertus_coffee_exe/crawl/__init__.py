"""Crawl and Vector-DB modules."""

__all__ = ["PeriodStatus", "ProgressEvent", "ensure_period"]


def __getattr__(name: str):
    if name in __all__:
        from . import ensure

        return getattr(ensure, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
