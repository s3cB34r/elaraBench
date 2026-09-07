"""Conservative static invocability and bounded BFS proof tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from elarabench.action_compliance import SyntheticToolDefinition
from elarabench.action_recovery import (
    ActionRecoveryConfig,
    ActionRecoveryEvaluationArtifact,
    ActionRecoveryOutcome,
)
from elarabench.action_recovery_corpus import (
    ReachabilityStatus,
    ToolInvocability,
    analyze_bounded_recoverability,
    analyze_tool_invocability,
)
from elarabench.benchmark import load_benchmark_suite
from elarabench.builtin import get_builtin_suite_path
from elarabench.evaluators.registry import evaluate
from elarabench.models import EvaluationContext, GenerationResponse

PROJECT_ROOT = Path(__file__).parents[2]


def _config(*, recoverable: bool) -> ActionRecoveryConfig:
    loaded = load_benchmark_suite(get_builtin_suite_path("action_recovery.core"))
    return next(
        ActionRecoveryConfig.model_validate(case.evaluation.config)
        for case in loaded.suite.cases
        if (ActionRecoveryConfig.model_validate(case.evaluation.config).recoverability is not None)
        and (
            ActionRecoveryConfig.model_validate(case.evaluation.config).recoverability.value
            == ("recoverable" if recoverable else "unrecoverable")
        )
    )


def _multi_config() -> ActionRecoveryConfig:
    loaded = load_benchmark_suite(get_builtin_suite_path("action_recovery.core"))
    return next(
        ActionRecoveryConfig.model_validate(case.evaluation.config)
        for case in loaded.suite.cases
        if "capability-multi-action" in case.tags and "contrastive-variant-recoverable" in case.tags
    )


def test_bounded_bfs_finds_minimal_recovery_and_exhausts_terminal_state() -> None:
    found = analyze_bounded_recoverability(_config(recoverable=True))
    exhausted = analyze_bounded_recoverability(_config(recoverable=False))
    assert found.status is ReachabilityStatus.RECOVERABLE
    assert len(found.path) >= 1
    assert len(found.path) <= _config(recoverable=True).max_plan_length
    assert exhausted.status is ReachabilityStatus.UNRECOVERABLE
    assert exhausted.path == ()


def test_depth_bound_is_inclusive_and_zero_budget_is_never_assumed() -> None:
    config = _multi_config()
    complete = analyze_bounded_recoverability(config)
    assert len(complete.path) == 2
    assert (
        analyze_bounded_recoverability(config.model_copy(update={"max_plan_length": 2})).status
        is ReachabilityStatus.RECOVERABLE
    )
    assert (
        analyze_bounded_recoverability(config.model_copy(update={"max_plan_length": 1})).status
        is ReachabilityStatus.UNRECOVERABLE
    )


def test_schema_witness_is_revalidated_through_static_action_validation() -> None:
    config = _config(recoverable=True)
    name = min(config.tools)
    analysis = analyze_tool_invocability(config, name, config.tools[name])
    assert analysis.classification is ToolInvocability.INVOCABLE
    assert analysis.witness is not None


def test_exact_empty_schema_is_provably_non_invocable() -> None:
    config = _config(recoverable=True)
    definition = SyntheticToolDefinition(
        arguments_schema={
            "type": "object",
            "properties": {"impossible": False},
            "required": ["impossible"],
            "additionalProperties": False,
        },
        effects={"phase": "irrelevant"},
    )
    analysis = analyze_tool_invocability(config, "empty_tool", definition)
    assert analysis.classification is ToolInvocability.PROVABLY_NON_INVOCABLE
    assert analysis.witness is None


def test_unsupported_satisfiable_composition_is_unprovable_not_empty() -> None:
    config = _config(recoverable=False)
    raw = deepcopy(config.model_dump(mode="json"))
    raw["tools"]["ambiguous_tool"] = {
        "arguments_schema": {
            "type": "object",
            "properties": {"choice": {"anyOf": [{"type": "string"}, {"type": "integer"}]}},
            "required": ["choice"],
            "additionalProperties": False,
        },
        "requires": {"phase": "sealed"},
        "effects": {"completed": True, "phase": "complete"},
    }
    ambiguous = ActionRecoveryConfig.model_validate(raw)
    tool = analyze_tool_invocability(ambiguous, "ambiguous_tool", ambiguous.tools["ambiguous_tool"])
    proof = analyze_bounded_recoverability(ambiguous)
    assert tool.classification is ToolInvocability.UNPROVABLE
    assert proof.status is ReachabilityStatus.UNPROVABLE


def test_found_path_remains_sound_with_an_unprovable_unneeded_tool() -> None:
    config = _config(recoverable=True)
    raw = deepcopy(config.model_dump(mode="json"))
    raw["tools"]["ambiguous_tool"] = {
        "arguments_schema": {
            "type": "object",
            "properties": {"choice": {"oneOf": [{"type": "string"}, {"type": "integer"}]}},
            "required": ["choice"],
            "additionalProperties": False,
        },
        "effects": {"phase": "irrelevant"},
    }
    proof = analyze_bounded_recoverability(ActionRecoveryConfig.model_validate(raw))
    assert proof.status is ReachabilityStatus.RECOVERABLE


def test_lexicographic_tool_order_selects_deterministic_equal_depth_path() -> None:
    config = _config(recoverable=True)
    raw = deepcopy(config.model_dump(mode="json"))
    applicable = next(
        definition
        for definition in raw["tools"].values()
        if all(
            raw["resulting_state"].get(key) == value
            for key, value in definition["requires"].items()
        )
        and definition["effects"] == raw["expected_state"]
    )
    raw["tools"]["000_first"] = applicable
    proof = analyze_bounded_recoverability(ActionRecoveryConfig.model_validate(raw))
    assert proof.status is ReachabilityStatus.RECOVERABLE
    assert proof.path[0].tool == "000_first"


def test_canonical_global_visited_set_suppresses_same_state_cycle() -> None:
    config = _config(recoverable=False)
    raw = deepcopy(config.model_dump(mode="json"))
    raw["tools"]["cycle"] = {
        "arguments_schema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "requires": {"phase": "sealed"},
        "effects": {"phase": "sealed"},
    }
    proof = analyze_bounded_recoverability(ActionRecoveryConfig.model_validate(raw))
    assert proof.status is ReachabilityStatus.UNRECOVERABLE
    assert proof.visited_state_count == 1
    assert proof.maximum_depth_reached == 1


def test_same_tool_different_argument_probe_is_not_r5_and_is_not_causal() -> None:
    loaded = load_benchmark_suite(get_builtin_suite_path("action_recovery.core"))
    case = next(
        item
        for item in loaded.suite.cases
        if "capability-changed-argument-identity" in item.tags
        and "contrastive-variant-recoverable" in item.tags
    )
    goldens = {
        row["case_id"]: row
        for row in map(
            json.loads,
            (
                PROJECT_ROOT
                / "tests"
                / "fixtures"
                / "builtin_suite_goldens"
                / "action-recovery-core-v1.jsonl"
            )
            .read_text()
            .splitlines(),
        )
    }
    incorrect = evaluate(
        EvaluationContext(
            response=GenerationResponse(text=goldens[case.id]["incorrect_response"]),
            specification=case.evaluation,
        )
    )
    correct = evaluate(
        EvaluationContext(
            response=GenerationResponse(text=goldens[case.id]["correct_response"]),
            specification=case.evaluation,
        )
    )
    incorrect_artifact = ActionRecoveryEvaluationArtifact.model_validate(incorrect.artifacts)
    correct_artifact = ActionRecoveryEvaluationArtifact.model_validate(correct.artifacts)
    assert incorrect_artifact.outcome is ActionRecoveryOutcome.RECOVERY_UNSUCCESSFUL
    assert incorrect_artifact.proposal is not None
    assert (
        incorrect_artifact.proposal.actions[0].tool
        == case.evaluation.config["attempted_actions"][1]["tool"]
    )
    assert (
        incorrect_artifact.proposal.actions[0].arguments
        != case.evaluation.config["attempted_actions"][1]["arguments"]
    )
    assert correct_artifact.outcome is ActionRecoveryOutcome.RECOVERED
    assert correct_artifact.proposal is not None
    assert correct_artifact.proposal.actions[0].tool.startswith("prepare_")
