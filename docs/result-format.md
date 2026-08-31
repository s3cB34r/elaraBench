# Result format: schema version 3

v0.2.1 produces inspectable filesystem runs:

```text
runs/<run-id>/
├── manifest.json
├── benchmark.json
├── events.jsonl
├── samples/
│   └── <case-id>/
│       └── repeat-000/
│           ├── request.json
│           ├── attempts/
│           │   ├── attempt-000.json
│           │   └── attempt-001.json
│           ├── response.json
│           ├── evaluation.json
│           └── artifacts/
└── summary.json
```

New runs use result schema v3. Schema v3 makes Thinking policy and provider-control provenance
canonical and changes fingerprint semantics. Historical M2 schema-v2 runs are verified with
their original models and hashes: `score` and `summarize` remain available, while `run --resume`
is rejected because v2 predates explicit Thinking identity. Canonical v2 files are never migrated
or rewritten in place. Other result schemas fail validation.

## Canonical and derived artifacts

`benchmark.json`, requests, attempt records, and final responses are canonical. They are written
atomically and cannot be silently overwritten. An interrupted provider call has an interrupted
attempt but no fabricated `response.json`. A finalized provider failure is itself a canonical
response and is not retried during normal resume.

Evaluations and `summary.json` are derived. Evaluation replacement must be explicit; summaries
are atomically replaceable. `summary.json` must never hold the only copy of a response, error,
individual evaluation, benchmark definition, or identity field.

Three version fields have deliberately separate meanings:

- `manifest.json.schema_version` is the physical result schema (`2` historically, `3` now).
- `evaluation.json.source_result_schema_version` identifies the physical canonical evidence that
  was evaluated. Composite children inherit the same value recursively.
- `summary.json.schema_version` is the summary artifact's own shape and is `4` for current
  summaries. `summary.json.source_result_schema_version` identifies its physical source.

Summary artifact shape does not establish physical provenance. Trusted run reads validate the
summary source version against the owning manifest. The missing provenance field in historical
summary schema v2 is resolved as source v2 only inside a physical-v2 run; the same payload in a
physical-v3 run is corrupt and is rejected.

Consequently, rescoring a historical physical v2 run with current evaluators produces:

```text
physical result schema:             2
evaluator version:                  current evaluator version
evaluation source result schema:    2 (outer and every composite child)
regenerated summary schema:         4
summary source result schema:       2
```

The original strict summary schema v2 had no source-provenance field. It remains historical
derived data. Readers accept summary v2 and infer only its physical source provenance; summary v3
also remains readable without fabricated refusal data. Regeneration may replace either derived
shape with summary schema v4; canonical manifests, benchmarks, requests, attempts, and responses
remain unchanged.

Historical schema-v2 `evaluation.json` files also predate the source-provenance field. Their
physical manifest is authoritative: version-aware loading infers source result schema 2 in memory
when the field is absent, without rewriting the evaluation merely to add it. Current schema-v3
evaluation writers always serialize source result schema 3 explicitly. Missing, null, invalid, or
contradictory provenance in a physical schema-v3 evaluation is rejected as an integrity error;
it is never synthesized during read.

All JSON is UTF-8, sorted, indented, and newline-terminated where practical. Atomic-write
temporary files have a reserved name prefix. Resume safely removes only those leftovers and
aborts on corrupt finalized JSON.

## Manifest

The manifest has immutable identity/configuration sections and one guarded mutable `lifecycle`
section. It records result schema version, physical run ID, deterministic run fingerprint,
ElaraBench/Git source identity, suite and snapshot hashes, resolved run configuration, sanitized
provider endpoint and capabilities, model digest/quantization/backend/template metadata,
seed support/application status, requested thinking policy, raw advertised model capabilities,
discovered thinking-control kind, whether an explicit control field is planned,
finite timeout, ordered request hashes, execution environment, timestamps, status, and resume
count. Hardware lives in
`environment`; it is not silently folded into the run fingerprint.

Lifecycle states are `initializing`, `running`, `completed`, `completed_with_errors`,
`interrupted`, and `failed`. Lifecycle updates cannot alter identity/configuration or move time
backwards.

## Benchmark snapshot

`benchmark.json` is complete enough for later scoring without the source suite. It stores its
schema version, complete ordered validated suite/cases/messages/evaluation specifications,
weights, aggregation settings, effective minimum coverage, fixture references, fixture hashes,
base64 fixture bytes, benchmark content hash, and snapshot self-hash. M2 does not execute fixture
contents.

## Attempts, events, and responses

Every provider invocation gets an immutable `attempt-NNN.json` containing sample identity,
retry number, request hash, start/end timestamps, monotonic duration, outcome, and the normalized
response/error plus raw provider evidence. Successful retry never erases earlier failures.

`events.jsonl` uses a fixed typed event vocabulary, UTC timestamp, run and invocation IDs,
zero-based monotonic sequence, optional sample/attempt identity, and small structured data. It
has one writer and no M2 locks. An incomplete final event fragment from a hard kill can be
discarded on resume; valid canonical artifacts determine recovery.

