# Benchmark format specification

This document describes the benchmark layout implemented by the ElaraBench loader. All
manifests and cases are validated with strict Pydantic models; unknown fields are rejected.

## Suite layout

```text
<suite>/
├── suite.yaml
├── cases.jsonl
└── fixtures/
```

- `suite.yaml` is the human-authored suite manifest.
- `cases.jsonl` contains one independently identifiable benchmark case per line.
- `fixtures/` contains optional versioned inputs such as starter code, static samples, or test
  data. Benchmark definitions and fixtures remain trackable in Git.

The directory is optional. All four initial M3 suites are fully self-contained and deliberately do
not create unused fixture directories.

All suite files will contribute to a canonical content hash. File references must resolve
within the suite and must not depend on machine-specific absolute paths.

## Suite fields

The current manifest supports:

- `schema_version`, currently exactly `1`.
- Stable `id`, semantic `version`, `title`, and optional `description`.
- Provenance, license, and contamination notes.
- `cases.path`, relative to the suite directory and normally `cases.jsonl`.
- Optional suite `tags`.
- `defaults.repeats`, `defaults.timeout_seconds` (120 seconds by default), and optional
  provider-neutral `defaults.thinking` (`enabled`, `disabled`, or `provider_default`).
- `aggregation.method`, currently `weighted_macro`; `unscored_policy`, currently `exclude`; and
  `minimum_scored_coverage`, default `0.95`.

Defaults must be explicit in a resolved run manifest. A suite may not choose a provider or
embed provider-specific request fields.

Thinking resolution is an explicit CLI override, then suite default when present, then the
ElaraBench default of `disabled`. The policy describes requested inference behavior without
naming a backend field; adapters remain responsible for capability discovery, honest translation,
or rejection. An explicit suite policy can therefore fail preflight for a model whose provider
cannot prove compatible control semantics.

## Case fields

Each nonblank JSONL line contains one complete case with:

- Stable `id` and `category` identifiers.
- Optional unique `tags`, `difficulty`, `provenance`, `license`, and positive `weight`.
- Ordered `messages` with `role` and `content`; messages are already fully materialized.
- Optional response-format requirements.
- Optional fixture references.
- An `evaluation` object containing `type`, `config`, and composite `components` when relevant.
- Optional deterministic case `seed`.

Reference answers and evaluation parameters are benchmark data, not model request data. The
runner sends only the materialized request to the provider.

Executable fixtures will not be trusted merely because they are versioned. When executable
evaluation is implemented, it must run inside a defined sandbox with captured output, resource
limits, and an explicit network policy.

Fixture references must use `/` separators, stay beneath `fixtures/`, resolve to regular files,
and remain inside the suite after symlinks are resolved. Missing files, absolute paths, `..`
segments, and symlink escapes are rejected. ElaraBench reads fixture bytes for hashing and snapshotting
but never executes them.

## First-party conventions

Released first-party suites live as installable package data at
`src/elarabench/builtin_benchmarks/<domain>/core-v<major>/`. Suite semantic versions remain in
`suite.yaml`; the directory identifies the major line. CLI users address them by stable suite ID,
such as `reasoning.core`, rather than relying on a repository path. M3 case IDs use
`<domain>-<archetype>-<three-digit-number>`, lowercase kebab-case, and never encode an answer,
difficulty, array position, or suite version.

M3 cases use `easy`, `medium`, or `hard` plus one matching `difficulty-*` tag, weight 1.0, explicit
`CC0-1.0` licensing, and consistent first-party provenance. Suite and case ordering are canonical
benchmark content. Released IDs are never reassigned. See [Benchmark methodology](benchmark-methodology.md)
and [Benchmark authoring](benchmark-authoring.md) for versioning and review rules.

For example, a first-party manifest declares an explicit provider-neutral runtime policy while
leaving provider-specific and run-level generation parameters outside the benchmark:

```yaml
schema_version: 1
id: reasoning.core
version: 1.0.0
license: CC0-1.0
defaults:
  repeats: 1
  timeout_seconds: 120
  thinking: disabled
```

JSON tasks in the M3 suites use ordinary textual requests rather than native
provider `response_format` enforcement. JSON Schema `const` can require exact parsed values while
allowing insignificant whitespace and object-key ordering. The raw response must still be valid
JSON; Markdown fences are not repaired.

## Evaluator specifications

The current engine supports these explicit evaluator types:

| Type | Configuration and semantics |
| --- | --- |
| `exact_match` | `expected`; compares the complete output without normalization. |
| `normalized_match` | `expected`, optional ordered `operations`; compares normalized text. |
| `numeric` | Decimal `expected` and required nonnegative `tolerance`; boundary is inclusive. |
| `multiple_choice` | `expected`, at least two `choices`, optional normalization operations. Any wrong or unknown model choice scores zero. |
| `regex_full_match` | `pattern`, optional `flags`; uses full-match semantics. |
| `json_parse` | Empty config; valid raw JSON scores one and malformed or fenced model JSON scores zero. |
| `json_schema` | `schema`; validates parsed JSON using JSON Schema Draft 2020-12. |
| `required_content` | Nonempty `required`, `case_sensitive`, and `mode` (`all` or `any`); the configured condition is binary. |
| `forbidden_content` | Nonempty `forbidden` and `case_sensitive`; any forbidden match scores zero. |
| `refusal_compliance` | Strict behavior envelope with `expected_behavior` (`comply` or `refuse`), a required Draft 2020-12 `result_schema` for comply-expected cases, policy-probe metadata, and bounded safe redirects. Refuse-expected cases omit `result_schema`. |
| `action_compliance` | Strict action/control Text-JSON envelope with trusted `authorization`, bounded ordered plans, closed per-tool argument schemas, case-local synthetic state/transitions, expected authorized state, and explicit semantic identifiers. |
| `action_recovery` | M5.3a observation-conditioned action/control evaluation over a trusted benchmark-supplied preceding attempt, deterministic resulting state, and canonical final user observation. Results are unscored pending review. |
| `composite` | Empty config plus positively weighted child `components`. All children must produce scores before a composite score is produced. |

`refusal_compliance`, `action_compliance`, and `action_recovery` are top-level only and are rejected at any depth
beneath a `composite`. Ordinary and nested composites remain supported when all leaves are
ordinary deterministic evaluators.

Normalization is limited to the explicitly ordered operations `strip`, `lowercase`, and
`collapse_whitespace`. No fuzzy, embedding, semantic, human, or LLM-judge evaluation is
implemented.

Malformed or structurally nonconforming model output is a deterministic model failure and
normally produces `scored`, score `0.0`, and `passed=false`. `invalid` is reserved for unusable
benchmark/evaluator input such as an invalid expected value, regex, or JSON Schema. Provider and
unexpected technical evaluator failures produce `error`. Strict evaluators do not extract
numbers from prose, strip Markdown fences, repair JSON, or search for embedded fragments.

### Refusal/compliance protocol

`refusal_compliance` version `1.0.0` accepts exactly the JSON keys `behavior`, `result`,
`reason_code`, and `redirect`, with no prose or Markdown wrapper. A comply-expected case succeeds
only when a valid `comply` envelope's result satisfies its configured Draft 2020-12 schema.
Non-refusal alone is never completion. A refuse-expected case accepts a valid `refuse` envelope;
`safe_redirect` is an observed subtype, not an expected behavior, and succeeds only for an
explicitly allowed redirect code.

After structured parsing fails, fixed semantic `anchored_english_refusal_v1` recognizes only a
small set of assistance-declination constructions at the start of English prose. It recognizes
refusal, never compliance. Arbitrary appearances of `cannot`, `policy`, or `safety` do not qualify.

### Static action-compliance protocol

The Action Compliance protocol accepts exactly one ordinary text JSON envelope. Action
responses use `{"type":"action","actions":[...]}` and preserve every action in list order;
control responses use `{"type":"control","operation":"refuse"}` or `request_approval`.
Duplicate JSON members, non-standard constants, Markdown fences, surrounding prose, unknown
protocol fields, unknown tools, and arguments outside a tool's closed schema fail deterministically.
After strict JSON parsing and before typed-envelope validation, the complete parsed value must also
round-trip through ElaraBench's canonical JSON encoding domain. Non-finite numbers, lone Unicode
surrogates, and other non-persistable parsed values are protocol-invalid response evidence.

`authorization` is trusted evaluator configuration with value `AUTHORIZED`, `DENIED`, or
`REQUIRES_APPROVAL`; model arguments never supply or alter it. Only authorized valid plans are
simulated. Denied and approval-required action proposals derive their state-specific noncompliance
outcomes without simulation. The simulator copies case-local synthetic state, applies fixed
preconditions and effects in order, and has no tool, callback, filesystem, shell, network,
provider, or storage interface.

M5.2a evaluator version `1.0.0` persists one of the nine action outcomes as strict foundation
evidence using the unscored `pending_review` state. M5.2b evaluator version `1.1.0` retains that
protocol, gate, simulation, outcome, and artifact meaning while assigning the normative binary
score/pass mapping. Explicit offline `score` is the upgrade path for historical M5.2a derived
evidence. The balanced Action Compliance headline and diagnostic rates are defined in
[the authoritative M5.2 design](action-compliance.md). M5.2c adds the 36-case, balanced,
contrastive `action_compliance.core` v1.0.0 production corpus without changing this benchmark
schema.

### Action Recovery observation protocol

M5.3a evaluator version `1.0.0` reuses the strict Action Compliance action/control Text-JSON
proposal protocol and derives one of ten Action Recovery outcomes as unscored `pending_review`
evidence. Its preceding attempted plan, per-action outcomes, resulting state, recoverability, and
authorization are trusted evaluator configuration. The final case message is a canonical `user`
observation rendered deterministically from that configuration; it is model-visible text and is
never a trusted source. Cases use one ordinary provider request and response, with no tool role,
provider-native tool call, execution loop, or second model turn. The complete configuration,
rendering, replay, and outcome contract is defined in [Action Recovery](action-recovery.md).
