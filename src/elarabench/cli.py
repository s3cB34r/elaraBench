"""Thin command-line interface over ElaraBench library services."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from elarabench import __version__
from elarabench.benchmark import BenchmarkLoadError, load_benchmark_suite
from elarabench.builtin import BuiltinSuiteError, resolve_suite_path
from elarabench.comparison import (
    ComparisonError,
    compare_runs,
    comparison_json,
    write_comparison,
)
from elarabench.comparison_models import (
    ComparabilityClassification,
    ComparisonIntent,
    ComparisonResult,
    EvidenceState,
)
from elarabench.models import (
    GenerationParameters,
    RetryPolicy,
    RunConfiguration,
    ThinkingPolicy,
)
from elarabench.providers import ProviderConfigurationError, create_provider
from elarabench.runner import RunInterrupted, Runner, RunnerError
from elarabench.scoring import RunIntegrityError, open_run_path, score_run, summarize_run
from elarabench.storage import ArtifactStoreError


def _parse_thinking_policy(value: str) -> ThinkingPolicy:
    try:
        return ThinkingPolicy(value.replace("-", "_"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "thinking policy must be enabled, disabled, or provider-default"
        ) from error


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
        description=(
            "Validate a benchmark suite path or bundled suite ID and print its canonical identity."
        ),
    )
    validate_parser.add_argument("suite_path", type=Path, metavar="SUITE_PATH")

    run_parser = commands.add_parser(
        "run",
        help="execute a benchmark sequentially",
        description="Run a suite with an implemented model provider, or resume a run.",
    )
    run_parser.add_argument("suite_path", nargs="?", type=Path, metavar="SUITE_PATH")
    run_parser.add_argument("--resume", type=Path, metavar="RUN_PATH")
    run_parser.add_argument("--provider")
    run_parser.add_argument("--model")
    run_parser.add_argument("--endpoint")
    run_parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    run_parser.add_argument("--run-id")
    run_parser.add_argument("--repeats", type=int)
    run_parser.add_argument("--seed", type=int)
    run_parser.add_argument("--temperature", type=float)
    run_parser.add_argument("--top-p", type=float)
    run_parser.add_argument("--top-k", type=int)
    run_parser.add_argument("--max-tokens", type=int)
    run_parser.add_argument("--stop", action="append")
    thinking_group = run_parser.add_mutually_exclusive_group()
    thinking_group.add_argument(
        "--think",
        dest="thinking",
        action="store_const",
        const=ThinkingPolicy.ENABLED,
        help="explicitly enable model reasoning/thinking",
    )
    thinking_group.add_argument(
        "--no-think",
        dest="thinking",
        action="store_const",
        const=ThinkingPolicy.DISABLED,
        help="explicitly disable model reasoning/thinking (the ElaraBench default)",
    )
    thinking_group.add_argument(
        "--thinking",
        type=_parse_thinking_policy,
        metavar="POLICY",
        help="set enabled, disabled, or provider-default thinking behavior",
    )
    run_parser.add_argument(
        "--timeout",
        type=float,
        help="generation read timeout in seconds (default: suite policy or 120)",
    )
    run_parser.add_argument("--max-retries", type=int)
    run_parser.add_argument("--retry-backoff", type=float)
    run_parser.add_argument("--minimum-coverage", type=float)

    score_parser = commands.add_parser(
        "score",
        help="re-evaluate stored canonical responses",
    )
    score_parser.add_argument("run_path", type=Path, metavar="RUN_PATH")

    summarize_parser = commands.add_parser(
        "summarize",
        help="regenerate a summary from stored evaluations",
    )
    summarize_parser.add_argument("run_path", type=Path, metavar="RUN_PATH")

    compare_parser = commands.add_parser(
        "compare",
        help="compare baseline and candidate run evidence",
        description=(
            "Compare the candidate (second path) against the baseline (first path) "
            "after checking methodological comparability. Exit status reflects quality "
            "comparability only: 0 for strict/qualified, 1 for quality not directly "
            "comparable, and 2 for input or operational failure."
        ),
    )
    compare_parser.add_argument("baseline_run", type=Path, metavar="BASELINE_RUN")
    compare_parser.add_argument("candidate_run", type=Path, metavar="CANDIDATE_RUN")
    compare_parser.add_argument(
        "--intent",
        choices=tuple(intent.value for intent in ComparisonIntent),
        default=ComparisonIntent.MODEL.value,
    )
    compare_parser.add_argument(
        "--json", action="store_true", dest="json_output", help="print complete JSON"
    )
    compare_parser.add_argument("--output", type=Path, metavar="PATH")
    return parser


def _validate_command(suite_path: Path) -> int:
    loaded = load_benchmark_suite(resolve_suite_path(suite_path))
    print(f"Suite: {loaded.suite.id}")
    print(f"Version: {loaded.suite.version}")
    print(f"Cases: {len(loaded.suite.cases)}")
    print(f"Content hash: {loaded.content_hash}")
    return 0


def _print_summary(
    run_path: Path,
    score: float | None,
    partial: float | None,
    coverage: float,
) -> None:
    print(f"Run: {run_path}")
    print(f"Score: {score if score is not None else 'insufficient coverage'}")
    print(f"Partial score: {partial if partial is not None else 'unavailable'}")
    print(f"Scored coverage: {coverage:.2%}")


def _new_run_command(arguments: argparse.Namespace) -> int:
    if arguments.suite_path is None:
        raise RunnerError("SUITE_PATH is required unless --resume is used")
    if arguments.model is None:
        raise RunnerError("--model is required for a new run")
    loaded = load_benchmark_suite(resolve_suite_path(arguments.suite_path))
    provider_type = arguments.provider or "ollama"
    repeats = arguments.repeats if arguments.repeats is not None else loaded.suite.defaults.repeats
    timeout = (
        arguments.timeout
        if arguments.timeout is not None
        else loaded.suite.defaults.timeout_seconds
    )
    minimum_coverage = (
        arguments.minimum_coverage
        if arguments.minimum_coverage is not None
        else loaded.suite.aggregation.minimum_scored_coverage
    )
    thinking = (
        arguments.thinking
        if arguments.thinking is not None
        else loaded.suite.defaults.thinking or ThinkingPolicy.DISABLED
    )
    parameters = GenerationParameters(
        temperature=arguments.temperature,
        top_p=arguments.top_p,
        top_k=arguments.top_k,
        max_tokens=arguments.max_tokens,
        stop=tuple(arguments.stop or ()),
    )
    retry_policy = RetryPolicy(
        max_retries=arguments.max_retries if arguments.max_retries is not None else 2,
        initial_backoff_seconds=(
            arguments.retry_backoff if arguments.retry_backoff is not None else 0.5
        ),
    )
    configuration = RunConfiguration(
        suite_path=str(loaded.suite_dir),
        provider=provider_type,
        model=arguments.model,
        endpoint=arguments.endpoint,
        repeats=repeats,
        generation_parameters=parameters,
        thinking=thinking,
        seed=arguments.seed,
        timeout_seconds=timeout,
        retry_policy=retry_policy,
        minimum_scored_coverage=minimum_coverage,
    )
    provider = create_provider(
        provider_type,
        model=configuration.model,
        endpoint=configuration.endpoint,
    )
    result = Runner(provider, runs_dir=arguments.runs_dir).run(
        loaded,
        configuration,
        run_id=arguments.run_id,
    )
    _print_summary(
        result.path,
        result.summary.score,
        result.summary.partial_score,
        result.summary.coverage.ratio,
    )
    return 0 if result.summary.coverage.sufficient else 1


def _resume_command(arguments: argparse.Namespace) -> int:
    if arguments.suite_path is not None:
        raise RunnerError("SUITE_PATH cannot be supplied with --resume")
    incompatible_options = (
        "provider",
        "model",
        "endpoint",
        "run_id",
        "repeats",
        "seed",
        "temperature",
        "top_p",
        "top_k",
        "max_tokens",
        "stop",
        "thinking",
        "timeout",
        "max_retries",
        "retry_backoff",
        "minimum_coverage",
    )
    if any(getattr(arguments, name) is not None for name in incompatible_options):
        raise RunnerError("run configuration options cannot override a resumed run")
    store = open_run_path(arguments.resume)
    schema_version = store.read_manifest_data().get("schema_version")
    if schema_version == 2:
        raise RunnerError(
            "result schema v2 predates explicit Thinking-policy identity; it may be "
            "scored or summarized but cannot be resumed under schema v3. Start a new run."
        )
    manifest = store.read_manifest()
    provider = create_provider(
        manifest.configuration.provider,
        model=manifest.configuration.model,
        endpoint=manifest.configuration.endpoint,
    )
    result = Runner(provider, runs_dir=store.path.parent).resume(store.path)
    _print_summary(
        result.path,
        result.summary.score,
        result.summary.partial_score,
        result.summary.coverage.ratio,
    )
    return 0 if result.summary.coverage.sufficient else 1


def _run_command(arguments: argparse.Namespace) -> int:
    if arguments.resume is not None:
        return _resume_command(arguments)
    return _new_run_command(arguments)


def _score_command(path: Path) -> int:
    summary = score_run(path)
    _print_summary(path.resolve(), summary.score, summary.partial_score, summary.coverage.ratio)
    return 0 if summary.coverage.sufficient else 1


def _summarize_command(path: Path) -> int:
    summary = summarize_run(path)
    _print_summary(path.resolve(), summary.score, summary.partial_score, summary.coverage.ratio)
    return 0 if summary.coverage.sufficient else 1


def _print_comparison(result: ComparisonResult) -> None:
    baseline_benchmark = result.baseline.benchmark
    candidate_benchmark = result.candidate.benchmark
    if baseline_benchmark == candidate_benchmark:
        print(
            f"Benchmark: {baseline_benchmark.suite_id} "
            f"v{baseline_benchmark.version}"
        )
    else:
        print(
            f"Baseline benchmark: {baseline_benchmark.suite_id} "
            f"v{baseline_benchmark.version} "
            f"({baseline_benchmark.content_hash})"
        )
        print(
            f"Candidate benchmark: {candidate_benchmark.suite_id} "
            f"v{candidate_benchmark.version} "
            f"({candidate_benchmark.content_hash})"
        )
    print(f"Intent: {result.intent.value}")
    print(f"Quality comparability: {result.quality_comparability.classification.value.upper()}")
    print(
        "Performance comparability: "
        f"{result.performance_comparability.classification.value.upper()}"
    )
    quality_reasons = result.quality_comparability.reason_codes
    if quality_reasons:
        print("Quality reasons: " + ", ".join(reason.value for reason in quality_reasons))
    performance_reasons = result.performance_comparability.reason_codes
    if performance_reasons:
        print(
            "Performance reasons: "
            + ", ".join(reason.value for reason in performance_reasons)
        )
    source_coverage_label = (
        "Source coverage" if result.verified_intersection is not None else "Coverage"
    )
    print(
        f"{source_coverage_label}: "
        f"baseline {result.coverage.baseline_scored_samples}/"
        f"{result.coverage.baseline_expected_samples} "
        f"({result.coverage.baseline_ratio:.2%}), "
        f"candidate {result.coverage.candidate_scored_samples}/"
        f"{result.coverage.candidate_expected_samples} "
        f"({result.coverage.candidate_ratio:.2%})"
    )
    if result.verified_intersection is not None:
        intersection = result.verified_intersection
        intersection_coverage = intersection.coverage
        baseline_intersection_ratio = (
            f"{intersection_coverage.baseline_ratio:.2%}"
            if intersection_coverage.baseline_ratio is not None
            else "not available"
        )
        candidate_intersection_ratio = (
            f"{intersection_coverage.candidate_ratio:.2%}"
            if intersection_coverage.candidate_ratio is not None
            else "not available"
        )
        print(
            "Intersection coverage: "
            f"baseline {intersection_coverage.baseline_scored_samples}/"
            f"{intersection_coverage.baseline_expected_samples} "
            f"({baseline_intersection_ratio}), "
            f"candidate {intersection_coverage.candidate_scored_samples}/"
            f"{intersection_coverage.candidate_expected_samples} "
            f"({candidate_intersection_ratio})"
        )
        if not intersection_coverage.sufficient:
            print(
                "Intersection minimum required: "
                f"baseline {intersection_coverage.baseline_minimum_required:.2%}, "
                f"candidate {intersection_coverage.candidate_minimum_required:.2%}"
            )
        print(f"Population: {result.case_population_mode.value.upper()}")
        print(
            "Cases: "
            f"baseline {intersection.baseline_total_case_count}, "
            f"candidate {intersection.candidate_total_case_count}, "
            f"verified {len(intersection.verified_cases)}, "
            f"definition mismatches {len(intersection.definition_mismatches)}"
        )
        print(
            "One-sided cases: "
            f"baseline-only {len(intersection.baseline_only_case_ids)}, "
            f"candidate-only {len(intersection.candidate_only_case_ids)}"
        )
    else:
        population_evidence = next(
            (
                item
                for item in result.evidence
                if item.field_path == "coverage.scored_sample_population"
            ),
            None,
        )
        completeness_evidence = next(
            (
                item
                for item in result.evidence
                if item.field_path == "coverage.population_completeness"
            ),
            None,
        )
        if population_evidence is not None and completeness_evidence is not None:
            populations_match = population_evidence.state is EvidenceState.MATCH
            population_complete = completeness_evidence.state is EvidenceState.MATCH
            if populations_match and not population_complete:
                print("Population: matching but incomplete")
            else:
                relationship = "matching" if populations_match else "different"
                completeness = "complete" if population_complete else "incomplete"
                print(f"Population: {relationship} and {completeness}")
    score = result.full_suite_score_comparison
    if score is not None:
        print(f"Baseline score: {score.baseline_score:.6f}")
        print(f"Candidate score: {score.candidate_score:.6f}")
        print(
            f"Delta (candidate - baseline): {score.delta:+.6f} "
            f"({score.percentage_points:+.2f} percentage points)"
        )
    elif result.matched_case_score_comparison is not None:
        partial = result.matched_case_score_comparison
        print(f"Full-suite delta: withheld; matched-case partial over {partial.case_count} cases")
        print(
            f"Matched delta (candidate - baseline): {partial.delta:+.6f} "
            f"({partial.percentage_points:+.2f} percentage points)"
        )
    elif result.verified_intersection_score_comparison is not None:
        intersection_score = result.verified_intersection_score_comparison
        print(
            "Full-suite delta: withheld; verified intersection over "
            f"{intersection_score.case_count} cases"
        )
        print(
            "Intersection score: "
            f"{intersection_score.baseline_score:.6f} -> "
            f"{intersection_score.candidate_score:.6f}"
        )
        print(
            "Intersection delta (candidate - baseline): "
            f"{intersection_score.delta:+.6f} "
            f"({intersection_score.percentage_points:+.2f} percentage points)"
        )
    else:
        print("Full-suite delta: unavailable")
    if result.categories:
        print("Categories:")
        for category in result.categories:
            denominator = ""
            if result.verified_intersection is not None:
                denominator = (
                    f" [{category.case_count}/{category.baseline_total_case_count} baseline, "
                    f"{category.case_count}/{category.candidate_total_case_count} candidate]"
                )
            print(
                f"  {category.name}: {category.baseline_score:.4f} -> "
                f"{category.candidate_score:.4f} ({category.delta:+.4f})"
                f"{denominator}"
            )


def _compare_command(arguments: argparse.Namespace) -> int:
    result = compare_runs(
        arguments.baseline_run,
        arguments.candidate_run,
        intent=ComparisonIntent(arguments.intent),
    )
    if arguments.output is not None:
        write_comparison(
            arguments.output,
            result,
            source_runs=(arguments.baseline_run, arguments.candidate_run),
        )
    if arguments.json_output:
        print(comparison_json(result), end="")
    else:
        _print_comparison(result)
    return (
        1
        if result.quality_comparability.classification
        is ComparabilityClassification.NOT_DIRECTLY_COMPARABLE
        else 0
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ElaraBench command-line interface."""
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "validate":
            return _validate_command(arguments.suite_path)
        if arguments.command == "run":
            return _run_command(arguments)
        if arguments.command == "score":
            return _score_command(arguments.run_path)
        if arguments.command == "summarize":
            return _summarize_command(arguments.run_path)
        if arguments.command == "compare":
            return _compare_command(arguments)
        return 0
    except RunInterrupted as error:
        print(f"interrupted: resumable run preserved at {error.path}", file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except (
        ArtifactStoreError,
        BenchmarkLoadError,
        BuiltinSuiteError,
        ComparisonError,
        ProviderConfigurationError,
        RunIntegrityError,
        RunnerError,
        ValidationError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
