# Architecture specification

ElaraBench is standalone and independent from Elara Core. It imports no Elara Core package,
configuration, service, or runtime state.

```text
Versioned benchmark definitions
        ↓
Runner → Provider adapter → Model/backend
        ↓
Immutable raw run artifacts
        ↓
Deterministic evaluators
        ↓
Derived summaries and comparisons
```

The benchmark defines tasks and scoring policy. A provider translates provider-neutral requests
and returns normalized responses while retaining raw provider evidence. Evaluators operate only
on stored responses. The artifact store preserves evidence and contains no model execution.
Aggregation derives metrics, and the CLI delegates to these library services. Providers never
score, evaluators never generate, and benchmark definitions contain no provider behavior.

## M2 runner lifecycle

M2 execution is synchronous and strictly sequential. A new run validates and snapshots the
suite, preflights the provider/model, discovers non-fatal environment metadata, resolves all
case/repeat requests in stable order, computes hashes and the run fingerprint, then writes the
manifest and complete benchmark snapshot before any generation. For every sample it writes the
request before invoking the provider, persists every attempt immediately, writes one final
canonical response or error, evaluates the stored response, and derives a summary.

The runner depends only on `ModelProvider`. Ollama-specific translation and HTTP behavior live
in `providers/ollama.py`. The provider owns one reusable synchronous HTTPX client and caches
preflight metadata for its lifetime.

## Runtime policy resolution

Thinking is a provider-neutral, typed part of `RunConfiguration` and every resolved
`GenerationRequest`:

- `enabled` requests reasoning/thinking when the provider adapter supports the control.
- `disabled` explicitly requests no reasoning/thinking and is the ElaraBench default.
- `provider_default` sends no explicit control and records that behavior depends on the
  provider/model default.

Resolution is CLI override, then optional suite default, then `disabled`. Ollama derives a typed
control kind from cached `/api/show` metadata: `none`, `boolean`, `levels`, or `unknown`. Raw
provider capabilities and architecture remain in model identity; the interpretation is persisted,
fingerprinted, and compared on resume. The broad `thinking` capability alone remains `unknown`.
Only an exact provider-reported architecture in the adapter's small compatibility table supplies
stronger evidence. There are no user-facing model-name heuristics or task-type inference.

Boolean-capable models map explicit policy to top-level `think`, outside generation `options`. A
non-thinking model satisfies `disabled` without sending `think` and rejects `enabled`. Unknown or
level-valued control rejects both explicit policies because enforcement cannot be proven with the
v0.2.1 policy. `provider_default` is safe in every state because it intentionally omits the field.
Translated requests are preserved as raw evidence. Reasoning-effort levels are outside v0.2.1.

The general request and suite timeout default is 120 seconds. It remains finite, persisted,
fingerprinted, and CLI-overrideable. Slowness alone never triggers a special retry or adaptive
timeout. M2.1 does not warm a model before the first sample. Client wall latency therefore retains
cold-start cost, while Ollama's total, load, prompt-evaluation, and generation durations remain
separate provider metrics.

Configuration, authentication/authorization, and internal adapter failures terminate a run
after available evidence is persisted. Ordinary finalized provider/backend failures affect one
sample, remain visible as evaluator errors, and never become zero scores. Conservative retries
apply only to explicitly retryable connection establishment/write, protocol, and transient HTTP
failures. A generation read timeout is not retried as a presumed transient failure: increasing
the timeout or starting a new physical run is an explicit user decision.

`KeyboardInterrupt` is handled at the orchestration boundary. Completed artifacts remain valid,
an in-flight attempt is recorded as interrupted when possible, no response is fabricated for
incomplete generation, a partial summary is regenerated, and lifecycle becomes `interrupted`.
Ollama client cancellation cannot guarantee that server-side generation has already stopped.

## Resume and offline derivation

Resume reads configuration only from stored canonical artifacts. Before lifecycle mutation it
checks the manifest, self-contained snapshot, fingerprint, adapter/model/source identity,
request plan, and all existing finalized JSON. All already-finalized canonical and derived evidence
that will be reused is validated before any provider or model preflight that could contact the
provider or generate new samples. If existing persisted evidence is corrupt or incompatible,
resume fails before any provider call and before writing any new response or attempt artifact.
Matching completed responses—including finalized provider errors—are never regenerated. Missing
evaluations are derived again. A matching request without a response continues, and a terminal
attempt left just before response finalization can be recovered without another provider call.
Request mismatch, fingerprint incompatibility, or corrupt finalized JSON aborts resume.

