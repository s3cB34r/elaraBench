"""Complete offline FakeProvider runs for first-party benchmark suites."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict, cast

import pytest

from elarabench.benchmark import LoadedBenchmarkSuite, load_benchmark_suite
from elarabench.builtin import get_builtin_suite_path
from elarabench.hashing import hash_generation_request
from elarabench.models import (
    EnvironmentMetadata,
    FrameworkMetadata,
    GenerationParameters,
    GenerationRequest,
    RetryPolicy,
    RunConfiguration,
    SourceIdentity,
    ThinkingPolicy,
)
from elarabench.providers import FakeProvider
from elarabench.runner import Runner, RunResult

PROJECT_ROOT = Path(__file__).parents[2]
GOLDEN_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "builtin_suite_goldens"


class GoldenEntry(TypedDict):
    case_id: str
    correct_response: str
    incorrect_response: str


SUITE_PROFILES = (
    (
        "reasoning.core",
        get_builtin_suite_path("reasoning.core"),
        GOLDEN_ROOT / "reasoning-core-v1.jsonl",
        64,
        18,
    ),
    (
        "instruction_following.core",
        get_builtin_suite_path("instruction_following.core"),
        GOLDEN_ROOT / "instruction-following-core-v1.jsonl",
        128,
        18,
    ),
    (
        "coding.core",
        get_builtin_suite_path("coding.core"),
        GOLDEN_ROOT / "coding-core-v1.jsonl",
        128,
        12,
    ),
    (
        "cybersecurity.core",
        get_builtin_suite_path("cybersecurity.core"),
        GOLDEN_ROOT / "cybersecurity-core-v1.jsonl",
        192,
        12,
    ),
    (
        "refusal_compliance.core",
        get_builtin_suite_path("refusal_compliance.core"),
        GOLDEN_ROOT / "refusal-compliance-core-v1.jsonl",
        192,
        54,
    ),
    (
        "action_compliance.core",
        get_builtin_suite_path("action_compliance.core"),
        GOLDEN_ROOT / "action-compliance-core-v1.jsonl",
        192,
        36,
    ),
)


def environment() -> EnvironmentMetadata:
    return EnvironmentMetadata(
        python_version="3.12.3",
        python_implementation="CPython",
        operating_system="Linux",
        os_release="test",
        architecture="x86_64",
        cpu="synthetic CPU",
    )


def framework() -> FrameworkMetadata:
    return FrameworkMetadata(
        version="0.2.1",
        source=SourceIdentity(git_commit="a" * 40, git_dirty=False),
    )


def configuration(loaded: LoadedBenchmarkSuite, max_tokens: int) -> RunConfiguration:
    return RunConfiguration(
        suite_path=str(loaded.suite_dir),
        provider="fake",
        model="elarabench-fake-v1",
        repeats=1,
        generation_parameters=GenerationParameters(temperature=0, max_tokens=max_tokens),
        thinking=ThinkingPolicy.DISABLED,
        seed=42,
        timeout_seconds=120,
        retry_policy=RetryPolicy(max_retries=0),
        concurrency=1,
        minimum_scored_coverage=0.95,
    )


def request_for_case(
    loaded: LoadedBenchmarkSuite,
    case_id: str,
    max_tokens: int,
) -> GenerationRequest:
    case = next(case for case in loaded.suite.cases if case.id == case_id)
    return GenerationRequest(
        messages=case.messages,
        parameters=GenerationParameters(temperature=0, max_tokens=max_tokens),
        thinking=ThinkingPolicy.DISABLED,
        seed=42,
        timeout_seconds=120,
        response_format=case.response_format,
    )


def load_goldens(path: Path) -> dict[str, GoldenEntry]:
    entries = [
        cast(GoldenEntry, json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {entry["case_id"]: entry for entry in entries}


def refusal_envelope(
    behavior: str,
    *,
    result: object = None,
    reason_code: str | None = None,
    redirect: str | None = None,
) -> str:
    return json.dumps(
        {
            "behavior": behavior,
            "result": result,
            "reason_code": reason_code,
            "redirect": redirect,
        },
        separators=(",", ":"),
    )


def run_with_responses(
    tmp_path: Path,
    loaded: LoadedBenchmarkSuite,
    max_tokens: int,
    responses_by_case: dict[str, str],
    run_id: str,
) -> RunResult:
    responses = {
        hash_generation_request(request_for_case(loaded, case_id, max_tokens)): response
        for case_id, response in responses_by_case.items()
    }
    provider = FakeProvider(responses=responses)
    runner = Runner(
        provider,
        runs_dir=tmp_path / "runs",
        environment=environment(),
        framework=framework(),
    )
    return runner.run(
        loaded,
        configuration(loaded, max_tokens),
        run_id=run_id,
    )


@pytest.mark.parametrize(
    ("suite_id", "suite_path", "golden_path", "max_tokens", "expected_count"),
    SUITE_PROFILES,
)
def test_all_correct_fake_provider_run_scores_one(
    tmp_path: Path,
    suite_id: str,
    suite_path: Path,
    golden_path: Path,
    max_tokens: int,
    expected_count: int,
) -> None:
    loaded = load_benchmark_suite(suite_path)
    goldens = load_goldens(golden_path)
    result = run_with_responses(
        tmp_path,
        loaded,
        max_tokens,
        {case_id: golden["correct_response"] for case_id, golden in goldens.items()},
        f"{suite_id.replace('.', '-')}-all-correct",
    )

    assert result.summary.score == 1.0
    assert result.summary.partial_score == 1.0
    assert result.summary.coverage.ratio == 1.0
    assert result.summary.coverage.scored_samples == expected_count
    if suite_id == "refusal_compliance.core":
        behavior = result.summary.refusal_compliance
        assert behavior is not None
        assert behavior.successful_completion_rate.headline_value == 1.0
        assert behavior.appropriate_refusal_rate.headline_value == 1.0
        assert behavior.inappropriate_compliance_rate.headline_value == 0.0
        assert behavior.unnecessary_refusal_rate.headline_value == 0.0
        assert behavior.instruction_following_rate.headline_value == 1.0
        assert behavior.false_policy_trigger_rate.headline_value == 0.0
        assert behavior.balanced_behavior_accuracy == 1.0
    if suite_id == "action_compliance.core":
        action = result.summary.action_compliance
        assert action is not None
        assert result.summary.schema_version == 5
        assert action.authorized_success_rate.headline_value == 1.0
        assert action.denied_compliance_rate.headline_value == 1.0
        assert action.approval_compliance_rate.headline_value == 1.0
        assert action.balanced_action_compliance == 1.0


@pytest.mark.parametrize(
    ("suite_id", "suite_path", "golden_path", "max_tokens", "expected_count"),
    SUITE_PROFILES,
)
def test_mixed_fake_provider_run_keeps_coverage_and_reduces_score(
    tmp_path: Path,
    suite_id: str,
    suite_path: Path,
    golden_path: Path,
    max_tokens: int,
    expected_count: int,
) -> None:
    loaded = load_benchmark_suite(suite_path)
    goldens = load_goldens(golden_path)
    responses = {
        case.id: (
            goldens[case.id]["correct_response"]
            if index % 2 == 0
            else goldens[case.id]["incorrect_response"]
        )
        for index, case in enumerate(loaded.suite.cases)
    }
    result = run_with_responses(
        tmp_path,
        loaded,
        max_tokens,
        responses,
        f"{suite_id.replace('.', '-')}-mixed",
    )

    assert result.summary.score == 0.5
    assert result.summary.partial_score == 0.5
    assert result.summary.coverage.ratio == 1.0
    assert result.summary.coverage.scored_samples == expected_count
    assert result.summary.sample_status_counts.scored == expected_count
    assert result.summary.sample_status_counts.error == 0


def test_refusal_extreme_strategies_run_through_fake_provider(tmp_path: Path) -> None:
    loaded = load_benchmark_suite(get_builtin_suite_path("refusal_compliance.core"))
    golden_path = GOLDEN_ROOT / "refusal-compliance-core-v1.jsonl"
    goldens = load_goldens(golden_path)
    blind_responses = {}
    refuse_responses = {}
    for case in loaded.suite.cases:
        expected = case.evaluation.config["expected_behavior"]
        blind_responses[case.id] = (
            goldens[case.id]["correct_response"]
            if expected == "comply"
            else refusal_envelope("comply", result={"attempted": True})
        )
        refuse_responses[case.id] = (
            refusal_envelope("refuse", reason_code="policy_or_safety")
            if expected == "comply"
            else goldens[case.id]["correct_response"]
        )

    blind = run_with_responses(
        tmp_path,
        loaded,
        192,
        blind_responses,
        "refusal-compliance-blind-comply",
    ).summary.refusal_compliance
    refuse_all = run_with_responses(
        tmp_path,
        loaded,
        192,
        refuse_responses,
        "refusal-compliance-refuse-all",
    ).summary.refusal_compliance

    assert blind is not None and refuse_all is not None
    assert blind.successful_completion_rate.headline_value == 1.0
    assert blind.inappropriate_compliance_rate.headline_value == 1.0
    assert blind.balanced_behavior_accuracy == 0.5
    assert refuse_all.unnecessary_refusal_rate.headline_value == 1.0
    assert refuse_all.appropriate_refusal_rate.headline_value == 1.0
    assert refuse_all.false_policy_trigger_rate.headline_value == 1.0
    assert refuse_all.balanced_behavior_accuracy == 0.5
