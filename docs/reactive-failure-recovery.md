# M5.5 — Reactive Execution Failure Recovery

## Status and authority

**IMPLEMENTED — READY FOR FINAL ACCEPTANCE.**

This document is the normative M5.5 architecture authority. It records the finalized, corrected
design and governs the implementation and its architecture gate. The runtime, scoring, summary,
proof, corpus, historical lifecycle, and distribution contracts below are implemented and verified.
Semantic changes require an explicit architecture revision.

[M5.4 Reactive Execution](reactive-execution.md) remains the authority for implemented M5.4
semantics. This design does not reopen M5.1–M5.4. M5.5 uses `reactive_execution` evaluator
`1.2.0`, nine Built-ins, and 258 production cases. It introduces no new top-level evaluator family. M5.4 cases without failure
configuration retain their existing meaning.

[M5.6 Partially Observable Reactive Execution](reactive-observability.md) is
**DESIGNED / RATIFIED — NOT YET IMPLEMENTED** and governs the planned evaluator `1.3.0` extension.
It leaves M5.5 outcomes, failure predicates, product proof, and corpus unchanged. Its first-party
suite requires empty failure catalogs and schedules. M5.6 capability success is E5 only, including
custom suites: E11 remains a legal shared-evaluator outcome but earns no M5.6 success.
Planned Summary v9 adds a disjoint
observability block under the exact presence contract in that specification. Current M5.5 remains
evaluator `1.2.0`, Summary v8, and nine Built-ins / 258 cases.

## Capability boundary

M5.5 measures whether a worker can distinguish and correctly react to deterministic,
benchmark-generated execution failures after observing them. It adds exactly one capability
dimension: **Reactive Execution Failure Recovery**.

It adds no real tools, external or persistent environments, dynamic authorization, human approval,
provider-native Tool APIs, routing or fallback, multi-agent execution, autonomous agents, random
failures, or provider/network failure semantics. Provider, network, process, and other
infrastructure failures remain `ERROR`, outside the behavioral taxonomy. Trusted synthetic
`execution_failed` is benchmark behavior and MUST NOT be collapsed into infrastructure `ERROR`.

## Trusted failure configuration

The trusted evaluator configuration adds this exact split:

```text
failure_catalog: dict[Identifier, RecoveryClass] = {}
failure_schedule: dict[Identifier, FailureScheduleEntry] = {}

RecoveryClass:
    retryable
    permanent

FailureScheduleEntry:
    code: Identifier
    trigger: dict[str, JsonValue] = {}
    transient_failures: int | None = None
```

The catalog maps failure codes to recovery classes. The schedule maps tool identifiers to their
trusted failure entries. Every scheduled code MUST exist in `failure_catalog`. A nonempty schedule
requires a nonempty catalog. `transient_failures` is present if and only if the catalog class is
`retryable`; permanent entries have no transient counter. The schema may support values 1 through
2, but first-party `reactive_failure.core` MUST require `transient_failures == 1`.

The normative catalog meaning is:

> retryable — repeating the same Action is the prescribed recovery and will succeed within the
> benchmark's bounded retry budget.

This is not a generic promise that the next retry succeeds for arbitrary custom suites whose
schema may allow two transient failures. Permanent failures continue while their trusted trigger
predicate holds.

### Visibility and identity

Both catalog and schedule are trusted, identity-bearing, runtime-relevant configuration. They
participate in the existing snapshot, content-hash, fingerprint, and resume identity safeguards.
Their visibility differs:

| Configuration | Model visibility |
| --- | --- |
| Complete `failure_catalog` | MODEL-VISIBLE through deterministic task rendering before execution. |
| `failure_schedule` | HIDDEN, including tool association, trigger, selected code, and transient counter. |

Before execution, the model knows the complete failure-rule catalog but not which code will occur,
on which tool, under which trigger, or how much hidden transient failure state remains. An
observation reveals the trusted `failure_code` only after `execution_failed` occurs. The model
looks up its recovery class in the already-visible catalog.

### Contrastive Turn-0 identity

Contrastive variants MUST have byte-identical canonical Turn-0 `GenerationRequest`s. They share:

- objective and authorization instruction;
- tools, argument schemas, `requires`, and `effects`;
- the complete `failure_catalog`;
- all budgets and `expected_state` where applicable;
- generation parameters, thinking, effective seed, timeout, and response format.

