"""ElaraBench package foundation."""

from elarabench.builtin import (
    BuiltinSuiteError,
    available_builtin_suites,
    get_builtin_suite_path,
)
from elarabench.comparison import compare_runs

__version__ = "0.2.1"

__all__ = [
    "BuiltinSuiteError",
    "__version__",
    "available_builtin_suites",
    "compare_runs",
    "get_builtin_suite_path",
]