Ollama responses preserve normalized text, finish reason, token usage, client latency, provider
timings, normalized error details, translated request payload, and raw JSON response. Credentials
or authorization values are never accepted into endpoint identity or persisted.

Every schema-v3 canonical request serializes one of `enabled`, `disabled`, or `provider_default`
for thinking. Provider metadata records control kind `none`, `boolean`, `levels`, or `unknown`,
while model identity preserves raw `/api/show` capabilities and architecture. The broad Ollama
`thinking` capability alone is not boolean proof. Ollama's translated
`raw_request_payload` contains top-level `"think": true` or `"think": false` only for explicit
policies on a boolean-controllable model. It omits the field for `provider_default` and for
`disabled` on a non-thinking model. Level-valued or unknown explicit control never reaches
generation. Together these artifacts distinguish requested policy from enforceable control.

Timing does not subtract model load duration from client wall latency. When Ollama supplies the
data, `latency_seconds`, `provider_total_seconds`, `provider_load_seconds`,
`provider_prompt_eval_seconds`, and `provider_eval_seconds` remain distinct. No implicit warmup
request precedes canonical samples.

## Summary and coverage

Current `summary.json` uses summary schema version 4. A current physical v3 run produces
`schema_version: 4` and `source_result_schema_version: 3`; a regenerated historical v2 summary
produces `schema_version: 4` and `source_result_schema_version: 2`. Schema v4 adds optional
`refusal_compliance`; it is null when compatible evaluator evidence is absent.
Current schema-v3/v4 summaries require explicit source provenance. The only inferred provenance is
for a provenance-less schema-v2 summary owned by a physical-v2 run. Historical v2/v3 summaries are
read-only compatibility objects; the current writer accepts only schema v4, and explicit rescore or
summary regeneration is the upgrade path.

Repeats are averaged per case, then positive case weights form a macro average. Category and tag
breakdowns use the same case weighting. Repeat statistics include count, mean, min, max, and
population variance/standard deviation for at least two scores.

Coverage is `scored samples / (cases × repeats)`. A valid score of zero is covered; provider
errors, invalid benchmark/evaluator inputs, technical evaluation errors, pending review, and
missing results are not. The default minimum is 0.95. At or above it, `score` is the headline
aggregate. Below it, `score` is null and `partial_score` retains the available aggregate. Status
counts remain separate; unscored samples never silently become zero.

Evaluation status is semantic rather than a synonym for pass/fail:

- `scored` has a score in `[0, 1]`; both passing answers and deterministically judged model
  failures belong here. Malformed model format is normally `scored` with `0.0`.
- `invalid` has no score because benchmark ground truth or evaluator configuration cannot be
  trusted.
- `error` has no score because generation or evaluation failed technically.
- `pending_review` has no deterministic score.

Derived evaluations retain evaluator version and configuration hash. Rescoring may replace them
when evaluator semantics change, but canonical response text remains untouched and strict
evaluators never repair it. Regenerated evaluations and summaries record the physical run's
source result schema, so a current rescore of v2 evidence is not presented as its historical
original score.

Action-compliance evaluations also retain a strict immutable artifact with independently auditable
proposal, trusted authorization-gate, and simulation evidence. It records artifact, protocol,
gate, simulation, outcome, evaluator, configuration, and source-result semantic provenance.
Invalid-argument evidence uses canonically sorted machine diagnostics containing JSON instance and
schema pointers, validator keyword, and a canonical validator-value hash; validator-library prose
is not persistent semantic evidence.
Denied and approval-required artifacts require simulation to be absent. During M5.2a these
deterministically derived artifacts use `pending_review`, with null score/pass fields, so they do
not enter generic numeric coverage or headline aggregation before M5.2b.
For a validated Action Compliance specification, a successful canonical provider response may
produce `pending_review` evidence or an evaluator `error`; `invalid` is not a valid derived state
and is rejected as corrupt during stored-evidence validation. Provider-error responses require an
artifact-free `error` result.

Offline `score` treats canonical response/attempt evidence and the snapshotted evaluator
specification as authoritative: it rederives and validates a supported Action Compliance artifact,
then replaces stale or corrupt derived evaluation evidence. `summarize` and resume do not repair;
they hard-fail incompatible or corrupt stored Action Compliance provenance through the normal run
integrity path.

