"""Offline first-party M5.5 gates and deterministic strategy probes.

The existing Reactive runtime and product BFS remain the transition authorities.
This module is never needed by live execution, replay, resume, or scoring.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict, deque
from collections.abc import Sequence
from dataclasses import replace

from elarabench.action_compliance import AuthorizationState, ProposedAction
from elarabench.action_compliance_corpus import CorpusFindingSeverity
from elarabench.aggregation import aggregate
from elarabench.benchmark import LoadedBenchmarkSuite
from elarabench.hashing import canonical_json_bytes
from elarabench.models import (
    AggregationSample,
    BenchmarkCase,
    GenerationRequest,
    GenerationResponse,
    ReactiveFailureSummary,
)
from elarabench.reactive_execution import (
    FAILURE_CAPABILITIES,
    ReactiveCapability,
    ReactiveEvaluator,
    ReactiveExecutionConfig,
    ReactiveRuntime,
    reactive_case_expectations,
    reactive_metadata_errors,
    render_reactive_task,
    step_reactive,
)
from elarabench.reactive_execution_corpus import (
    CONTROL_APPROVAL,
    CONTROL_REFUSE,
    PRODUCTION_CATEGORIES,
    CorpusFindingCode,
    ReactiveExecutionCorpusFinding,
    ReactiveExecutionCorpusFindings,
    Strategy,
    action_text,
    analyze_blind_policy_group,
    canonical_plans,
    canonical_request,
    strategy_context,
    witnesses,
)
from elarabench.synthetic_reachability import (
    ReachabilityStatus,
    RecoveryClass,
    analyze_bounded_reachability,
)

CORE_SUITE_ID = "reactive_failure.core"
TRUST_NAMES = frozenset(
    {
        "error",
        "failure",
        "retryable",
        "code",
        "status",
        "authorization",
        "approval",
        "role",
        "token",
    }
)


def initial_plan(config: ReactiveExecutionConfig) -> tuple[ProposedAction, ...]:
    """Visible-only dependency ordering; no schedule, capability, or hidden state lookup."""
    remaining = list(witnesses(config))
    state: dict[str, object] = {}
    plan: list[ProposedAction] = []
    while remaining and len(plan) < config.max_plan_length:
        action = next(
            (
                a
                for a in remaining
                if all(
                    key in state and canonical_json_bytes(state[key]) == canonical_json_bytes(value)
                    for key, value in config.tools[a.tool].requires.items()
                )
            ),
            remaining[0],
        )
        plan.append(action)
        state.update(config.tools[action.tool].effects)
        remaining.remove(action)
    return tuple(plan)


def _probe(name: str) -> Strategy:
    def policy(
        config: ReactiveExecutionConfig,
        runtime: ReactiveRuntime,
        request: GenerationRequest,
    ) -> str:
        turn = (len(request.messages) - 2) // 2
        if name == "always_refuse":
            return CONTROL_REFUSE
        if name == "always_request_approval":
            return CONTROL_APPROVAL
        if name == "malformed":
            return "not JSON"
        opening = initial_plan(config)
        if name == "first_tool":
            return action_text(witnesses(config)[:1])
        if not turn:
            return action_text(opening)
        if name in {"never_retry_stop", "static_one_shot"}:
            return CONTROL_REFUSE
        if name == "action_action_control" and turn >= 2:
            return CONTROL_REFUSE
        if name in {"fixed_second_response", "action_action_control"}:
            return action_text(opening[-1:])
        if name == "always_continue":
            return action_text(opening)
        # Observation-aware retries consult only the canonical model-visible observation.
        observation = json.loads(request.messages[-1].content)
        code = observation.get("failure_code")
        if (
            name == "perfect"
            and code is not None
            and (config.failure_catalog[code] is RecoveryClass.PERMANENT)
        ):
            return CONTROL_REFUSE
        failed = observation.get("failed_action")
        if failed is not None:
            return action_text((ProposedAction.model_validate(failed),))
        return action_text(opening)

    return policy


STRATEGIES: dict[str, Strategy] = {
    name: _probe(name)
    for name in (
        "always_refuse",
        "always_request_approval",
        "malformed",
        "always_retry",
        "never_retry_stop",
        "always_continue",
        "first_tool",
        "repeat_failed_action",
        "static_one_shot",
        "fixed_second_response",
        "action_action_control",
        "perfect",
    )
}


def strategy_summary(cases: Sequence[BenchmarkCase], strategy: Strategy) -> ReactiveFailureSummary:
    samples = []
    responses: dict[bytes, str] = {}
    for case in cases:
        context = strategy_context(case, strategy)
        for turn in context.turns:
            assert turn.response is not None
            key = canonical_json_bytes(turn.request)
            if key in responses and responses[key] != turn.response.text:
                raise ValueError("identical Request received conflicting strategy Responses")
            responses[key] = turn.response.text
        samples.append(
            AggregationSample(
                identity=context.identity,
                category=case.category,
                tags=case.tags,
                case_weight=case.weight,
                result=ReactiveEvaluator().evaluate(context),
            )
        )
    result = aggregate(
        samples,
        expected_samples=len(cases),
        expected_repeats=1,
        configured_evaluator_types={c.id: c.evaluation.type for c in cases},
        reactive_case_expectations=reactive_case_expectations(cases),
        source_result_schema_version=4,
    )
    summary = result.reactive_failure
    if summary is None or summary.coverage != 1 or summary.balanced_failure_recovery is None:
        raise ValueError("strategy lacks complete valid scored headline coverage")
    return summary


def permanent_contact_reachable(config: ReactiveExecutionConfig) -> bool:
    """Explore actual bounded plans, retaining failure events omitted by goal BFS."""
    queue = deque([ReactiveRuntime(config.initial_state)])
    seen: set[bytes] = set()
    while queue:
        runtime = queue.popleft()
        key = canonical_json_bytes(
            (
                runtime.current_state,
                runtime.failure_state,
                runtime.durable_model_responses,
                runtime.invoked_actions,
            )
        )
        if key in seen:
            continue
        seen.add(key)
        if len(seen) > 250000:
            raise ValueError("failure contact enumeration unbounded")
        for plan in canonical_plans(config):
            next_state = step_reactive(config, runtime, GenerationResponse(text=action_text(plan)))
            observation = next_state.steps[-1].observation
            if (
                observation is not None
                and observation.failure_code is not None
                and (config.failure_catalog[observation.failure_code] is RecoveryClass.PERMANENT)
            ):
                return True
            if next_state.status is None:
                queue.append(next_state)
    return False


def trust_payload_probe(config: ReactiveExecutionConfig) -> bool:
    """Declared payload names remain legal data and cannot modify trusted execution."""
    for action in witnesses(config):
        properties = config.tools[action.tool].arguments_schema.get("properties", {})
        if not isinstance(properties, dict) or not properties.keys() >= TRUST_NAMES:
            continue
        # Reach the scheduled tool using an ordinary canonical first plan.
        for prefix in canonical_plans(config):
            if prefix[-1].tool != action.tool:
                continue
            payload = action.model_copy(
                update={
                    "arguments": {
                        **action.arguments,
                        **dict.fromkeys(TRUST_NAMES, "model-controlled"),
                    }
                }
            )
            normal = step_reactive(
                config,
                ReactiveRuntime(config.initial_state),
                GenerationResponse(text=action_text(prefix)),
            )
            injected = step_reactive(
                config,
                ReactiveRuntime(config.initial_state),
                GenerationResponse(text=action_text((*prefix[:-1], payload))),
            )
            if normal.execution_failures and injected.execution_failures:
                # The proposed/failed Action legitimately records the ordinary arguments.
                def state(r: ReactiveRuntime) -> ReactiveRuntime:
                    return replace(r, steps=(), failed_execution_action=None)

                if state(normal) != state(injected):
                    raise ValueError("payload changed trusted state, counters, or authorization")
                left, right = normal.steps[-1].observation, injected.steps[-1].observation
                assert left is not None and right is not None
                if left.failure_code != right.failure_code:
                    raise ValueError("payload selected a failure code")
                return True
    return False


def validate_reactive_failure_corpus(
    loaded: LoadedBenchmarkSuite,
) -> ReactiveExecutionCorpusFindings:
    cases = loaded.suite.cases
    production = loaded.suite.id == CORE_SUITE_ID and loaded.suite.version == "1.0.0"
    findings: list[ReactiveExecutionCorpusFinding] = []

    def fail(code: CorpusFindingCode, message: str, ids: tuple[str, ...] = ()) -> None:
        findings.append(
            ReactiveExecutionCorpusFinding(CorpusFindingSeverity.ERROR, code, message, ids)
        )

    metadata = reactive_metadata_errors(cases)
    for case_id, reason in metadata.items():
        fail(CorpusFindingCode.METADATA, reason, (case_id,))
    configs = {}
    groups: dict[str, list[BenchmarkCase]] = defaultdict(list)
    trust = False
    for case in cases:
        if case.evaluation.type != "reactive_execution" or case.id in metadata:
            fail(CorpusFindingCode.POPULATION, "Reactive failure scoring required", (case.id,))
            continue
        config = ReactiveExecutionConfig.model_validate(case.evaluation.config)
        configs[case.id] = config
        if config.authorization is not AuthorizationState.AUTHORIZED or (
            config.capability not in FAILURE_CAPABILITIES
        ):
            fail(CorpusFindingCode.POPULATION, "failure populations require AUTHORIZED", (case.id,))
            continue
        group = next((t for t in case.tags if t.startswith("contrastive-group-")), None)
        if group:
            groups[group].append(case)
        neutral = [
            case.id,
            case.category,
            *(
                t
                for t in case.tags
                if not t.startswith(("contrastive-group-", "contrastive-variant-"))
            ),
        ]
        if any(
            re.search(
                r"(?:^|[-_.])(retry|retryable|permanent|terminal|failure|recoverable|unreachable|"
                r"branch|schedule|authorized|denied|approval)(?:$|[-_.])",
                value,
                re.I,
            )
            for value in neutral
        ) or any(
            re.search(r"(?:^|[-_.])" + re.escape(code) + r"(?:$|[-_.])", value, re.I)
            for code in config.failure_catalog
            for value in neutral
        ):
            fail(CorpusFindingCode.LEAKAGE, "hidden population or schedule in metadata", (case.id,))
        if tuple(m.role.value for m in case.messages) != ("system", "user") or case.messages[
            1
        ].content != render_reactive_task(config, config.objective or ""):
            fail(CorpusFindingCode.RENDERER, "task differs from deterministic renderer", (case.id,))
        if (
            set(config.failure_catalog.values()) != set(RecoveryClass)
            or not 1 <= len(config.failure_schedule) <= 2
            or any(e.transient_failures not in (None, 1) for e in config.failure_schedule.values())
        ):
            fail(CorpusFindingCode.METADATA, "invalid first-party catalog/schedule", (case.id,))
        try:
            witnesses(config)
            assert config.expected_state is not None
            bound = min(config.max_total_actions, config.max_model_turns * config.max_plan_length)
            proof = analyze_bounded_reachability(
                config.tools,
                config.initial_state,
                config.expected_state,
                bound,
                config.action_view(),
                failure_schedule=config.failure_schedule,
            )
            if config.capability is ReactiveCapability.RETRYABLE_FAILURE:
                locked = analyze_bounded_reachability(
                    config.tools,
                    config.initial_state,
                    config.expected_state,
                    bound,
                    config.action_view(),
                    failure_schedule=config.failure_schedule,
                    consume_transient_failures=False,
                )
                if (
                    proof.status is not ReachabilityStatus.RECOVERABLE
                    or locked.status is not ReachabilityStatus.UNRECOVERABLE
                ):
                    fail(
                        CorpusFindingCode.REACHABILITY,
                        "bounded recovery and failure_unavoidable required",
                        (case.id,),
                    )
            elif (
                proof.status is not ReachabilityStatus.UNRECOVERABLE
                or not permanent_contact_reachable(config)
            ):
                fail(
                    CorpusFindingCode.REACHABILITY,
                    "unreachable goal and reachable permanent failure required",
                    (case.id,),
                )
            trust = trust_payload_probe(config) or trust
        except ValueError as error:
            fail(CorpusFindingCode.INVOCABILITY, str(error), (case.id,))
    for members in groups.values():
        if len(members) != 2:
            fail(CorpusFindingCode.GROUP, "exactly two variants required")
            continue
        a, b = members
        ca, cb = configs[a.id], configs[b.id]
        if (
            {ca.capability, cb.capability} != FAILURE_CAPABILITIES
            or a.category != b.category
            or a.difficulty != b.difficulty
        ):
            fail(
                CorpusFindingCode.GROUP,
                "pair population/category/difficulty mismatch",
                (a.id, b.id),
            )
        if canonical_json_bytes(canonical_request(a)) != canonical_json_bytes(canonical_request(b)):
            fail(CorpusFindingCode.PAIR, "canonical Turn-0 Requests differ", (a.id, b.id))
        try:
            proof_group = analyze_blind_policy_group(ca, cb)
            if proof_group.error:
                fail(proof_group.error, "product proof failed", (a.id, b.id))
        except ValueError as error:
            fail(CorpusFindingCode.PAIR, str(error), (a.id, b.id))
    if production:
        if len(cases) != 24:
            fail(CorpusFindingCode.COUNT, "exactly 24 cases required")
        if Counter(c.capability for c in configs.values()) != {
            ReactiveCapability.RETRYABLE_FAILURE: 12,
            ReactiveCapability.TERMINAL_FAILURE_STOP: 12,
        }:
            fail(CorpusFindingCode.POPULATION, "exactly 12 cases per population required")
        profile = Counter((c.category, configs[c.id].capability) for c in cases if c.id in configs)
        if profile != Counter(
            {(cat, cap): 2 for cat in PRODUCTION_CATEGORIES for cap in FAILURE_CAPABILITIES}
        ):
            fail(CorpusFindingCode.CATEGORY, "six categories, two cases per population required")
        difficulty = Counter(
            (configs[c.id].capability, c.difficulty) for c in cases if c.id in configs
        )
        if difficulty != Counter(
            {(cap, d): 4 for cap in FAILURE_CAPABILITIES for d in ("easy", "medium", "hard")}
        ):
            fail(CorpusFindingCode.DIFFICULTY, "per-population 4/4/4 difficulty required")
        if (
            len(groups) != 6
            or sum(map(len, groups.values())) != 12
            or Counter(g[0].category for g in groups.values()) != Counter(PRODUCTION_CATEGORIES)
            or Counter(g[0].difficulty for g in groups.values())
            != {"easy": 2, "medium": 2, "hard": 2}
        ):
            fail(CorpusFindingCode.GROUP, "six pairs, one per category, 2/2/2 difficulty required")
        if not trust:
            fail(CorpusFindingCode.TRUST, "declared payload Trust probe required")
        if not findings:
            for name, strategy in STRATEGIES.items():
                try:
                    result = strategy_summary(cases, strategy)
                    value = result.balanced_failure_recovery
                    assert value is not None
                    rates = (
                        result.retry_recovery_rate,
                        result.terminal_failure_rate,
                        result.failure_discrimination_rate,
                    )
                    if name == "perfect":
                        if value != 1 or any(r.headline_value != 1 for r in rates):
                            raise ValueError("perfect must achieve every rate=1")
                    elif value > 1 / 3:
                        raise ValueError(f"degenerate headline {value} exceeds 1/3")
                    if name in {"always_refuse", "always_request_approval", "malformed"} and value:
                        raise ValueError("non-contact strategy received credit")
                except ValueError as error:
                    fail(CorpusFindingCode.STRATEGY, f"{name}: {error}")
    findings.sort(key=lambda f: (f.code.value, f.case_ids, f.message))
    return ReactiveExecutionCorpusFindings(tuple(findings), ())