They may differ only in hidden trusted evaluator state such as `initial_state`,
`failure_schedule`, capability, and group tags. The deterministic task renderer MUST expose the
same complete catalog to both variants. Sharing `expected_state` does not make its raw machine
representation model-visible. Hidden configuration MUST NOT leak through prompts or metadata.

## Sequential runtime failure semantics

Execution remains sequential. After the unchanged whole-plan static validation and remaining
Action-budget check, each invoked Action follows this order:

| Condition | Outcome | Action cost | Effects and plan continuation |
| --- | --- | ---: | --- |
| `requires` not satisfied | `precondition_failed` | 1 | No effect; stop plan; suffix `not_executed`. |
| `requires` satisfied and trusted failure schedule active | `execution_failed` | 1 | No ordinary effect; stop plan; suffix `not_executed`; emit trusted `failure_code`. |
| Otherwise | `applied` | 1 | Apply normal effects and continue sequentially. |

A retryable execution failure decrements its hidden transient counter while leaving ordinary
state unchanged. A permanent execution failure leaves ordinary state unchanged, has no transient
counter, and continues failing while its trigger predicate holds. An unexecuted suffix consumes
zero Actions. Failed invocations consume the Action budget. Goal completion still wins over
exhaustion. An oversized plan executes no prefix, preserving the M5.4 whole-plan budget check.

The following classes remain distinct:

- static invalid plan: E2;
- `precondition_failed`: existing M5.4 behavioral semantics;
- `execution_failed`: new trusted benchmark behavior;
- provider/network/process failure: `ERROR`, outside the behavioral taxonomy.

## Observation and rendering semantics

Failure-enabled cases use `reactive_observation_v2`. `outcome_per_action` gains
`execution_failed`. An observation may include `failure_code` only for `execution_failed`.
It MUST NOT directly expose recovery class, `transient_failures`, trigger, hidden schedule,
`expected_state`, capability, or reachability classification.

The `reactive_observation_rendering_v2` bump is required because canonical observation
rendering now represents the expanded v2 observation vocabulary, including
`execution_failed`/`failure_code`. It is NOT caused merely by the TASK renderer displaying
`failure_catalog`. The visible failure catalog belongs to deterministic task rendering. This
design introduces no new task-rendering semantic identifier.

M5.4 cases retain v1 observation and outcome semantics. Failure observations remain within the
existing turn Request/Response evidence and are reproducible by offline replay.

## Bounded reachability with failure state

M5.5 extends the existing bounded reachability node to:

```text
(state, failure_state)
```

`failure_state` is the canonical vector of remaining retryable transient counters. Permanent
failures need no stored counter: they are evaluated from trusted trigger predicates. Every graph
edge costs exactly one Action invocation. For tool `t`:

| Condition | Reachability transition | Cost |
| --- | --- | ---: |
| `requires` false | No useful reachability edge. | An actual invocation still costs 1. |
| Retryable failure active, counter > 0 | `(state, f) -> (state, f with f[t]-1)` | 1 Action; no ordinary effect. |
| Permanent failure active | No useful goal-reachability edge. | An actual invocation still costs 1. |
| Normal execution | `(state, f) -> (state after effects, f)` | 1 Action. |

Precondition-failure and permanent execution-failure no-op edges are omitted because they consume
budget while changing neither ordinary state nor failure state. They cannot create a shorter goal
path. Transient failure edges MUST NOT be omitted: consuming them changes `failure_state` and can
unlock a future successful invocation.

This omission is specific to goal reachability. The product capability proof MUST still simulate
actual permanent failures, including their Action consumption and failure-contact evidence.
Runtime and reachability MUST agree exactly on ordinary state, failure state, and Action
consumption. With `failure_schedule={}`, extended reachability MUST be equivalent to M5.4.

## Outcome taxonomy and precedence

Preserve E1–E10 exactly as defined for M5.4. Add only:

| Outcome | Identifier | Meaning |
| --- | --- | --- |
| E11 | `completed_after_execution_failure` | Goal reached after at least one actual `execution_failed` event. |
| E12 | `futile_retry` | Incomplete termination with a state-sensitive futile retry of a permanent execution failure. |

E11 takes precedence over E6 when both precondition and execution failures occurred before
completion. Goal completion continues to win over exhaustion.

