"""Production Recovery Goldens and shortcut strategies through the real pipeline."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from elarabench.action_compliance import ActionPlanEnvelope, ProposedAction
from elarabench.action_recovery import (
    ActionRecoveryConfig,
    ActionRecoveryEvaluationArtifact,
    ActionRecoveryOutcome,
    Recoverability,
)
from elarabench.action_recovery_corpus import (
    ToolInvocability,
    analyze_tool_invocability,
)
from elarabench.benchmark import LoadedBenchmarkSuite, load_benchmark_suite
from elarabench.builtin import get_builtin_suite_path
from elarabench.hashing import canonical_json_bytes, hash_generation_request
from elarabench.models import (
    EnvironmentMetadata,
    FrameworkMetadata,
    GenerationParameters,
    GenerationRequest,
    RetryPolicy,
    RunConfiguration,
    SampleIdentity,
    SourceIdentity,
    ThinkingPolicy,
)
from elarabench.providers import FakeProvider
from elarabench.runner import Runner
from elarabench.storage import ArtifactStore

PROJECT_ROOT = Path(__file__).parents[2]
GOLDEN_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "builtin_suite_goldens" / "action-recovery-core-v1.jsonl"
)
CONTROL_REFUSE = '{"operation":"refuse","type":"control"}'
CONTROL_APPROVAL = '{"operation":"request_approval","type":"control"}'


def _loaded() -> LoadedBenchmarkSuite:
    return load_benchmark_suite(get_builtin_suite_path("action_recovery.core"))


def _goldens() -> dict[str, dict[str, str]]:
    return {row["case_id"]: row for row in map(json.loads, GOLDEN_PATH.read_text().splitlines())}


def _request(case) -> GenerationRequest:
    return GenerationRequest(
        messages=case.messages,
        parameters=GenerationParameters(temperature=0, max_tokens=256),
        thinking=ThinkingPolicy.DISABLED,
        seed=42,
        timeout_seconds=30,
        response_format=None,
    )


def _action(actions: tuple[ProposedAction, ...]) -> str:
    return canonical_json_bytes(ActionPlanEnvelope(type="action", actions=actions)).decode()


def _run_strategy(
    tmp_path: Path,
    name: str,
    strategy: Callable[[object, ActionRecoveryConfig], str],
):
    loaded = _loaded()
    responses = {
        hash_generation_request(_request(case)): strategy(
            case, ActionRecoveryConfig.model_validate(case.evaluation.config)
        )
        for case in loaded.suite.cases
    }
    provider = FakeProvider(responses=responses)
    runs_dir = tmp_path / name
    runner = Runner(
        provider,
        runs_dir=runs_dir,
        framework=FrameworkMetadata(
            version="0.2.1",
            source=SourceIdentity(git_commit="a" * 40, git_dirty=False),
        ),
        environment=EnvironmentMetadata(
            python_version="3.12.3",
            python_implementation="CPython",
            operating_system="Linux",
            os_release="test",
            architecture="x86_64",
            cpu="synthetic CPU",
        ),
        sleeper=lambda _: None,
    )
    result = runner.run(
        loaded,
        RunConfiguration(
            suite_path="action_recovery.core",
            provider="fake",
            model="elarabench-fake-v1",
            generation_parameters=GenerationParameters(temperature=0, max_tokens=256),
            thinking=ThinkingPolicy.DISABLED,
            seed=42,
            timeout_seconds=30,
            retry_policy=RetryPolicy(max_retries=0, initial_backoff_seconds=0),
        ),
        run_id=name,
    )
    store = ArtifactStore(runs_dir).open_run(name)
    outcomes: Counter[ActionRecoveryOutcome] = Counter()
    for case in loaded.suite.cases:
        evaluation = store.read_evaluation(
            SampleIdentity(case_id=case.id, repeat_index=0),
            source_result_schema_version=3,
        )
        artifact = ActionRecoveryEvaluationArtifact.model_validate(evaluation.artifacts)
        outcomes[artifact.outcome] += 1
    return result.summary, outcomes


def _first_invocable(config: ActionRecoveryConfig, *, alternate: bool) -> str:
    for name in sorted(config.tools):
        analysis = analyze_tool_invocability(config, name, config.tools[name])
        if analysis.classification is not ToolInvocability.INVOCABLE:
            continue
        assert analysis.witness is not None
        if alternate and analysis.witness.tool == config.failed_action.tool:
            continue
        return _action((analysis.witness,))
    return "not json"


def test_all_36_production_goldens_have_exact_outcomes_and_scores(tmp_path: Path) -> None:
    goldens = _goldens()
    loaded = _loaded()
    assert len(goldens) == len(loaded.suite.cases) == 36
    for vector, expected_score in (
        ("correct_response", 1.0),
        ("incorrect_response", 0.0),
        ("malformed_response", 0.0),
    ):
        summary, outcomes = _run_strategy(
            tmp_path,
            f"golden-{vector}",
            lambda case, _config, vector=vector: goldens[case.id][vector],
        )
        assert summary.action_recovery is not None
        assert summary.action_recovery.sample_outcomes.total() == 36
        assert sum(outcomes.values()) == 36
        if vector == "correct_response":
            assert summary.action_recovery.balanced_action_recovery == expected_score
            assert outcomes == Counter(
                {
                    ActionRecoveryOutcome.RECOVERED: 12,
                    ActionRecoveryOutcome.CORRECT_TERMINAL_STOP: 12,
                    ActionRecoveryOutcome.GATED_CORRECT_STOP: 12,
                }
            )
        elif vector == "malformed_response":
            assert summary.action_recovery.balanced_action_recovery == 0.0
            assert outcomes == Counter({ActionRecoveryOutcome.PROTOCOL_INVALID: 36})
        else:
            assert outcomes[ActionRecoveryOutcome.PROTOCOL_INVALID] == 0


def test_degenerate_and_perfect_strategies_are_measured_through_runner(
    tmp_path: Path,
) -> None:
    goldens = _goldens()
    always_refuse, _ = _run_strategy(
        tmp_path, "always-refuse", lambda _case, _config: CONTROL_REFUSE
    )
    refuse = always_refuse.action_recovery
    assert refuse is not None
    assert refuse.balanced_action_recovery == 0.5
    assert refuse.denied_compliance_rate.headline_value == 1.0
    assert refuse.approval_compliance_rate.headline_value == 0.0

    always_approval, _ = _run_strategy(
        tmp_path, "always-approval", lambda _case, _config: CONTROL_APPROVAL
    )
    approval = always_approval.action_recovery
    assert approval is not None
    assert approval.balanced_action_recovery == 0.5
    assert approval.denied_compliance_rate.headline_value == 0.0
    assert approval.approval_compliance_rate.headline_value == 1.0

    malformed, _ = _run_strategy(tmp_path, "always-malformed", lambda _case, _config: "not json")
    assert malformed.action_recovery is not None
    assert malformed.action_recovery.balanced_action_recovery == 0.0

    repeat, repeat_outcomes = _run_strategy(
        tmp_path,
        "repeat-failed",
        lambda _case, config: _action((config.failed_action,)),
    )
    assert repeat.action_recovery is not None
    assert repeat.action_recovery.balanced_action_recovery == 0.0
    assert repeat_outcomes[ActionRecoveryOutcome.RECOVERED] == 0

    replay, replay_outcomes = _run_strategy(
        tmp_path,
        "resubmit-original",
        lambda _case, config: _action(config.attempted_actions),
    )
    assert replay.action_recovery is not None
    assert replay.action_recovery.balanced_action_recovery == 0.0
    assert replay_outcomes[ActionRecoveryOutcome.RECOVERED] == 0

    always_act, _ = _run_strategy(
        tmp_path,
        "always-act",
        lambda _case, config: _first_invocable(config, alternate=False),
    )
    assert always_act.action_recovery is not None
    assert always_act.action_recovery.balanced_action_recovery == 0.0

    group_correct: dict[str, str] = {}
    for case in _loaded().suite.cases:
        config = ActionRecoveryConfig.model_validate(case.evaluation.config)
        groups = [tag for tag in case.tags if tag.startswith("contrastive-group-ar-")]
        if groups and config.recoverability is Recoverability.RECOVERABLE:
            group_correct[groups[0]] = goldens[case.id]["correct_response"]

    observation_blind, _ = _run_strategy(
        tmp_path,
        "observation-blind",
        lambda case, config: (
            group_correct[next(tag for tag in case.tags if tag.startswith("contrastive-group-ar-"))]
            if config.recoverability is not None
            else CONTROL_REFUSE
        ),
    )
    assert observation_blind.action_recovery is not None
    assert observation_blind.action_recovery.balanced_action_recovery == 0.5

    first_tool, _ = _run_strategy(
        tmp_path,
        "first-tool",
        lambda _case, config: _first_invocable(config, alternate=False),
    )
    assert first_tool.action_recovery is not None
    assert first_tool.action_recovery.balanced_action_recovery == 0.0

    alternate, _ = _run_strategy(
        tmp_path,
        "alternate-tool",
        lambda _case, config: _first_invocable(config, alternate=True),
    )
    assert alternate.action_recovery is not None
    assert alternate.action_recovery.balanced_action_recovery == 0.0

    perfect, _ = _run_strategy(
        tmp_path,
        "perfect",
        lambda case, _config: goldens[case.id]["correct_response"],
    )
    control = perfect.action_recovery
    assert control is not None
    assert control.balanced_action_recovery == 1.0
    assert control.denied_compliance_rate.headline_value == 1.0
    assert control.approval_compliance_rate.headline_value == 1.0
