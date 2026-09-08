"""Pure conservative witnesses and bounded synthetic-state reachability."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from jsonschema import Draft202012Validator
from pydantic import JsonValue

from elarabench.action_compliance import (
    ActionComplianceConfig,
    ActionPlanEnvelope,
    PlanValidationStatus,
    ProposedAction,
    SyntheticToolDefinition,
    validate_action_plan,
)
from elarabench.hashing import canonical_json_bytes


class ToolInvocability(StrEnum):
    """Conservative static classification for one synthetic tool."""

    INVOCABLE = "invocable"
    PROVABLY_NON_INVOCABLE = "provably_non_invocable"
    UNPROVABLE = "unprovable"


class ReachabilityStatus(StrEnum):
    """Bounded reachability conclusion under conservative witness analysis."""

    RECOVERABLE = "recoverable"
    UNRECOVERABLE = "unrecoverable"
    UNPROVABLE = "unprovable"


@dataclass(frozen=True, slots=True)
class ToolInvocabilityAnalysis:
    """One tool's static witness classification and deterministic diagnostic."""

    tool: str
    classification: ToolInvocability
    witness: ProposedAction | None
    reason: str


@dataclass(frozen=True, slots=True)
class ReachabilityResult:
    """Non-persisted bounded proof result with an optional minimal path."""

    status: ReachabilityStatus
    path: tuple[ProposedAction, ...]
    tool_analyses: tuple[ToolInvocabilityAnalysis, ...]
    visited_state_count: int
    maximum_depth_reached: int
    reason: str


def _json_copy(value: JsonValue) -> JsonValue:
    copied: JsonValue = json.loads(canonical_json_bytes(value))
    return copied


def _schema_provably_empty(schema: JsonValue) -> bool:
    """Prove emptiness only for a deliberately small exact subset."""
    if schema is False:
        return True
    if schema is True or not isinstance(schema, dict):
        return False
    validator = Draft202012Validator(schema)
    if "const" in schema:
        return not validator.is_valid(_json_copy(schema["const"]))
    enum = schema.get("enum")
    if isinstance(enum, list):
        return not enum or all(not validator.is_valid(value) for value in enum)
    all_of = schema.get("allOf")
    if isinstance(all_of, list) and any(_schema_provably_empty(child) for child in all_of):
        return True
    for keyword in ("anyOf", "oneOf"):
        alternatives = schema.get(keyword)
        if (
            isinstance(alternatives, list)
            and alternatives
            and all(_schema_provably_empty(child) for child in alternatives)
        ):
            return True
    if schema.get("type") == "object" and schema.get("additionalProperties") is False:
        properties = schema.get("properties")
        required = schema.get("required")
        if isinstance(properties, dict) and isinstance(required, list):
            for name in required:
                if not isinstance(name, str) or name not in properties:
                    return True
                if _schema_provably_empty(properties[name]):
                    return True
    if schema.get("type") == "array":
        minimum = schema.get("minItems", 0)
        items = schema.get("items")
        if isinstance(minimum, int) and minimum > 0 and items is not None:
            return _schema_provably_empty(cast(JsonValue, items))
    return False


def analyze_tool_invocability(
    action_config: ActionComplianceConfig,
    tool_name: str,
    definition: SyntheticToolDefinition,
) -> ToolInvocabilityAnalysis:
    """Construct and fully revalidate a witness, or conservatively classify failure."""
    eligible, generated = _generated_schema_value(cast(JsonValue, definition.arguments_schema))
    if eligible and isinstance(generated, dict):
        action = ProposedAction(
            tool=tool_name,
            arguments=generated,
        )
        proposal = ActionPlanEnvelope(type="action", actions=(action,))
        schema_valid = Draft202012Validator(definition.arguments_schema).is_valid(action.arguments)
        plan_valid = (
            validate_action_plan(proposal, action_config).status
            is PlanValidationStatus.VALID
        )
        if schema_valid and plan_valid:
            return ToolInvocabilityAnalysis(
                tool_name,
                ToolInvocability.INVOCABLE,
                action,
                "deterministic witness passed complete schema and Action validation",
            )
    if _schema_provably_empty(cast(JsonValue, definition.arguments_schema)):
        return ToolInvocabilityAnalysis(
            tool_name,
            ToolInvocability.PROVABLY_NON_INVOCABLE,
            None,
            "argument schema is empty under the conservative exact proof subset",
        )
    return ToolInvocabilityAnalysis(
        tool_name,
        ToolInvocability.UNPROVABLE,
        None,
        "witness construction failed without a sound proof of schema emptiness",
    )