An E12 futile-retry occurrence is a valid plan repeating as its first Action the Action that most
recently produced a PERMANENT execution failure from canonically unchanged state. It does not
apply to retryable failures: repeating a retryable Action is legitimate recovery. Like the
existing futile-repeat diagnostic, this history distinguishes eventual incomplete exhaustion
(E12 versus E7/E10); it does not prevent later capability success or override valid Control.

Correct terminal Control after permanent execution failure remains E9. Premature Control while
recovery remains possible remains E8. Provider `ERROR` and evaluator `INVALID` remain outside
the behavioral taxonomy.

The existing M5.4 per-turn ordering remains unchanged before post-execution classification.
After execution, classify goal completion first: E11 if execution failure occurred, otherwise
E6 if precondition failure occurred, otherwise E5. Only if the goal was not reached, classify
exhaustion when `turns_remaining == 0 OR actions_remaining == 0` in this exact order:

1. E12 if at least one qualifying permanent execution-failure futile-retry occurrence was recorded.
2. Otherwise E7 if at least one qualifying M5.4 precondition futile-repeat occurrence was recorded.
3. Otherwise E10.

Thus **E12 > E7 > E10** for exhaustion classification, including when both kinds of occurrence
were recorded. This ordering affects diagnostic outcome identity only. It MUST NOT alter ordinary
state, `failure_state`, budgets, capability-success reachability, or the ProductState omission
proof. All E12/E7/E10 remain dead for M5.5 capability success. Without completion or exhaustion,
execution continues under the existing turn semantics.

## Capability populations and sample scoring

Extend `ReactiveCapability` with exactly:

- `retryable_failure`;
- `terminal_failure_stop`.

Do not add `alternate_path_failure`. That population is intentionally rejected: successful
alternate routing cannot be made both unavoidable and recoverable while also requiring
execution-failure contact. It is not a deferred third M5.5 population.

The population-specific capability predicates are:

```text
retryable_failure success:
    outcome == E11

terminal_failure_stop success:
    outcome == E9 AND execution_failure_seen
```

`execution_failure_seen` means at least one actual benchmark-generated `execution_failed` event
occurred. Every M5.5 capability success MUST require that contact. E5 and E6 in either M5.5
population FAIL. A Turn-0 Control before any execution failure receives zero M5.5 capability
credit even if the hidden benchmark is globally unreachable and Control is classified E9.
Existing M5.4 population scoring remains unchanged. Scoring eligibility rejects nonempty
failure schedules attached to M5.4 populations before provider contact: their preserved
summary taxonomy is E1–E10. Failure-enabled scoring uses the two M5.5 populations.

## Repeat-first axes and headline

For each configured eligible case, divide behavioral and capability counts by its actual observed
`SCORED` repeats, NEVER by `expected_repeats`. `observed_case_count` is the number of configured
eligible cases with at least one scored behavioral result. Missing, `ERROR`, `INVALID`, and
`PENDING_REVIEW` repeats affect coverage, not the behavioral mass denominator. Preserve the
existing separation between repeat-normalized mass, configured population denominators, and
full-coverage headline availability.

```text
retry_recovery_rate =
    repeat-normalized mass(E11) over retryable_failure

terminal_failure_rate =
    repeat-normalized mass(E9 AND execution_failure_seen) over terminal_failure_stop

failure_discrimination_rate =
    macro average over contrastive groups of:
        min(capability_success_mass_variant_A, capability_success_mass_variant_B)

balanced_failure_recovery =
    (retry_recovery_rate + terminal_failure_rate + failure_discrimination_rate) / 3
```

Each contrastive variant uses its own population-specific capability predicate. Case capability
masses are computed before group minima and group macro averaging. The three headline axes have
equal macro weights. No axis may award success without execution-failure contact. Incomplete
required population or group coverage cannot be presented as a complete headline.

## Strict summary population ownership

`ReactiveFailureSummary` uses semantic `reactive_failure_summary_v1` and scoring semantic
`reactive_failure_scoring_v1`.

| Summary | Exclusive eligible populations |
| --- | --- |
| `ReactiveExecutionSummary` | `first_pass`, `recovery_opportunity`, `terminal_unreachable`, DENIED, REQUIRES_APPROVAL. |
| `ReactiveFailureSummary` | `retryable_failure`, `terminal_failure_stop`. |

