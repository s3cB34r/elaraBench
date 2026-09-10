# M5.6 — Partially Observable Reactive Execution

**DESIGNED / RATIFIED — NOT YET IMPLEMENTED.**

This is the authoritative, self-contained M5.6 architecture specification. It extends
[Reactive Execution](reactive-execution.md) and
[Reactive Execution Failure Recovery](reactive-failure-recovery.md); it does not replace their
existing semantics. M5.2–M5.5 are implemented. The current repository has `reactive_execution`
evaluator `1.2.0`, Summary v8, nine Built-ins and 258 production cases. Everything identified
below as M5.6 is planned: evaluator `1.3.0`, Summary v9, and a tenth suite bringing the future
catalog to 282 cases. Physical result schema v4 and fingerprint schema v3 remain unchanged.

## Capability and boundary

M5.6 measures whether a worker acts correctly under incomplete observation: whether it recognizes
when visible information does not determine the correct continuation and obtains additional
information, while avoiding unnecessary information gathering when visible information is already
sufficient. It extends the existing `reactive_execution` evaluator family; there is no new family.

M5.6 introduces no real tools, persistent external environments, exogenous environment changes,
dynamic authorization, human approval workflow, provider-native Tool APIs, routing/fallback,
multi-agent execution, autonomous agents, random failures, new behavioral runtime events, or new
behavioral outcomes. E1–E12 meanings remain unchanged. Provider, network, and process failures
remain `ERROR`, separate from benchmark behavior.

## Trusted observability configuration

The planned optional configuration is:

```python
observability: ObservabilitySpec | None = None

class ObservabilitySpec:
    observable_keys: tuple[Identifier, ...]
    reveals: dict[Identifier, tuple[Identifier, ...]] = {}
```

`observable_keys` names initially visible state keys. `reveals` maps inspection-tool identifiers
to the state keys that successful application makes visible. `observability` is trusted,
identity-bearing, runtime/replay relevant, and scoring relevant through capability and corpus
semantics. Its default is `None`. Both `observable_keys` and `reveals` are trusted, model-visible,
and identity-bearing. Hidden current state **values** are trusted but not model-visible until
legitimately revealed. A model-produced value does not become trusted because of its field name.
There is no recursive Trust-name blacklist.

Let `project(state, keys)` retain only the named entries of the true state, rendered canonically.
Projection controls disclosure; it does not replace the true state used for transitions,
reachability, or exact whole-state target equality.

## Turn-0 visibility and task identity

When observability is configured, the deterministic TASK renderer exposes an initial projected
observation **before the first worker Action**, using a canonical block equivalent to:

```text
Observable state keys: <canonical sorted representation>

Initial observed state:
project(initial_state, observable_keys)

Inspection tools:
<canonical sorted reveals mapping>
```

The task explains that additional state exists whose current values are not exposed, and that
successfully applied inspection tools can make configured keys visible in later observations.
Hidden key **names** may legitimately occur in model-visible tool `requires`, tool `effects`, or
`reveals` declarations. The protected information is the current value unless observable or
revealed; the task must not claim that hidden names can never appear anywhere.

The worker receives projected initial values, `observable_keys`, the `reveals` mapping, normal
visible tool schemas, `requires`/`effects`, objective, and budgets. It does not receive hidden
current values, full `initial_state`, capability, variant/group identity, `expected_state` where
the existing Reactive task contract withholds it, or reachability classification.

For historical cases, `observability == None` renders **no new block** and preserves byte-identical
historical Turn-0 requests. There is **no new task-rendering semantic identifier**: the repository
has no task-render semantic-ID field. Task-text identity remains carried by canonical suite
content, content hash, and corpus renderer equality checks.

## Observation v3 and reveal runtime state

M5.6 plans `reactive_observation_v3` and `reactive_observation_rendering_v3`. The observation field
structure is unchanged. Only `resulting_state` is projected:

```text
visible_keys = set(observable_keys) UNION revealed_keys
resulting_state = project(true_current_state, visible_keys)
```

Observations must not expose hidden unrevealed current values, capability, group/variant identity,
expected state, or reachability classification. With `observability == None`, projection is the
identity and historical observation bytes remain unchanged.

The planned ephemeral trusted `ReactiveRuntime` component is:

```python
revealed_keys: frozenset[Identifier]
```

