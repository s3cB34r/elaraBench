"""Command-line interface for the ElaraBench project foundation."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from elarabench import __version__
from elarabench.benchmark import BenchmarkLoadError, load_benchmark_suite


def build_parser() -> argparse.ArgumentParser:
    """Build the ElaraBench command-line parser."""
    parser = argparse.ArgumentParser(
        prog="elarabench",
        description="Reproducible, provider-neutral benchmarks for local and remote LLMs.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    commands = parser.add_subparsers(dest="command", title="commands")
    validate_parser = commands.add_parser(
        "validate",
        help="validate and hash a benchmark suite",
        description="Validate a benchmark suite and print its canonical identity.",
    )
    validate_parser.add_argument("suite_path", type=Path, metavar="SUITE_PATH")
    return parser


def _validate_command(suite_path: Path) -> int:
    try:
        loaded = load_benchmark_suite(suite_path)
    except BenchmarkLoadError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"Suite: {loaded.suite.id}")
    print(f"Version: {loaded.suite.version}")
    print(f"Cases: {len(loaded.suite.cases)}")
    print(f"Content hash: {loaded.content_hash}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ElaraBench command-line interface."""
    arguments = build_parser().parse_args(argv)
    if arguments.command == "validate":
        return _validate_command(arguments.suite_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