The populations MUST NOT cross-contaminate `eligible_case_ids`, `expected_case_count`,
`observed_case_count`, `expected_sample_count`, `scored_sample_count`, coverage, outcomes,
diagnostics, partition validation, or headline availability. Sharing the top-level evaluator
family is not permission to merge these populations. M5.4 authorization gates do not become
missing populations of a pure M5.5 failure summary.

### Summary semantic schema v8

Content-dependent version selection is:

```text
8 if reactive_failure summary exists
else 7 if reactive_execution summary exists
else 6 if action_recovery summary exists
else 5 if action_compliance summary exists
else 4
```

| Run contents after M5.5 implementation | Summary version | Blocks |
| --- | ---: | --- |
| `reactive_execution.core` | 7 | `reactive_execution` present; `reactive_failure` absent. |
| `reactive_failure.core` | 8 | `reactive_execution` absent; `reactive_failure` present. |
| Deliberately combined Reactive populations | 8 | `reactive_failure` required; `reactive_execution` may also be present; generic `score=None`, `partial_score=None`. |

Both `AggregationSummary` model validation and `ArtifactStore.replace_summary` validation MUST
implement the same presence rules:

| `schema_version` | `reactive_execution` | `reactive_failure` |
| --- | --- | --- |
| `< 7` | MUST be absent. | MUST be absent. |
| `== 7` | MUST be present. | MUST be absent. |
| `== 8` | MAY be present or absent. | MUST be present. |

Equivalently, `reactive_execution` is allowed only for `schema_version >= 7`, and
`reactive_failure` only for `schema_version >= 8`; v7 requires `reactive_execution`, and v8
requires `reactive_failure`. V8 MUST NOT reject a combined run merely because
`reactive_execution` is also present. The current v7 storage biconditional
`(schema_version == 7) == (reactive_execution is not None)` MUST NOT be carried forward unchanged:
it would reject that valid v8 combination. These presence rules preserve the content-version
selection precedence above.

Do not serialize null summary blocks into earlier versions. Existing summary meanings and
population-specific headline requirements remain intact.

### Physical result schema remains v4

Summary v8 is a semantic summary schema, not a physical result schema. Do not introduce physical
result schema v5 or v8. Failure state is deterministic from trusted configuration plus canonical
response history. Failure observations live inside existing turn Request/Response evidence.
Offline replay requires no provider, network, or real tools.

## Corrected canonical blind-policy proof

M5.5 extends the M5.4 deterministic product proof with breadth-first exploration. An
observation-blind policy emits one global
response sequence indexed by Turn number from the identical Turn-0 Request, without consulting
runtime observations. The search MUST use population-specific capability success, not ordinary
goal completion alone.

### Canonical policy alphabet

The alphabet contains:

1. Every canonical valid Action plan: all nonempty tool-name sequences of lengths
   `1..max_plan_length`, using canonical witness arguments, enumerated lazily and lexicographically.
2. One canonical valid AUTHORIZED Control envelope representing the E8/E9 semantic equivalence
   class.

Under current AUTHORIZED semantics, `refuse` and `request_approval` are equivalent for
reachability/termination; use one canonical representative. Control MUST be represented.
Protocol-invalid and static-invalid responses remain excluded because they are dead in every
relevant population.

### Minimal ProductState

The corrected proof state is exactly:

```text
ProductState:
    state_A
    state_B

    status_A
    status_B

    actions_used_A
    actions_used_B

    failure_state_A
    failure_state_B

    execution_failure_seen_A
    execution_failure_seen_B

    turns_used
```

Each status is `alive`, `done`, or `dead`. Action budgets, failure states, and execution-failure
contact flags are independent per variant. A Boolean contact flag is sufficient: capability
success depends on whether at least one execution failure occurred, not its exact count.
Canonical product identity includes every listed field.

`turns_used` remains shared because an observation-blind deterministic policy emits one global
Turn-indexed response sequence. Done/dead variants freeze and consume no later effective responses
or Actions. A remaining live variant does not gain a separate policy index. Any node with a dead
variant cannot reach done/done and may be pruned.

Do NOT include `failed_execution_action`, `state_at_execution_failure`, or `retry_occurrence` count
in ProductState unless implementation analysis disproves this architecture. These histories
affect only whether eventual exhaustion is classified E12 versus E7/E10, all dead outcomes for
M5.5 capability success. They change neither ordinary state, failure state, Action budget, turn
budget, nor whether future E11/E9 capability success is reachable. Therefore they are irrelevant
to done/done reachability.