Refusal-aware evaluations retain the generic `EvaluationResult` shape. Orthogonal expected and
observed behavior, protocol and completion status, detection source, reason/redirect evidence,
redirect acceptance, policy-probe status, observable policy attribution, and result-schema status
use a strict refusal-specific artifact contract. Policy attribution records its typed source:
configured reason-code membership for structured refusals or the versioned anchored-prose
classification for fallback refusals. Safe-redirect acceptance is likewise validated against the
expected behavior, protocol status, configured enablement and redirect-code allowlist. Summary
regeneration validates required fields,
enum and cross-field semantics, evaluator/configuration provenance, and agreement with the
snapshot case expectation. Corrupt derived evidence fails summary construction rather than being
counted as model behavior; an explicit rescore can regenerate it from the immutable canonical
response. `refusal_compliance_summary_v1` first averages repeats within each case,
then macro-averages eligible cases. Every rate records numerator, denominator, eligible count,
coverage, partial value, and a headline value only at 100% scored coverage for that metric's
eligible population. Missing historical analysis is not a zero rate.
Outcome counts are mutually exclusive, including a distinct accepted-safe-redirection count;
protocol validity remains independently visible. A zero-eligible rate has null coverage and
values, whereas an incompletely covered non-empty population has a positive denominator and a
coverage ratio below one.
Run scoring supplies the persisted run-level repeat count. Standalone `aggregate(samples)` calls
infer that count only from a contiguous global repeat-index domain starting at zero; ambiguous
gaps, duplicate sample identities, and indexes outside an explicit repeat count are aggregation
errors rather than clamped or over-100% behavioral coverage. If standalone aggregation has no
authoritative case expectations and encounters an unscored refusal evaluation, it withholds the
behavioral analysis rather than inferring a complete population from surviving scored evidence.

## Comparison schema 1

Comparison JSON is an independent derived artifact; it does not change result schema v3 or live
inside either source run. Output paths inside source runs are rejected. Schema 1 records
comparison policy `1.3.0`, ordered baseline/candidate run and evidence identities, intent,
benchmark/model/profile evidence, separate quality and performance assessments, current in-memory
evaluator provenance, coverage/population mode, and available full-suite, matched-case partial, or
verified cross-version intersection case/category/tag deltas. Intersection evidence records both
suite case counts, shared/verified/mismatched/one-sided IDs, snapshot-fixture-aware case hashes,
evaluator-unavailable and incomplete cases, expected repeats, selected weight, weighting semantic,
and intersection coverage. `generated_at` is descriptive and excluded from the deterministic
comparison fingerprint.

Policy-`1.0.0` schema-1 comparison artifacts remain valid. Historical category/tag breakdowns
may omit `population_mode`, `baseline_total_case_count`, and `candidate_total_case_count`; readers
treat those absent additive fields as unknown (`null`) and do not infer intersection semantics.
Policy-`1.1.0` writers populate all three fields on every emitted breakdown. A suite version
difference is reported independently and does not emit `verified_intersection_comparison` unless
an intersection population is actually selected.

Policy `1.2.0` adds optional `performance_analysis`. Policy `1.3.0` adds optional typed
`refusal_compliance_analysis`, derived symmetrically over the M4-selected population. Compatibility
defaults keep schema-1
policy-`1.0.0`, policy-`1.1.0`, and policy-`1.2.0` artifacts readable without fabricating absent
performance or refusal data. Performance analysis records semantic/aggregation versions,
directional performance evidence hashes, selected
terminal and execution-cost sample identities, all thirteen typed metrics, units, paired
missingness, metric-specific comparability/reasons, paired summaries/deltas, and finish-reason
counts. Performance hashes include attempt index/retry/outcome/duration and normalized terminal
usage/timing/finish reason; descriptive attempt wall timestamps are excluded.

`evaluator_resolution` retains availability for every case on each side. Run-global registry
failures are diagnostic; `evaluator_unavailable` enters quality comparability only when an
unavailable evaluator affects the identical-benchmark population or an identity-verified common
intersection case. One-sided and definition-mismatched failures therefore remain auditable
without changing the selected intersection's quality reasons.

Timeout and retry settings remain structured profile evidence. For verified intersections their
quality impact is determined from completeness of evaluator-available verified common cases,
while whole-source completeness remains diagnostic. `benchmark.case_definitions` is
`not_applicable`, not `match`, when the runs have no shared case IDs.

An intersection may meet its sample coverage threshold while selecting no fully scored common
case. In that situation `quality.verified_intersection.selected_population` is `incomplete`, uses
reason `empty_matched_scored_population`, and records verified/evaluator-available counts,
per-case scored repeat indexes, expected repeats, observed/required coverage, and selected count
zero. The ordinary coverage evidence may simultaneously remain `match`, because coverage and
case-level selectability are separate claims.

The default comparison text labels top-level `coverage` as `Source coverage` whenever
`verified_intersection` exists, then renders `verified_intersection.coverage` separately as
`Intersection coverage`. Insufficient intersection coverage also displays both configured
minimums. Full-suite output retains its single `Coverage` line; JSON fields are unchanged.

Intersection comparisons additionally emit quality evidence at
`coverage.verified_intersection.minimum_required`. Its directional values are the effective
per-run thresholds applied to the selected intersection; a difference produces
`coverage_threshold_difference` independently of observed source coverage or
`coverage_insufficient`. The `coverage.verified_intersection` evidence values also include each
side's ratio, threshold, and sufficiency so the acceptance decision is self-describing.

Stored `summary.json` and `evaluation.json` files are not used as asymmetric score authorities.
Comparison evaluates both canonical response sets in memory and writes nothing back. See
[Same-benchmark comparison](comparison.md) for the full contract.