`score` dispatches evaluators over canonical stored responses and replaces only derived
evaluations and summary, so replaceable derived evaluation evidence may be regenerated from
authoritative canonical evidence. `summarize` reads and strictly validates stored evaluations and
replaces only the summary; resume likewise strictly validates finalized evidence that it will
reuse. Neither service constructs a provider, reads the original suite directory, or uses the
network.
Evaluation context carries the physical source result-schema version; composite dispatch forwards
it unchanged at every nesting level. Current summaries use artifact schema version 4 by default
and version 7 for scored Reactive Execution, otherwise 6 for Action Recovery, otherwise 5 for
Action Compliance summary semantics. Summaries separately record whether their canonical
source run was physical result schema 2, 3, or 4.
Artifact loading receives the physical manifest schema explicitly. A versionless historical v2
evaluation is interpreted as source schema 2 in memory and is not silently migrated on read;
current v3 evaluation writes carry the field explicitly. Summary reads and writes likewise
validate source provenance against the owning manifest. Only a physical-v2 run may resolve the
omitted provenance of a historical summary-v2 payload; context-free summary parsing does not
invent that ownership fact.

New physical runs use result schema v4 when Reactive Execution is configured, otherwise v3. A
narrow compatibility layer validates historical M2
schema-v2 manifests, snapshots, request hashes, and fingerprint schema v1 without injecting v3
defaults. Those runs can be scored and summarized, but cannot resume because doing so would mix
pre-Thinking and explicit-Thinking execution semantics. Canonical v2 evidence remains immutable.

## Read-only comparison

Comparison extracts manifest, snapshot, request, response, provider/model, and environment evidence
through the same version-aware validator used by offline scoring. Comparison deliberately skips
the validator's eager evaluator check so an unavailable current evaluator becomes typed
comparison evidence rather than corrupting otherwise valid physical input. It then validates the
stored evaluator specifications and evaluates both canonical response sets in memory. No scoring,
summary, event, manifest, or source artifact write occurs.

The comparison domain is independent of result schema v3: comparison schema 1 and policy version
`1.3.0` represent intent, field evidence, separate quality/performance classifications, complete,
matched-case, or verified-intersection populations, and case/category/tag deltas. Snapshot fixture
hashes extend individual case identity without consulting live suite files. Same-suite different-
version runs may select exact verified cases; different suite namespaces and same-version content
conflicts cannot. Optional JSON export is the only write and targets a caller-selected path
outside the source-run protocol.

Validated attempts and terminal responses also flow through the provider-neutral
`comparison_performance` layer. That layer selects paired sample identities, extracts normalized
physical metrics, retains retry active cost, aggregates paired medians, applies metric-specific
tokenizer/provider comparability, and produces deterministic performance hashes. Runner,
providers, storage, benchmark evaluators, and physical result schema v3 are unchanged. Missing
optional timing/usage degrades only the affected metric and never changes quality scoring.

The provider-neutral `refusal_compliance` evaluator keeps expected behavior inside evaluator
configuration and records orthogonal observed behavior, protocol status, and completion status in
ordinary evaluation artifacts. Derived summary schema v4 adds an optional repeat-first,
case-macro behavior summary; physical result schema v3 is unchanged. Comparison reevaluates
canonical responses with the current registry and derives optional refusal analysis over exactly
the already selected M4 population. No provider, Runner, storage-execution, tool, or LLM-judge
subsystem is involved.

## Evaluation outcome boundaries

An evaluator returns `scored` whenever generation succeeded, its configuration and ground truth
are valid, and it can deterministically judge the raw output. Both success and model failure are
scored outcomes: a wrong answer or required-format violation is `scored` with score `0.0` and
`passed=false`. Strict numeric evaluators do not extract numbers from prose, and strict JSON
evaluators do not strip Markdown fences, repair syntax, or search for embedded JSON.

`invalid` means benchmark or evaluator inputs cannot support a trustworthy score—for example an
invalid expected number, regex, or JSON Schema. `error` means generation failed or evaluation hit
an unexpected technical failure. `pending_review` is explicitly unscored. Composite evaluators
combine scored children, including zeroes, and preserve child diagnostics; a true invalid, error,
or pending child propagates as an unscored composite result.

