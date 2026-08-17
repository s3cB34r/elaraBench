"""Command-line interface for the ElaraBench project foundation."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from elarabench import __version__


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ElaraBench command-line interface."""
    build_parser().parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
