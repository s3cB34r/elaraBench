"""ElaraBench package foundation."""

from elarabench.builtin import (
    BuiltinSuiteError,
    available_builtin_suites,
    get_builtin_suite_path,
)

__version__ = "0.2.1"

__all__ = [
    "BuiltinSuiteError",
    "__version__",
    "available_builtin_suites",
    "get_builtin_suite_path",
]