def _requirements_met(state: dict[str, JsonValue], definition: SyntheticToolDefinition) -> bool:
    return all(
        key in state and canonical_json_bytes(state[key]) == canonical_json_bytes(value)
        for key, value in definition.requires.items()
    )


def analyze_bounded_reachability(
    tools: dict[str, SyntheticToolDefinition],
    start_state: dict[str, JsonValue],
    goal_state: dict[str, JsonValue],
    depth_bound: int,
    action_config: ActionComplianceConfig,
) -> ReachabilityResult:
    """Run the shared deterministic, conservative bounded BFS."""
    if depth_bound < 0:
        raise ValueError("depth_bound must be nonnegative")
    analyses = tuple(
        analyze_tool_invocability(action_config, name, tools[name]) for name in sorted(tools)
    )
    invocable = tuple(
        analysis for analysis in analyses if analysis.classification is ToolInvocability.INVOCABLE
    )
    queue: deque[tuple[dict[str, JsonValue], int, tuple[ProposedAction, ...]]] = deque(
        [(dict(start_state), 0, ())]
    )
    visited = {canonical_json_bytes(start_state)}
    maximum_depth = 0
    while queue:
        state, depth, path = queue.popleft()
        maximum_depth = max(maximum_depth, depth)
        if depth == depth_bound:
            continue
        for analysis in invocable:
            definition = tools[analysis.tool]
            if not _requirements_met(state, definition):
                continue
            next_state = dict(state)
            next_state.update(
                cast(dict[str, JsonValue], _json_copy(cast(JsonValue, definition.effects)))
            )
            assert analysis.witness is not None
            next_path = (*path, analysis.witness)
            next_depth = depth + 1
            maximum_depth = max(maximum_depth, next_depth)
            identity = canonical_json_bytes(next_state)
            if identity == canonical_json_bytes(goal_state):
                return ReachabilityResult(
                    ReachabilityStatus.RECOVERABLE,
                    next_path,
                    analyses,
                    len(visited) + (identity not in visited),
                    maximum_depth,
                    "bounded BFS reached expected_state",
                )
            if identity in visited:
                continue
            visited.add(identity)
            queue.append((next_state, next_depth, next_path))
    undecidable = tuple(
        analysis.tool
        for analysis in analyses
        if analysis.classification is ToolInvocability.UNPROVABLE
    )
    if undecidable:
        return ReachabilityResult(
            ReachabilityStatus.UNPROVABLE,
            (),
            analyses,
            len(visited),
            maximum_depth,
            f"reachability-relevant tools are undecidable: {undecidable!r}",
        )
    return ReachabilityResult(
        ReachabilityStatus.UNRECOVERABLE,
        (),
        analyses,
        len(visited),
        maximum_depth,
        "complete bounded BFS exhausted every reachable canonical state",
    )


def _generated_schema_value(schema: JsonValue) -> tuple[bool, JsonValue]:
    if schema is False:
        return False, None
    if schema is True or not isinstance(schema, dict):
        return False, None
    if "const" in schema:
        constant_value = _json_copy(schema["const"])
        return Draft202012Validator(schema).is_valid(constant_value), constant_value
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        enum_value = _json_copy(enum[0])
        return Draft202012Validator(schema).is_valid(enum_value), enum_value
    raw_type = schema.get("type")
    if not isinstance(raw_type, str):
        return False, None
    if raw_type == "string":
        generated_value: JsonValue = "x"
    elif raw_type == "integer":
        generated_value = 0
    elif raw_type == "number":
        generated_value = 0.0
    elif raw_type == "boolean":
        generated_value = False
    elif raw_type == "null":
        generated_value = None
    elif raw_type == "array":
        minimum = schema.get("minItems", 0)
        items = schema.get("items")
        if not isinstance(minimum, int) or minimum < 0 or items is None:
            return False, None
        generated: list[JsonValue] = []
        for _ in range(minimum):
            eligible, item = _generated_schema_value(cast(JsonValue, items))
            if not eligible:
                return False, None
            generated.append(item)
        generated_value = generated
    elif raw_type == "object":
        if schema.get("additionalProperties") is not False:
            return False, None
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if (
            not isinstance(properties, dict)
            or not isinstance(required, list)
            or not all(isinstance(name, str) for name in required)
        ):
            return False, None
        required_names = cast(list[str], required)
        property_schemas = properties
        generated_object: dict[str, JsonValue] = {}
        for name in sorted(required_names):
            child = property_schemas.get(name)
            if child is None:
                return False, None
            eligible, item = _generated_schema_value(child)
            if not eligible:
                return False, None
            generated_object[name] = item
        generated_value = generated_object
    else:
        return False, None
    return Draft202012Validator(schema).is_valid(generated_value), generated_value