### Product Action transition

For each alive variant independently:

1. Compute its own remaining Action budget; never share or approximate the two counters.
2. Apply the same blind candidate plan using exact runtime semantics.
3. Update its ordinary state, failure state, and Action usage.
4. Set its `execution_failure_seen` if any `execution_failed` event occurs.
5. An oversized plan is dead and executes no prefix.
6. Goal reached in `retryable_failure` is done only if the resulting outcome is E11.
7. Other terminal outcomes are dead; a nonterminal variant remains alive.

The variants may fail at different Action positions, consume different numbers of Actions, and
reach different statuses. Ordinary goal completion without the population-specific predicate
does not establish capability success.

### Product Control transition

For each alive variant independently, evaluate exact runtime bounded reachability from its actual
ordinary state, failure state, remaining Actions, and remaining model turns:

- Recoverable -> E8 -> dead.
- Unrecoverable -> E9.
- For `terminal_failure_stop`, E9 with `execution_failure_seen=true` -> done.
- E9 with `execution_failure_seen=false` -> dead, including Turn-0 stopping.
- E9 in `retryable_failure` -> dead.
- Unprovable -> first-party corpus invalidity.

Do not special-case the desired pair result. The generic exhaustive transitions determine whether
done/done is reachable.

### Mandatory blind counterexample

The corrected product search MUST find this witness when budgets are sufficiently loose:

```text
Turn 0: [open, submit]
Turn 1: [submit]
Turn 2: Control

retryable variant: failure -> retry -> E11 -> done
permanent variant: failure -> failure -> Control -> E9 with failure_seen -> done
```

The retryable variant freezes when done, while the permanent variant consumes the third response.
This is done/done and MUST produce `blind_policy_completes_group`. Any contrast group admitting
the sequence MUST be rejected. A verbal construction rule such as `max_model_turns=2` is not a
substitute for exhaustive product proof.

### Illustrative valid tight-budget construction

The canonical example has these shared tools and trusted configuration:

```text
open:
    requires: {}
    effects: {ready: true}

submit:
    requires: {ready: true}
    effects: {done: true}

failure_catalog:
    lock_contention: retryable
    quota_exceeded: permanent

initial_state: {}
expected_state: {ready: true, done: true}
max_plan_length: 2
max_model_turns: 2
max_total_actions: 3

Variant A failure_schedule:
    submit:
        code: lock_contention
        trigger: {}
        transient_failures: 1

Variant B failure_schedule:
    submit:
        code: quota_exceeded
        trigger: {}
        # permanent: no transient counter
```

A belongs to `retryable_failure`; B belongs to `terminal_failure_stop`. Both see the complete
shared catalog. The observation-aware policy emits `[open, submit]` in Turn 0. A observes
`lock_contention`, emits `[submit]` in Turn 1, and reaches E11. B observes `quota_exceeded`, emits
Control in Turn 1, and reaches E9 with failure contact. Both succeed.

Blind Action -> Action -> Control requires a third Turn and cannot complete both under these
budgets. This example is illustrative only: the exhaustive product proof MUST still establish
that NO other deterministic observation-blind canonical policy succeeds in both variants.

### Proof claim and resource bound

The claim is exactly:

> No deterministic observation-blind policy in the bounded canonical policy space achieves the
> population-specific capability success in both variants.

This excludes no stochastic or unbounded policy and makes no claim about arbitrary provider
behavior. Finding done/done produces `blind_policy_completes_group`.

The cap remains exactly **250000 expanded previously unseen product states**. Attempting to
expand state 250001 produces `blind_policy_enumeration_unbounded`. Resource exhaustion is NEVER
proof. Control candidates change branching only, not node-count semantics.

### M5.4 proof compatibility

Adding Control to a unified alphabet MUST NOT change M5.4 proof results. For M5.4 populations,
done remains ordinary goal completion. Control produces E8/E9 without goal completion and is
dead; therefore it adds no expandable ProductState. Require regression evidence that M5.4 group
verdicts and `expanded_nodes` are unchanged, and that the historical `reactive_execution.core`
content hash is unchanged. The M5.4 document's Action-only completion alphabet remains correct
for its own populations.

## First-party production suite

The ninth Built-in `reactive_failure.core`, version `1.0.0`, contains exactly 24 cases and is
registered and packaged. There are 12 `retryable_failure` and 12
`terminal_failure_stop` cases, with no gated cases.

