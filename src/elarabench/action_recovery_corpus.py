"""Deterministic bounded proof and structural validation for Recovery corpora."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from pydantic import JsonValue

from elarabench.action_compliance import (
    AuthorizationState,
    SyntheticToolDefinition,
)
from elarabench.action_recovery import (
    ActionRecoveryCaseError,
    ActionRecoveryConfig,
    Recoverability,
    validate_action_recovery_case,
)
from elarabench.benchmark import LoadedBenchmarkSuite
from elarabench.hashing import canonical_json_bytes, hash_canonical
from elarabench.models import BenchmarkCase
from elarabench.synthetic_reachability import (
    ReachabilityResult as ReachabilityResult,
)
from elarabench.synthetic_reachability import (
    ReachabilityStatus as ReachabilityStatus,
)
from elarabench.synthetic_reachability import (
    ToolInvocability as ToolInvocability,
)
from elarabench.synthetic_reachability import (
    ToolInvocabilityAnalysis as ToolInvocabilityAnalysis,
)
from elarabench.synthetic_reachability import (
    analyze_bounded_reachability,
)
from elarabench.synthetic_reachability import (
    analyze_tool_invocability as _analyze_tool_invocability,
)

CORE_SUITE_ID = "action_recovery.core"
CORE_SUITE_VERSION = "1.0.0"
GROUP_TAG_PATTERN = re.compile(r"^contrastive-group-ar-pair-\d{2}$")
GROUP_TAG_PREFIX = "contrastive-group-ar-"
VARIANT_TAG_PREFIX = "contrastive-variant-"
VARIANT_RECOVERABILITY = {
    "contrastive-variant-recoverable": Recoverability.RECOVERABLE,
    "contrastive-variant-unrecoverable": Recoverability.UNRECOVERABLE,
}
STATE_TOKEN_PATTERN = re.compile(
    r"(?:^|-)(?:recoverable|unrecoverable|denied|requires-approval|approval-required)(?:-|$)"
)
PRODUCTION_CATEGORIES = {
    "document-workflow",
    "record-lifecycle",
    "notification-routing",
    "inventory-processing",
    "release-coordination",
    "roster-maintenance",
}


class CorpusFindingSeverity(StrEnum):
    """Whether a deterministic finding invalidates the applicable profile."""

    ERROR = "error"
    WARNING = "warning"


class CorpusFindingCode(StrEnum):
    """Stable machine-readable Action Recovery corpus finding identifiers."""

    MALFORMED_RESERVED_TAG = "malformed_reserved_tag"
    MULTIPLE_GROUP_TAGS = "multiple_group_tags"
    MULTIPLE_VARIANT_TAGS = "multiple_variant_tags"
    GROUP_WITHOUT_VARIANT = "group_without_variant"
    VARIANT_WITHOUT_GROUP = "variant_without_group"
    GROUP_SIZE = "group_size"
    GROUP_VARIANTS = "group_variants"
    VARIANT_CONFIG_MISMATCH = "variant_config_mismatch"
    CONTROLLED_PAIR_MISMATCH = "controlled_pair_mismatch"
    NONCANONICAL_OBSERVATION = "noncanonical_observation"
    RECOVERABILITY_CONTRADICTION = "recoverability_contradiction"
    UNPROVABLE_RECOVERABILITY = "unprovable_recoverability"
    CORE_CASE_COUNT = "core_case_count"
    CORE_POPULATION_BALANCE = "core_population_balance"
    CORE_PAIR_COUNT = "core_pair_count"
    CORE_CATEGORY_PROFILE = "core_category_profile"
    CORE_DIFFICULTY_PROFILE = "core_difficulty_profile"
    CORE_CAPABILITY_PROFILE = "core_capability_profile"
    CORE_CASE_ID_LEAKAGE = "core_case_id_leakage"
    CORE_TAG_LEAKAGE = "core_tag_leakage"
    CLASS_EXCLUSIVE_LEAKAGE = "class_exclusive_leakage"
    AUTHORIZATION_DISTRIBUTION_LEAKAGE = "authorization_distribution_leakage"
    UNDECIDABLE_PRODUCTION_TOOL = "undecidable_production_tool"
    MISSING_TRUST_PROBE = "missing_trust_probe"


@dataclass(frozen=True, slots=True)
class ActionRecoveryCorpusFinding:
    """One deterministic, non-persisted corpus validation finding."""

    severity: CorpusFindingSeverity
    code: CorpusFindingCode
    message: str
    case_ids: tuple[str, ...] = ()
    group_id: str | None = None


@dataclass(frozen=True, slots=True)
class ActionRecoveryCorpusFindings:
    """Separated deterministic errors and authoring warnings."""

    errors: tuple[ActionRecoveryCorpusFinding, ...]
    warnings: tuple[ActionRecoveryCorpusFinding, ...]

    @property
    def valid(self) -> bool:
        return not self.errors


def _finding_key(
    finding: ActionRecoveryCorpusFinding,
) -> tuple[str, str, str, tuple[str, ...], str]:
    return (
        finding.severity.value,
        finding.code.value,
        finding.group_id or "",
        finding.case_ids,
        finding.message,
    )


def _json_copy(value: JsonValue) -> JsonValue:
    copied: JsonValue = json.loads(canonical_json_bytes(value))
    return copied


def analyze_tool_invocability(
    config: ActionRecoveryConfig,
    tool_name: str,
    definition: SyntheticToolDefinition,
) -> ToolInvocabilityAnalysis:
    """Compatibility boundary for the Recovery Action-validation view."""
    return _analyze_tool_invocability(config._action_view(), tool_name, definition)


def analyze_bounded_recoverability(config: ActionRecoveryConfig) -> ReachabilityResult:
    """Preserve the Recovery API and its trusted starting state and bound."""
    if config.authorization is not AuthorizationState.AUTHORIZED:
        raise ValueError("bounded recoverability applies only to AUTHORIZED cases")
    assert config.expected_state is not None
    return analyze_bounded_reachability(
        config.tools,
        config.resulting_state,
        config.expected_state,
        config.max_plan_length,
        config._action_view(),
    )


def _config(case: BenchmarkCase) -> ActionRecoveryConfig:
    return ActionRecoveryConfig.model_validate(case.evaluation.config)


def _shape(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {key: _shape(child) for key, child in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(child) for child in value]
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    return "number"


def _payload_properties(value: JsonValue) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            names.update(properties)
        for child in value.values():
            names.update(_payload_properties(child))
    elif isinstance(value, list):
        for child in value:
            names.update(_payload_properties(child))
    return names


def _recoverability_features(
    case: BenchmarkCase,
    config: ActionRecoveryConfig,
) -> dict[str, set[str]]:
    tool_payloads = set().union(
        *(
            _payload_properties(cast(JsonValue, definition.arguments_schema))
            for definition in config.tools.values()
        )
    )
    return {
        "category": {case.category},
        "difficulty": {case.difficulty or ""},
        "tool": set(config.tools),
        "tool_count": {str(len(config.tools))},
        "payload_property": tool_payloads,
        "attempted_plan_length": {str(len(config.attempted_actions))},
        "failed_action_index": {str(config.failed_action_index)},
        "resulting_state_shape": {hash_canonical(_shape(cast(JsonValue, config.resulting_state)))},
        "max_plan_length": {str(config.max_plan_length)},
        "non_variant_tag": {
            tag
            for tag in case.tags
            if tag not in VARIANT_RECOVERABILITY and not tag.startswith(GROUP_TAG_PREFIX)
        },
    }


def _pair_signature(case: BenchmarkCase, config: ActionRecoveryConfig) -> str:
    return hash_canonical(
        {
            "category": case.category,
            "difficulty": case.difficulty,
            "messages": [message.model_dump(mode="json") for message in case.messages[:-1]],
            "configuration": config.model_dump(
                mode="json",
                exclude={
                    "attempted_actions",
                    "outcome_per_action",
                    "resulting_state",
                    "recoverability",
                },
            ),
            "attempted_plan_length": len(config.attempted_actions),
            "failed_action_index": config.failed_action_index,
            "outcome_shape": [outcome.value for outcome in config.outcome_per_action],
            "resulting_state_shape": _shape(cast(JsonValue, config.resulting_state)),
            "tags": sorted(
                tag
                for tag in case.tags
                if not tag.startswith(GROUP_TAG_PREFIX) and tag not in VARIANT_RECOVERABILITY
            ),
        }
    )


def _gated_signature(case: BenchmarkCase, config: ActionRecoveryConfig) -> str:
    user = case.messages[1].content
    marker = "Authorization rule: <trusted-state>"
    normalized_lines = [
        marker if line.startswith("Authorization rule: ") else line for line in user.splitlines()
    ]
    return hash_canonical(
        {
            "category": case.category,
            "difficulty": case.difficulty,
            "system": case.messages[0].content,
            "user": "\n".join(normalized_lines),
            "config": config.model_dump(mode="json", exclude={"authorization"}),
            "tags": sorted(tag for tag in case.tags if tag != "trust-payload-probe"),
        }
    )


def validate_action_recovery_corpus(
    loaded: LoadedBenchmarkSuite,
) -> ActionRecoveryCorpusFindings:
    """Return stable structural/proof findings without executing providers or Runner."""
    cases = tuple(case for case in loaded.suite.cases if case.evaluation.type == "action_recovery")
    configs = {case.id: _config(case) for case in cases}
    production = loaded.suite.id == CORE_SUITE_ID and loaded.suite.version == CORE_SUITE_VERSION
    findings: list[ActionRecoveryCorpusFinding] = []
    groups: dict[str, list[BenchmarkCase]] = defaultdict(list)
    valid_groups = 0

    for case in cases:
        try:
            validate_action_recovery_case(case)
        except ActionRecoveryCaseError as error:
            findings.append(
                ActionRecoveryCorpusFinding(
                    CorpusFindingSeverity.ERROR,
                    CorpusFindingCode.NONCANONICAL_OBSERVATION,
                    str(error),
                    (case.id,),
                )
            )
        group_like = [tag for tag in case.tags if tag.startswith(GROUP_TAG_PREFIX)]
        variant_like = [tag for tag in case.tags if tag.startswith(VARIANT_TAG_PREFIX)]
        group_tags = [tag for tag in group_like if GROUP_TAG_PATTERN.fullmatch(tag)]
        variant_tags = [tag for tag in variant_like if tag in VARIANT_RECOVERABILITY]
        for tag in sorted(set(group_like) - set(group_tags)):
            findings.append(
                ActionRecoveryCorpusFinding(
                    CorpusFindingSeverity.ERROR,
                    CorpusFindingCode.MALFORMED_RESERVED_TAG,
                    f"malformed reserved contrastive group tag {tag!r}",
                    (case.id,),
                )
            )
        for tag in sorted(set(variant_like) - set(variant_tags)):
            findings.append(
                ActionRecoveryCorpusFinding(
                    CorpusFindingSeverity.ERROR,
                    CorpusFindingCode.MALFORMED_RESERVED_TAG,
                    f"malformed reserved contrastive variant tag {tag!r}",
                    (case.id,),
                )
            )
        if len(group_tags) > 1:
            findings.append(
                ActionRecoveryCorpusFinding(
                    CorpusFindingSeverity.ERROR,
                    CorpusFindingCode.MULTIPLE_GROUP_TAGS,
                    "case carries multiple contrastive group tags",
                    (case.id,),
                )
            )
        if len(variant_tags) > 1:
            findings.append(
                ActionRecoveryCorpusFinding(
                    CorpusFindingSeverity.ERROR,
                    CorpusFindingCode.MULTIPLE_VARIANT_TAGS,
                    "case carries multiple contrastive variant tags",
                    (case.id,),
                )
            )
        if group_tags and not variant_tags:
            findings.append(
                ActionRecoveryCorpusFinding(
                    CorpusFindingSeverity.ERROR,
                    CorpusFindingCode.GROUP_WITHOUT_VARIANT,
                    "contrastive group tag requires one variant tag",
                    (case.id,),
                    group_tags[0],
                )
            )
        if variant_tags and not group_tags:
            findings.append(
                ActionRecoveryCorpusFinding(
                    CorpusFindingSeverity.ERROR,
                    CorpusFindingCode.VARIANT_WITHOUT_GROUP,
                    "contrastive variant tag requires one group tag",
                    (case.id,),
                )
            )
        if len(group_tags) == 1:
            groups[group_tags[0]].append(case)

    for group_id, members in sorted(groups.items()):
        case_ids = tuple(sorted(case.id for case in members))
        if len(members) != 2:
            findings.append(
                ActionRecoveryCorpusFinding(
                    CorpusFindingSeverity.ERROR,
                    CorpusFindingCode.GROUP_SIZE,
                    "contrastive Recovery group must contain exactly two cases",
                    case_ids,
                    group_id,
                )
            )
            continue
        variants = {
            tag: case for case in members for tag in case.tags if tag in VARIANT_RECOVERABILITY
        }
        if set(variants) != set(VARIANT_RECOVERABILITY):
            findings.append(
                ActionRecoveryCorpusFinding(
                    CorpusFindingSeverity.ERROR,
                    CorpusFindingCode.GROUP_VARIANTS,
                    "contrastive Recovery group requires one of each variant",
                    case_ids,
                    group_id,
                )
            )
            continue
        valid_groups += 1
        for tag, member in variants.items():
            config = configs[member.id]
            if (
                config.authorization is not AuthorizationState.AUTHORIZED
                or config.recoverability is not VARIANT_RECOVERABILITY[tag]
            ):
                findings.append(
                    ActionRecoveryCorpusFinding(
                        CorpusFindingSeverity.ERROR,
                        CorpusFindingCode.VARIANT_CONFIG_MISMATCH,
                        "variant tag disagrees with trusted Recovery configuration",
                        (member.id,),
                        group_id,
                    )
                )
        if len({_pair_signature(member, configs[member.id]) for member in members}) != 1:
            findings.append(
                ActionRecoveryCorpusFinding(
                    CorpusFindingSeverity.ERROR,
                    CorpusFindingCode.CONTROLLED_PAIR_MISMATCH,
                    "formal Recovery pair differs outside permitted trace/state dimensions",
                    case_ids,
                    group_id,
                )
            )

    proofs: dict[str, ReachabilityResult] = {}
    for case in cases:
        config = configs[case.id]
        if config.authorization is not AuthorizationState.AUTHORIZED:
            continue
        proof = analyze_bounded_recoverability(config)
        proofs[case.id] = proof
        declared = config.recoverability
        objective = (
            Recoverability.RECOVERABLE
            if proof.status is ReachabilityStatus.RECOVERABLE
            else Recoverability.UNRECOVERABLE
            if proof.status is ReachabilityStatus.UNRECOVERABLE
            else None
        )
        if objective is not None and declared is not objective:
            findings.append(
                ActionRecoveryCorpusFinding(
                    CorpusFindingSeverity.ERROR,
                    CorpusFindingCode.RECOVERABILITY_CONTRADICTION,
                    f"declared {declared} but bounded proof found {objective.value}",
                    (case.id,),
                )
            )
        if proof.status is ReachabilityStatus.UNPROVABLE:
            findings.append(
                ActionRecoveryCorpusFinding(
                    CorpusFindingSeverity.ERROR if production else CorpusFindingSeverity.WARNING,
                    CorpusFindingCode.UNPROVABLE_RECOVERABILITY,
                    proof.reason,
                    (case.id,),
                )
            )
        if production:
            undecidable = tuple(
                item.tool
                for item in proof.tool_analyses
                if item.classification is ToolInvocability.UNPROVABLE
            )
            if undecidable:
                findings.append(
                    ActionRecoveryCorpusFinding(
                        CorpusFindingSeverity.ERROR,
                        CorpusFindingCode.UNDECIDABLE_PRODUCTION_TOOL,
                        f"production tools are undecidable: {undecidable!r}",
                        (case.id,),
                    )
                )

    if production:
        if len(cases) != 36 or len(loaded.suite.cases) != 36:
            findings.append(
                ActionRecoveryCorpusFinding(
                    CorpusFindingSeverity.ERROR,
                    CorpusFindingCode.CORE_CASE_COUNT,
                    "action_recovery.core must contain exactly 36 Recovery cases",
                    tuple(sorted(configs)),
                )
            )
        populations = Counter(
            (
                config.authorization.value,
                config.recoverability.value if config.recoverability else "gated",
            )
            for config in configs.values()
        )
        expected_populations = {
            ("AUTHORIZED", "recoverable"): 12,
            ("AUTHORIZED", "unrecoverable"): 12,
            ("DENIED", "gated"): 6,
            ("REQUIRES_APPROVAL", "gated"): 6,
        }
        if dict(populations) != expected_populations:
            findings.append(
                ActionRecoveryCorpusFinding(
                    CorpusFindingSeverity.ERROR,
                    CorpusFindingCode.CORE_POPULATION_BALANCE,
                    "production Recovery populations must be exactly 12/12/6/6",
                    tuple(sorted(configs)),
                )
            )
        if len(groups) != 12 or valid_groups != 12:
            findings.append(
                ActionRecoveryCorpusFinding(
                    CorpusFindingSeverity.ERROR,
                    CorpusFindingCode.CORE_PAIR_COUNT,
                    "action_recovery.core must contain exactly 12 valid formal pairs",
                    tuple(sorted(configs)),
                )
            )
        category_counts = Counter(case.category for case in cases)
        if set(category_counts) != PRODUCTION_CATEGORIES or any(
            count != 6 for count in category_counts.values()
        ):
            findings.append(
                ActionRecoveryCorpusFinding(
                    CorpusFindingSeverity.ERROR,
                    CorpusFindingCode.CORE_CATEGORY_PROFILE,
                    "production Recovery categories must be the six neutral six-case families",
                    tuple(sorted(configs)),
                )
            )
        expected_difficulties = {"easy": 4, "medium": 4, "hard": 4}
        for label, selected in (
            (
                "recoverable",
                [
                    case
                    for case in cases
                    if configs[case.id].recoverability is Recoverability.RECOVERABLE
                ],
            ),
            (
                "unrecoverable",
                [
                    case
                    for case in cases
                    if configs[case.id].recoverability is Recoverability.UNRECOVERABLE
                ],
            ),
            (
                "denied",
                [
                    case
                    for case in cases
                    if configs[case.id].authorization is AuthorizationState.DENIED
                ],
            ),
            (
                "approval",
                [
                    case
                    for case in cases
                    if configs[case.id].authorization is AuthorizationState.REQUIRES_APPROVAL
                ],
            ),
        ):
            expected = (
                expected_difficulties
                if len(selected) == 12
                else {"easy": 2, "medium": 2, "hard": 2}
            )
            if Counter(case.difficulty for case in selected) != expected:
                findings.append(
                    ActionRecoveryCorpusFinding(
                        CorpusFindingSeverity.ERROR,
                        CorpusFindingCode.CORE_DIFFICULTY_PROFILE,
                        f"production {label} difficulty distribution is unbalanced",
                        tuple(sorted(case.id for case in selected)),
                    )
                )
        capability_tags = {
            "capability-changed-tool",
            "capability-changed-order",
            "capability-prerequisite-insertion",
            "capability-multi-action",
            "capability-alternate-route",
            "capability-terminal-trap",
            "capability-changed-argument-identity",
        }
        authorized = tuple(
            case
            for case in cases
            if configs[case.id].authorization is AuthorizationState.AUTHORIZED
        )
        feature_classes: dict[tuple[str, str], tuple[set[Recoverability], set[str]]] = {}
        for case in authorized:
            config = configs[case.id]
            assert config.recoverability is not None
            for feature, values in _recoverability_features(case, config).items():
                for value in values:
                    classes, feature_case_ids = feature_classes.setdefault(
                        (feature, value), (set(), set())
                    )
                    classes.add(config.recoverability)
                    feature_case_ids.add(case.id)
        for (feature, value), (classes, feature_case_ids) in sorted(feature_classes.items()):
            if len(classes) == 1:
                findings.append(
                    ActionRecoveryCorpusFinding(
                        CorpusFindingSeverity.ERROR,
                        CorpusFindingCode.CLASS_EXCLUSIVE_LEAKAGE,
                        f"{feature} value {value!r} is exclusive to one Recovery class",
                        tuple(sorted(feature_case_ids)),
                    )
                )
        for tag in sorted(capability_tags):
            if not any(tag in case.tags for case in authorized):
                findings.append(
                    ActionRecoveryCorpusFinding(
                        CorpusFindingSeverity.ERROR,
                        CorpusFindingCode.CORE_CAPABILITY_PROFILE,
                        f"production profile lacks required capability tag {tag!r}",
                        tuple(sorted(case.id for case in authorized if tag in case.tags)),
                    )
                )
        for case in cases:
            if STATE_TOKEN_PATTERN.search(case.id):
                findings.append(
                    ActionRecoveryCorpusFinding(
                        CorpusFindingSeverity.ERROR,
                        CorpusFindingCode.CORE_CASE_ID_LEAKAGE,
                        "production case ID encodes Recovery/authorization class",
                        (case.id,),
                    )
                )
            leaking = tuple(
                sorted(
                    tag
                    for tag in case.tags
                    if tag not in VARIANT_RECOVERABILITY and STATE_TOKEN_PATTERN.search(tag)
                )
            )
            if leaking:
                findings.append(
                    ActionRecoveryCorpusFinding(
                        CorpusFindingSeverity.ERROR,
                        CorpusFindingCode.CORE_TAG_LEAKAGE,
                        f"non-reserved tags encode class: {leaking!r}",
                        (case.id,),
                    )
                )
        gated = tuple(
            case
            for case in cases
            if configs[case.id].authorization is not AuthorizationState.AUTHORIZED
        )
        gated_signatures: dict[str, set[AuthorizationState]] = defaultdict(set)
        gated_ids: dict[str, set[str]] = defaultdict(set)
        for case in gated:
            signature = _gated_signature(case, configs[case.id])
            gated_signatures[signature].add(configs[case.id].authorization)
            gated_ids[signature].add(case.id)
        for signature, states in sorted(gated_signatures.items()):
            if states != {
                AuthorizationState.DENIED,
                AuthorizationState.REQUIRES_APPROVAL,
            }:
                findings.append(
                    ActionRecoveryCorpusFinding(
                        CorpusFindingSeverity.ERROR,
                        CorpusFindingCode.AUTHORIZATION_DISTRIBUTION_LEAKAGE,
                        "gated structure lacks a matched opposite-authorization analogue",
                        tuple(sorted(gated_ids[signature])),
                    )
                )
        trust_states = {
            configs[case.id].authorization for case in gated if "trust-payload-probe" in case.tags
        }
        if trust_states != {
            AuthorizationState.DENIED,
            AuthorizationState.REQUIRES_APPROVAL,
        }:
            findings.append(
                ActionRecoveryCorpusFinding(
                    CorpusFindingSeverity.ERROR,
                    CorpusFindingCode.MISSING_TRUST_PROBE,
                    "production requires matched gated approval-like payload probes",
                    tuple(sorted(case.id for case in gated if "trust-payload-probe" in case.tags)),
                )
            )

    errors = tuple(
        sorted(
            (finding for finding in findings if finding.severity is CorpusFindingSeverity.ERROR),
            key=_finding_key,
        )
    )
    warnings = tuple(
        sorted(
            (finding for finding in findings if finding.severity is CorpusFindingSeverity.WARNING),
            key=_finding_key,
        )
    )
    return ActionRecoveryCorpusFindings(errors=errors, warnings=warnings)
