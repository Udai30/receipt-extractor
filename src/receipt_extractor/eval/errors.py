"""Exception hierarchy of the evaluation framework (TRD §S4).

Every error the CLI reports as a one-line message derives from :class:`EvalError`. Messages
name files and fields, never dataset content or secrets.
"""

from __future__ import annotations


class EvalError(RuntimeError):
    """Base class: a run cannot start, continue or be reported."""


class RunConfigError(EvalError):
    """A run config or import manifest is missing, malformed or inconsistent."""


class PathConfinementError(EvalError):
    """A path from a config or CLI argument resolves outside its allowed directories."""


class DatasetError(EvalError):
    """A dataset file is missing, malformed, or does not contain a requested case."""


class RunCorruptError(EvalError):
    """A run directory holds files the runner did not write or cannot parse."""


class RunLockedError(EvalError):
    """Another process is already executing the same run."""


class UnknownTaskError(EvalError):
    """The config names a task that is not registered."""


class UnknownMetricError(EvalError):
    """The config names a metric that is not registered."""


class PredictionImportError(EvalError):
    """A Kaggle output directory cannot be imported."""