Aggregation counts every `scored` sample in coverage regardless of score. It excludes true
invalid, error, pending, and missing results without converting them to zero. Thus strict output
failures lower the model score instead of artificially reducing coverage and inflating the
partial aggregate.

## Deliberate v1/M2 limits

M2 uses a filesystem store, not a database, and an explicit provider factory, not a plugin
framework. There is no parallelism or locking; `events.jsonl` has one writer. Events are useful
diagnostics, not event-sourced recovery state—canonical sample files remain authoritative.

Human/LLM-assisted judgment remains separate and unimplemented. Future executable coding or
cybersecurity evaluators must use sandbox isolation; M2 never executes fixtures. OpenAI-compatible
and llama.cpp-specific adapters, interactive tool/agent benchmarks, real tool execution,
autonomous agent loops, multi-turn action execution, production tool or sandbox behavior,
distributed execution, Unsloth, Elara Core integration, dashboards, and leaderboards are outside
M2. Static action-plan compliance evaluation remains compatible with the v1 boundary: it evaluates
one stored provider response per sample as a provider-neutral structured plan against externally
defined authorization/gating semantics using deterministic, pure, side-effect-free simulation
only; it executes no tools and introduces no agent loop. The normative M5.2 constraints are defined
in [Static Action Compliance](action-compliance.md).

Implemented M5.3b Action Recovery preserves the same one-request, one-response runner boundary and
persists scored behavioral evidence under evaluator version `1.1.0`. Its
preceding attempt and observation are trusted benchmark-supplied case data, not the result of
executing the current model's own plan. The normative design and its primary capability limitation
are defined in [Action Recovery](action-recovery.md). Bounded proof and the production corpus are
offline validation machinery, not runtime tool execution. A causal provider -> tool -> provider loop,
runtime-generated observations, and additional model turns require M5.4. The implemented M5.4a
bounded causal runtime and physical-v4 evidence foundation is defined in [Reactive Execution](reactive-execution.md).
The same authoritative document specifies implemented M5.4b capability-conditioned scoring with
`reactive_execution` evaluator `1.1.0` (`SCORED`), Summary v7, observation-conditioned proof, and
`reactive_execution.core` v1.0.0: 48 cases across six categories. M5.4 introduced the eighth Built-in and
brought the historical catalog to 234 cases. Summary v7 does not change physical result schema v4.

## M5.5 Reactive Execution Failure Recovery

[M5.5 Reactive Execution Failure Recovery](reactive-failure-recovery.md) is the normative
architecture authority for the **IMPLEMENTED** extension. It provides
`reactive_execution` evaluator `1.2.0`, visible failure catalog and hidden trusted schedule,
failure-contact scoring, and disjoint failure populations in Summary semantic schema v8.
Physical result schema remains v4. It adds no evaluator family, real tools, provider failure
semantics, or new orchestration capability. M5.4 behavior and suite hashes remain unchanged under
evaluator `1.2.0`. The 24-case ninth suite brings the catalog to nine Built-ins and 258 cases.
The existing product BFS now includes Control, independent failure counters, and independent
failure-contact flags; M5.4 node pins remain 8, 8, 17, 17, 17, 17.

## M5.6 Partially Observable Reactive Execution (planned)

[M5.6 Partially Observable Reactive Execution](reactive-observability.md) is the normative authority
and is **DESIGNED / RATIFIED — NOT YET IMPLEMENTED**. Planned evaluator `1.3.0` extends
`reactive_execution` with trusted optional observability, initial projected values before the first
Action, and observation v3 projecting only `resulting_state`. Ephemeral monotonic `revealed_keys`
is reconstructed from successfully applied inspection Actions, without physical persistence.
First-party inspection tools preserve ordinary state. No new behavioral events or outcomes,
provider APIs, real tools, or orchestration capabilities are introduced.

The design adds information-required/sufficient scoring and a disjoint Summary v9 block. It reuses
the existing blind-policy analyzer, full alphabet, and M5.5 ProductState unchanged, without visibility
state; M5.4/M5.5 proof semantics remain intact. Physical result schema v4 and fingerprint schema v3
remain unchanged. The current nine Built-ins / 258 cases become ten / 282 only after future
implementation; current evaluator `1.2.0` and Summary v8 remain implemented.
