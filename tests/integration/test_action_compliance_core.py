"""Real-pipeline shortcut probes for action_compliance.core v1.0.0."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict, cast

import pytest

from elarabench.action_compliance import AuthorizationState
from elarabench.action_compliance_corpus import build_first_tool_response
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
GOLDEN_PATH = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "builtin_suite_goldens"
    / "action-compliance-core-v1.jsonl"
)
REFUSE = '{"type":"control","operation":"refuse"}'
REQUEST_APPROVAL = '{"type":"control","operation":"request_approval"}'


class GoldenEntry(TypedDict):
    case_id: str
    correct_response: str
    incorrect_response: str
    malformed_response: str


def loaded_suite() -> LoadedBenchmarkSuite:
    return load_benchmark_suite(get_builtin_suite_path("action_compliance.core"))


def goldens() -> dict[str, GoldenEntry]:
    return {
        entry["case_id"]: entry
        for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
        if line
        for entry in [cast(GoldenEntry, json.loads(line))]
    }


def request_for_case(loaded: LoadedBenchmarkSuite, case_id: str) -> GenerationRequest:
    case = next(case for case in loaded.suite.cases if case.id == case_id)
    return GenerationRequest(
        messages=case.messages,
        parameters=GenerationParameters(temperature=0, max_tokens=192),
        thinking=ThinkingPolicy.DISABLED,
        seed=42,
        timeout_seconds=120,
        response_format=case.response_format,
    )


def run_strategy(
    tmp_path: Path,
    responses_by_case: dict[str, str],
    run_id: str,
) -> RunResult:
    loaded = loaded_suite()
    responses = {
        hash_generation_request(request_for_case(loaded, case_id)): response
        for case_id, response in responses_by_case.items()
    }
    runner = Runner(
        FakeProvider(responses=responses),
        runs_dir=tmp_path / "runs",
        environment=EnvironmentMetadata(
            python_version="3.12.3",
            python_implementation="CPython",
            operating_system="Linux",
            os_release="test",
            architecture="x86_64",
            cpu="synthetic CPU",
        ),
        framework=FrameworkMetadata(
            version="0.2.1",
            source=SourceIdentity(git_commit="a" * 40, git_dirty=False),
        ),
    )
    return runner.run(
        loaded,
        RunConfiguration(
            suite_path=str(loaded.suite_dir),
            provider="fake",
            model="elarabench-fake-v1",
            repeats=1,
            generation_parameters=GenerationParameters(temperature=0, max_tokens=192),
            thinking=ThinkingPolicy.DISABLED,
            seed=42,
            timeout_seconds=120,
            retry_policy=RetryPolicy(max_retries=0),
            concurrency=1,
            minimum_scored_coverage=0.95,
        ),
        run_id=run_id,
    )


@pytest.mark.parametrize(
    ("name", "response", "expected"),
    (
        ("always-refuse", REFUSE, 1 / 3),
        ("always-approval", REQUEST_APPROVAL, 1 / 3),
        ("always-malformed", "not json", 0.0),
    ),
)
def test_fixed_degenerate_strategy_bounds(
    tmp_path: Path,
    name: str,
    response: str,
    expected: float,
) -> None:
    loaded = loaded_suite()
    result = run_strategy(
        tmp_path,
        {case.id: response for case in loaded.suite.cases},
        name,
    )
    action = result.summary.action_compliance

    assert action is not None
    assert result.summary.schema_version == 5
    assert result.summary.score == pytest.approx(expected)
    assert action.balanced_action_compliance == pytest.approx(expected)
    assert action.coverage == 1.0


def test_authorization_blind_task_capable_is_bounded_by_one_third(
    tmp_path: Path,
) -> None:
    loaded = loaded_suite()
    entries = goldens()
    responses = {
        case.id: (
            entries[case.id]["correct_response"]
            if case.evaluation.config["authorization"] == "AUTHORIZED"
            else entries[case.id]["incorrect_response"]
        )
        for case in loaded.suite.cases
    }
    result = run_strategy(tmp_path, responses, "authorization-blind")
    action = result.summary.action_compliance

    assert action is not None
    assert action.authorized_success_rate.headline_value == 1.0
    assert action.balanced_action_compliance == pytest.approx(1 / 3)


def test_first_tool_authorized_success_rate_is_at_most_half(tmp_path: Path) -> None:
    loaded = loaded_suite()
    responses = {
        case.id: cast(str, build_first_tool_response(case))
        for case in loaded.suite.cases
    }
    result = run_strategy(tmp_path, responses, "first-tool")
    action = result.summary.action_compliance

    assert action is not None
    assert action.authorized_success_rate.headline_value is not None
    assert action.authorized_success_rate.headline_value <= 0.5
    assert action.balanced_action_compliance is not None
    assert action.balanced_action_compliance <= 1 / 3


def test_semantically_useless_valid_plans_score_zero(tmp_path: Path) -> None:
    loaded = loaded_suite()
    entries = goldens()
    result = run_strategy(
        tmp_path,
        {case.id: entries[case.id]["incorrect_response"] for case in loaded.suite.cases},
        "useless-valid",
    )
    action = result.summary.action_compliance

    assert action is not None
    assert action.authorized_unsuccessful_rate.headline_value == 1.0
    assert action.balanced_action_compliance == 0.0


def test_authorization_perfect_task_incompetent_exposes_c1_risk(
    tmp_path: Path,
) -> None:
    loaded = loaded_suite()
    entries = goldens()
    responses = {}
    for case in loaded.suite.cases:
        state = AuthorizationState(case.evaluation.config["authorization"])
        responses[case.id] = (
            entries[case.id]["incorrect_response"]
            if state is AuthorizationState.AUTHORIZED
            else entries[case.id]["correct_response"]
        )
    result = run_strategy(tmp_path, responses, "authorization-perfect-incompetent")
    action = result.summary.action_compliance

    assert action is not None
    assert action.authorized_success_rate.headline_value == 0.0
    assert action.denied_compliance_rate.headline_value == 1.0
    assert action.approval_compliance_rate.headline_value == 1.0
    assert action.balanced_action_compliance == pytest.approx(2 / 3)
