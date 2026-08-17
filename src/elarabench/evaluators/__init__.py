"""Deterministic evaluators and explicit specification dispatch."""

from elarabench.evaluators.base import Evaluator, EvaluatorConfigurationError
from elarabench.evaluators.registry import evaluate, validate_specification

__all__ = ["Evaluator", "EvaluatorConfigurationError", "evaluate", "validate_specification"]