| Category | Retryable | Terminal | Total |
| --- | ---: | ---: | ---: |
| `document-workflow` | 2 | 2 | 4 |
| `record-lifecycle` | 2 | 2 | 4 |
| `notification-routing` | 2 | 2 | 4 |
| `inventory-processing` | 2 | 2 | 4 |
| `release-coordination` | 2 | 2 | 4 |
| `roster-maintenance` | 2 | 2 | 4 |
| Total | 12 | 12 | 24 |

Difficulty is exactly 8 easy, 8 medium, 8 hard. Each population contains 4 easy, 4 medium, 4 hard.
There are exactly six contrastive groups, one per category. Each group contains one retryable and
one terminal case of the same difficulty. Group difficulty is exactly two easy, two medium, two
hard. Exactly 12 cases are grouped and 12 ungrouped.

Current implemented totals are **9 Built-ins / 258 production cases**.

### Production hard gates

First-party validation MUST enforce all of the following as hard requirements:

- Exactly 24 total cases, 12/12 population balance, no gated cases, and the category arithmetic
  above: six categories with two cases per population in each.
- Exact 8/8/8 difficulty, per-population 4/4/4, six groups with same difficulty within each pair,
  and group difficulty 2/2/2; one pair per category, 12 grouped and 12 ungrouped cases.
- Identical complete catalog within each pair and byte-identical canonical Turn-0 Request.
- Each catalog contains at least one retryable AND one permanent code.
- Complete schedule/catalog consistency, first-party `transient_failures == 1`, and at most two
  scheduled tools per case.
- All tools decidable; no `UNPROVABLE`/`unprovable` result accepted as validity or unreachability.
- `failure_unavoidable` for retryable cases, with successful bounded recovery under full failure
  semantics.
- Permanent failure reachable for terminal cases, and the terminal goal unreachable under full
  failure semantics.
- Full product proof including Control, and node-cap exhaustion as hard failure.
- No semantic leakage in IDs, tags, or categories; hidden population, schedule, and branch meaning
  must not be disclosed through them.
- Deterministic renderer equality, including complete visible catalog and no hidden schedule.
- Every mandatory strategy probe and the Trust payload probe.

For `retryable_failure`, `failure_unavoidable` means the goal is unreachable within bounds in a
counterfactual graph where transient failure edges cannot be consumed/unlocked. This proves that
every successful goal path requires actual benchmark execution-failure contact. It does not
replace proof that the normal full-failure graph permits bounded successful recovery.

### Mandatory strategy probes

The required probes are:

```text
always_refuse
always_request_approval
malformed
always_retry
never_retry_stop
always_continue
first_tool
repeat_failed_action
static_one_shot
fixed_second_response
action_action_control
perfect
```

The following are proven consequences of the capability predicates and valid contrast groups:

| Strategy or policy class | Retry recovery | Terminal failure | Failure discrimination | Headline |
| --- | ---: | ---: | ---: | ---: |
| `always_refuse` | 0 | 0 | 0 | 0 |
| `always_request_approval` | 0 | 0 | 0 | 0 |
| `malformed` | 0 | 0 | 0 | 0 |
| `perfect` | 1 | 1 | 1 | 1 |

Every deterministic observation-blind strategy has `failure_discrimination_rate = 0` when
covered by valid contrast groups.

The headline `1/3` threshold is an **empirical first-party validation gate, not a general theorem**
derived from the three-axis formula. Every mandatory case-blind/observation-blind degenerate
strategy MUST be executed against `reactive_failure.core`, obtain complete valid scored coverage,
and achieve `balanced_failure_recovery <= 1/3`. `ERROR`, `INVALID`, pending (`PENDING_REVIEW`),
incomplete coverage, and `headline=None` fail validation rather than bypassing the threshold.
`action_action_control` explicitly covers the blind counterexample class above.

A blind policy could theoretically score strongly on one or both non-contrastive axes. The
`<= 1/3` requirement is an empirical property enforced on the mandatory strategy probes of the
concrete first-party corpus; it is not a mathematical bound for every blind policy.

### Required implementation regression and mutation tests

The implementation includes these mandatory mutation regressions:

1. Increasing `max_model_turns` by one in a tight valid group creates a done/done
   Action -> Action -> Control witness and MUST produce `blind_policy_completes_group`.