It begins empty, is monotonic and not model-controlled. It is neither ordinary environment state
nor goal state. It is not physically persisted and must be reconstructed exactly through replay.
It is derived **only** from successfully `applied` inspection-tool invocations. An inspection
Action producing `precondition_failed` or `execution_failed` must not expose keys. Only `applied`
extends `revealed_keys` with the configured reveal keys.

Reveal acquisition affects later observations. It does not permit adapting subsequent Actions
inside the same already-submitted Action plan.

### Inspection-tool semantic inertness

Every first-party tool `t` appearing in `reveals` must satisfy the machine-checkable gate:

```text
effects(t) ⊆ requires(t), as identical key/value pairs
```

When the Action is applicable, every effect already holds. Applying it consumes one Action and
leaves ordinary true state byte-equivalent. It cannot unlock or block another tool, change
expected-state equality, or alter reachability distance. Its only state acquisition is expansion
of `revealed_keys`. `SyntheticToolDefinition` itself remains unchanged.

## Populations

M5.6 adds exactly `information_required` and `information_sufficient`. The older names
`inspection_required` and `inspection_unnecessary` are not population identifiers: the capability
concerns epistemic information status, not mandatory use of one inspection mechanism.

For `information_required`, the initial visible projection does not determine the correct
continuation. Information must be acquired through legitimate model-visible interaction. Valid
routes include an inspection/Reveal Action, an informative successful Action, or an informative
`precondition_failed` observation. Scoring must not require a particular inspection mechanism.

For `information_sufficient`, the initial projection and known deterministic rules already
determine a valid shortest completion path. Hidden initial values are behaviorally irrelevant.
Additional information gathering is unnecessary and budget-fatal in the first-party corpus.
Two **independent** gates establish epistemic sufficiency and exact-budget restraint; neither
gate is a substitute for the other.

### Epistemic sufficiency gate

```text
hidden_keys(case) = keys(initial_state) - set(observable_keys)
```

For every `information_sufficient` case and every `k` in `hidden_keys`, require:

- **S1:** `k` exists in `expected_state` and `initial_state[k] == expected_state[k]`.
- **S2:** no tool `requires` references `k`.
- **S3:** no tool `effects` references `k`.

S2 removes hidden-value influence on Action applicability. S3 removes hidden-value state
transitions. S1 makes the immutable hidden value compatible with exact whole-state target
equality. These hidden values are therefore inert decoys, not information needed for planning.

The validator must reject the canonical counterexample: hidden `mode=A/B`, different one-step
tools requiring different modes, and exact Action budget 1. This violates S2 even if each world's
shortest path exactly consumes its budget.

### Exact-budget restraint gate

For each `information_sufficient` case:

```text
analyze_bounded_reachability(
    initial_state, expected_state, depth_bound=max_total_actions
) == RECOVERABLE, with len(path) == max_total_actions

analyze_bounded_reachability(
    initial_state, expected_state, depth_bound=max_total_actions - 1
) == UNRECOVERABLE

max_plan_length * max_model_turns >= max_total_actions
```

All tools must be decidable. `UNPROVABLE` never establishes validity or unreachability. This gate
proves that exact shortest Action distance equals the full Action budget. Together with
state-preserving inspection tools, it makes every wasted Action fatal, including unnecessary
Reveal, intentional precondition probes, and no-op detours. It does not itself prove epistemic
sufficiency.

## First-party failure isolation

Every case in planned `reactive_observability.core` must satisfy:

```text
failure_schedule == {}
failure_catalog == {}
```

This is a hard validity gate. The production suite uses no benchmark-generated `execution_failed`
events. Generic evaluator semantics may still score M5.6 capabilities in custom suites containing
execution failures. E11 therefore remains a generically valid completion outcome, while it is
unreachable by construction in first-party `reactive_observability.core`.

## Contrast pairs and identity

Exactly six contrast groups contain two hidden-world variants each. Every member is
`information_required`; a required case must never be paired with a sufficient case.

Pair members must have byte-identical canonical Turn-0 `GenerationRequest`s; identical initial
projected values, `observable_keys`, `reveals`, tools and their `requires`/`effects`,
`failure_catalog`, budgets, `expected_state`, generation parameters, and hidden-key sets.
Variants differ only in **values** of allowed hidden initial keys. After legitimate information
acquisition, visible observations must permit different correct continuations.

Both explicit identity gates are required:

```text
P1: canonical_json_bytes(canonical_request(A))
    == canonical_json_bytes(canonical_request(B))

P2: project(initial_state_A, observable_keys)
    == project(initial_state_B, observable_keys)

observable_keys_A == observable_keys_B
reveals_A == reveals_B
hidden_key_set_A == hidden_key_set_B
```

Although P1 can imply part of P2 through rendering, both remain for precise diagnostics.

### Constructive adaptive witness

Every required pair must have one deterministic policy implementation, used unchanged against
both variants, that succeeds in both. Its conceptual interface is:

```text
ObservabilityWitness(current_generation_request, global_turn_index) -> response
```

It receives only the same model-visible canonical request a worker receives and the turn index.
It must not receive full `ReactiveRuntime`, full true state, hidden initial values, `revealed_keys`
directly, capability, group ID, variant ID, expected hidden branch, `expected_state`, or
`failure_schedule`.

A signature alone cannot prevent closure capture of hidden state. Across both executions, record
`canonical_request_bytes -> response_bytes`. Identical request bytes receiving different response
bytes invalidate the witness, using the established diagnostic pattern equivalent to
“identical Request received conflicting strategy Responses”. Different responses become permitted
once requests legitimately diverge through visible observations. A required mutation test must
reject a witness that captures hidden `initial_state` and branches at Turn 0.

### Unchanged blind-policy proof

Reuse `analyze_blind_policy_group` **unchanged**, with no alphabet filter and no `ProductState`
modification. Use the full existing canonical policy alphabet: all canonical valid Action plans
(including Reveal tools), canonical Witness arguments, and one canonical `AUTHORIZED` Control
representative.

The proof claim is exactly: for every valid `information_required` contrast group, no deterministic
observation-blind policy in the bounded canonical policy space reaches population-specific
capability success in both hidden variants. It makes no claim about observation-adaptive policies,
no-Reveal adaptive policies, stochastic policies, unbounded policies, or arbitrary provider behavior.

The cap remains **250000 previously unseen ProductStates selected for expansion**. Attempt 250001
is a hard validation failure, never proof. The unchanged minimal M5.5 `ProductState` is:

```text
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

Do not add `revealed_keys`. Blind policies do not branch on observations; Reveal acquisition does
not affect true environment state, failure state, budgets except through existing `actions_used`,
or turns. Initial projected observations are byte-identical across the pair. Visibility state is
therefore irrelevant to blind-policy reachability.

The analyzer, ProductState, canonical alphabet, transition semantics, and reachability remain
unchanged. Preserve M5.4 expanded-node pins **8, 8, 17, 17, 17, 17** and all M5.5 proof semantics.

## Sample scoring and aggregation

For both new populations, sample capability success means target completion via **E5, E6, or
E11**. E9 is not success. No specific Reveal invocation is required. Exact-budget and no-failure
gates structurally exclude E6/E11 successful routes in first-party sufficient cases, while generic
scoring remains broader. Existing M5.4/M5.5 predicates remain unchanged.

Define:

- `information_acquisition_rate`: repeat-normalized capability success mass over required cases.
- `information_restraint_rate`: repeat-normalized capability success mass over sufficient cases.
- `observability_discrimination_rate`: macro-average over the six required groups of
  `min(capability_mass_variant_A, capability_mass_variant_B)`.

```text
balanced_observability = (
    information_acquisition_rate
    + information_restraint_rate
    + observability_discrimination_rate
) / 3
```

The three axes have equal macro weights. Do not combine this headline with
`balanced_reactive_execution` or `balanced_failure_recovery`. A blind lucky guess may succeed on
one case; that success is allowed. The pair minimum stays zero when the same blind policy cannot
win the other variant, preventing lucky single-case completion from establishing adaptation.

The per-case behavioral denominator is **actual observed `SCORED` repeats**, never
`expected_repeats`. `observed_case_count` counts configured eligible cases with at least one
scored behavioral result. Missing, `ERROR`, `INVALID`, and `PENDING_REVIEW` results affect coverage
only and do not become behavioral mass. Preserve the order:

```text
sample -> case mass -> pair minimum -> population macro -> headline
```

For a pure M5.6 Reactive population configuration, generic score may expose
`balanced_observability` under the established complete/partial coverage convention. Any deliberate
mixture of M5.6 with M5.5 or M5.4 Reactive populations sets both `score = None` and
`partial_score = None`. Suppression depends on **configured** composition, never accidental observed
coverage. Existing M5.4/M5.5 generic behavior remains unchanged.

### Mandatory strategy probes

All twelve probes are mandatory:

| Probe | Required interpretation |
| --- | --- |
| `always_refuse` | Proven acquisition/restraint/discrimination `0 / 0 / 0`, headline `0` |
| `always_request_approval` | Proven `0 / 0 / 0`, headline `0` |
| `malformed` | Proven `0 / 0 / 0`, headline `0` |
| `always_reveal` | Axis values, including restraint behavior, are empirical |
| `never_reveal` | Behavior and axis values are empirical; no required inspection mechanism |
| `always_reveal_then_fixed` | Proven discrimination `0` as a deterministic blind policy; other axes empirical |
| `fixed_second_response` | Proven discrimination `0` as a deterministic blind policy; other axes empirical |
| `static_one_shot` | Proven discrimination `0` as a deterministic blind policy; other axes empirical |
| `action_action_control` | Proven discrimination `0` as a deterministic blind policy; other axes empirical |
| `first_tool` | Axis values are empirical |
| `repeat_last_tool` | Axis values are empirical |
| `perfect` | Must empirically achieve `1 / 1 / 1`, headline `1.0` |

Every deterministic observation-blind policy represented by valid contrast groups has
`observability_discrimination_rate == 0`. The `<= 1/3` headline limit for designated blind/degenerate
mandatory probes is an **empirical first-party validation gate**, not a theorem. Remaining axis
values must be measured. Blind probe implementations must not read hidden trusted inputs.
Incomplete coverage, `ERROR`, `INVALID`, `PENDING_REVIEW`, or headline `None` fails validation.

## Summary v9 and semantic identity

Planned `ReactiveObservabilitySummary` owns only `information_required` and
`information_sufficient`. It must not contaminate `ReactiveExecutionSummary` or
`ReactiveFailureSummary`. The exact presence contract is:

| `schema_version` | `reactive_execution` | `reactive_failure` | `reactive_observability` |
| --- | --- | --- | --- |
| `< 7` | absent | absent | absent |
| `7` | REQUIRED | absent | absent |
| `8` | OPTIONAL | REQUIRED | absent |
| `9` | OPTIONAL | OPTIONAL | REQUIRED |

V9 deliberately permits observability only, observability + execution, observability + failure,
and observability + execution + failure, where configured population composition legitimately
produces those blocks. Absent means absent, not an added null field. `AggregationSummary` model
validation and `RunArtifactStore.replace_summary` must use **one identical presence contract**.

Content precedence is:

```text
9 if observability summary exists
else 8 if failure summary exists
else 7 if execution summary exists
else 6 if Action Recovery summary exists
else 5 if Action Compliance summary exists
else 4
```

Planned identifiers are `reactive_execution` evaluator `1.3.0`, `reactive_observation_v3`,
`reactive_observation_rendering_v3`, `reactive_observability_scoring_v1`, and
`reactive_observability_summary_v1`. Preserve existing artifact semantic identity unless actual
implementation needs only additive validation support. There is no new behavioral outcome semantic
version, no task-render semantic identifier, no physical schema bump, and no fingerprint bump.

`revealed_keys` must not require physical persistence. Prefer deriving diagnostics such as
`reveal_invocation_rate` from canonical responses/actions plus trusted observability configuration.
Do not persist redundant reveal history. If implementation demonstrates a genuine need for an
additive semantic artifact field, it must remain reconstructible from canonical evidence and must
not force physical schema v5. This design prescribes no unnecessary physical evidence fields.

## Historical lifecycle

`observability` defaults to `None` **in memory**. Historical raw configuration dictionaries remain
authoritative: never replace them with `model_dump()`. Historical configuration/content hashes,
snapshot bytes, requests, responses, attempts, observations, and production hashes are preserved.

| Operation | Required M5.6 behavior |
| --- | --- |
| READ | M5.4/M5.5 evidence remains readable. |
| REPLAY | `observability=None` reproduces historical task and observation bytes exactly; configured reveal state is reconstructed from applied Actions. |
| SCORE | Eligible existing Reactive evidence may explicitly upgrade to evaluator `1.3.0` only after whole-run eligibility preflight, before every write/event. Do not broaden pre-M5.4b eligibility where capability/objective/group metadata was absent. |
| SUMMARIZE | Require current derivation where applicable. |
| RESUME | Reject stale Reactive derivation before provider contact and provide score-upgrade guidance; reconstruct reveal state exactly through replay. |
| COMPARE | Use current semantics in memory, without mutation. |

## First-party production design

Planned `reactive_observability.core` version `1.0.0` contains exactly **24 cases**:

- 12 `information_required`, all in six two-variant groups.
- 12 `information_sufficient`, all ungrouped.
- Six established categories: document-workflow, record-lifecycle, notification-routing,
  inventory-processing, release-coordination, and roster-maintenance; each has two cases per population.
- Difficulty totals 8 easy / 8 medium / 8 hard, with 4 / 4 / 4 within each population.
- One required contrast group per category; group difficulty is 2 easy / 2 medium / 2 hard.
- No authorization-gated cases; all are `AUTHORIZED`. Failure configuration is empty in every case.

### Fail-closed corpus validator contract

The future first-party validator must enforce all of these gates:

1. Exactly 24 cases and 12/12 populations.
2. No authorization-gated cases.
3. Six categories with four cases each and two per population.
4. Difficulty 8/8/8 and each population 4/4/4.
5. Exactly six groups, one per category, with group difficulty 2/2/2; all sufficient cases ungrouped.
6. Both members of every group are `information_required`.
7. Byte-identical canonical Turn-0 Request per pair (P1).
8. Identical initial projection, `observable_keys`, `reveals`, and hidden-key set per pair (P2 and explicit equality gates).
9. Pair variants differ only in allowed hidden initial values, preserving all other specified pair identity.
10. Epistemic sufficiency S1/S2/S3 for every sufficient case, including rejection of the hidden-mode counterexample.
11. Exact-budget restraint for every sufficient case, including the plan/turn capacity inequality.
12. Inspection-tool inertness: effects are a subset of requires as identical key/value pairs.
13. At least one hidden initial key per case.
14. Reveal keys are outside `observable_keys`.
15. At most two inspection tools per case.
16. `failure_schedule == {}` and `failure_catalog == {}` in every case.
17. All tools are decidable.
18. `UNPROVABLE` is never accepted as validity or unreachability.
19. Goal reachability within budget in both required variants.
20. Blind-policy proof has no done/done success and does not hit the node cap.
21. One information-safe adaptive witness succeeds in both variants.
22. Witness input determinism is enforced across the pair, including the hidden-state closure mutation test.
23. Semantic leakage is forbidden in case IDs, categories, and tags.
24. Authoritative renderer equality includes initial projection, `observable_keys`, and `reveals`.
25. All mandatory strategy probes are complete and valid, satisfy empirical gates, and include perfect `1.0`.
26. A Trust-payload probe is present and passes.

First-party IDs, categories, and tags must not leak solution labels such as `reveal`, `probe`,
`hidden`, `unknown`, `required`, `sufficient`, `inspect`, or `decoy`. Visible tool names and
descriptions may naturally communicate legitimate task function; this rule targets benchmark
labels/metadata, not task comprehensibility.

The Trust-payload probe uses authorized synthetic payload schemas with ordinary fields including
`authorization`, `approval`, `role`, `token`, `status`, `state`, `error`, `failure`, `retryable`,
`permission`, and `policy`. It must prove that these model-controlled values cannot modify
observability configuration, `observable_keys`, `reveals`, `revealed_keys`, true state outside
declared normal tool effects, failure configuration, or authorization. No recursive blacklist
is introduced.

### Built-in, hashes, and wheel plan

The future registration is `reactive_observability.core -> reactive_observability/core-v1`.
All nine existing production hashes must remain byte-identical. Future implementation adds one
tenth pin; no existing production corpus file is rewritten. No new hash is assigned by this
documentation task.

The future isolated wheel regression must verify ten Built-ins, 282 cases, discovery of the new
suite, an importable/executable new validator, and all ten hashes. Test-only Goldens and foundation
fixtures remain excluded. `pyproject.toml` is expected unchanged.

The **current implemented** catalog remains **nine Built-ins / 258 cases**. The **planned catalog
after M5.6 implementation** is **ten Built-ins / 282 cases**. This ratified architecture does not
implement evaluator `1.3.0`, Summary v9, runtime projection, or the new corpus.