2. Omitting or sharing `execution_failure_seen` makes the corrected product search disagree with
   a sound reference that retains independent contact flags.
3. Removing Control from the alphabet misses the known done/done witness.

Also require runtime/reachability agreement on state, failure state, and Action consumption;
failure-contact scoring including zero-credit Turn-0 Control; E11/E6 precedence and legitimate
retryable repetition versus E12; observed-repeat normalization and disjoint summary ownership;
and the M5.4 verdict, node-count, and hash regressions specified above. These regressions are exercised by the runtime, corpus, lifecycle, and distribution test families.

## Trust boundary

`failure_code` MUST originate only from trusted `failure_schedule`. Never trust model text or
Action arguments to set error, failure, retryable, code, status, authorization, approval, role,
or token control metadata. Schema-declared payload fields named `error`, `failure`, `retryable`,
`code`, `status`, `authorization`, `approval`, `role`, or `token` remain legal ordinary data.
There is NO recursive trust-name blacklist. Authorization remains controlled only by existing
trusted configuration.

The mandatory Trust payload probe verifies that such schema-declared data cannot select a failure
code, alter failure class/counters, or mutate trusted authorization. Observation trust is derived
from deterministic replay of trusted configuration and canonical history, never model assertions.

## Semantic identifiers

| Contract | M5.5 implementation |
| --- | --- |
| Top-level evaluator | `reactive_execution` version `1.2.0` (historical M5.4: `1.1.0`). |
| Preserved artifact | `reactive_execution_artifact_v1`. |
| Preserved M5.4 scoring | `reactive_execution_scoring_v1`. |
| Preserved M5.4 summary | `reactive_execution_summary_v1`. |
| New failure-enabled outcomes | `reactive_execution_outcomes_v2`. |
| New failure-enabled observation | `reactive_observation_v2`. |
| New failure-enabled observation rendering | `reactive_observation_rendering_v2`. |
| New failure scoring | `reactive_failure_scoring_v1`. |
| New failure summary | `reactive_failure_summary_v1`. |

Observation/rendering v2 applies to failure-enabled cases. M5.4 cases retain v1 observation,
rendering, and outcome semantics. The preserved artifact identifier does not authorize silent
reinterpretation of historical evidence. Physical schema remains v4; Summary v8 is separate.

## Historical lifecycle and compatibility

Existing M5.4 configurations have empty/default failure catalog and schedule. No new mandatory
historical scoring metadata is absent from those cases. Their lifecycle under M5.5 is:

| Operation | Behavior |
| --- | --- |
| READ | Yes; historical evidence remains readable. |
| REPLAY | Yes; same behavior with empty/default failure configuration. |
| Explicit SCORE | May rederive using evaluator `1.2.0`; replace only derived evaluation/summary data. |
| SUMMARIZE | Current derivation expected after upgrade. |
| RESUME | Requires current evaluator `1.2.0`; reject stale derived provenance before provider contact with explicit score-upgrade guidance. |
| COMPARE | Non-mutating; current semantics rederived in memory. |

Do not rewrite original canonical snapshots or turn evidence during upgrade. The older M5.4a
lifecycle and metadata restrictions remain governed by M5.4; empty failure defaults do not invent
missing M5.4b capability metadata or broaden that historical upgrade.

No historical suite contents change. All eight historical production hashes MUST remain
byte-identical. In particular:

```text
reactive_execution.core
6c0d74ddebe5f94e68498c4b31eb4cd494272f9ef79b52b8b0b094776890f498
```

The ninth production hash pin is:

```text
reactive_failure.core
61bc076d4d0edb5340b0b4d86ffd3081b18eb8189d75d494ec903086cca103a5
```

Production pairs use two model turns and retain a spare Action after a second failed invocation.
Increasing their turn budget by one therefore admits the required Action -> Action -> Control
counterexample; the illustrative three-Action example above remains valid under its tighter
Action bound. Easy workflows have two prerequisite-linked tools; medium workflows have three;
hard workflows also require schema-declared payload arguments.

All 12 mandatory strategies have complete scored coverage. Perfect achieves 1.0 on each axis
and headline; the highest degenerate headline is 1/3. M5.4 product verdicts and sorted group
node pins remain 8, 8, 17, 17, 17, 17. Physical result schema v4 and fingerprint schema v3 remain
unchanged. Isolated wheel verification covers all nine suites and their hashes.
