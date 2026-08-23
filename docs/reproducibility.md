# Reproducibility contract

ElaraBench separates physical execution identity, logical reproducibility identity, and observed
execution environment.

## Run ID

A run ID names one physical execution. The generated form is a UTC, human-sortable timestamp plus
the first twelve run-fingerprint characters. A user may provide another safe unique identifier.
The timestamp and run ID never participate in deterministic identity.

## Run fingerprint

Fingerprint schema version 3 is SHA-256 over canonical JSON containing:

- Benchmark snapshot/content hashes, ordered case IDs, evaluator configuration hashes, and the
  ordered sample/request-hash plan.
- Provider type and adapter version, sanitized endpoint identity, declared capabilities, model
  name/digest, quantization, backend version, tokenizer/family/format/size, model capabilities,
  and hashes of model parameters/template metadata when discoverable.
- Resolved generation parameters, thinking policy, global seed, provider seed
  requested/supported/applied state, repeats, timeout, deterministic retry policy, concurrency
  (exactly one), and minimum scored coverage.
- ElaraBench version and Git/source identity, including dirty source-state hash when available.

The fingerprint excludes run timestamps and ID, absolute suite/run paths, host name, CPU/GPU
model, driver, OS, architecture, and general runtime environment. Endpoint data is included only
after credential rejection/sanitization because endpoint/backend identity can affect execution
semantics. Specific backend and model-runtime versions belong in provider/model identity when
they can change inference; the general machine description remains separate.

## Execution environment

The manifest separately records Python version/implementation, OS/release, architecture, CPU,
GPU list, NVIDIA driver when available, relevant package/runtime versions, and discovery
diagnostics. No host name or broad environment-variable capture occurs. Optional probes are
bounded and best effort; absent or broken `nvidia-smi` never prevents a run.

Environment differences can later qualify comparability and explain performance without making
otherwise equivalent logical configurations impossible to group by fingerprint. Supplying a
seed records intent and backend capability/application status; it is never represented as a
guarantee of deterministic model output. Repeats report observed variance.

Thinking has the same identity status as temperature, seed, and timeout. Changing between
`enabled`, `disabled`, and `provider_default` changes materialized request hashes and the run
fingerprint, and prevents incompatible resume. `provider_default` is explicit metadata, but it is
less reproducible: identical ElaraBench configuration can execute differently after a provider,
model, or template default changes. Strict comparison therefore requires matching thinking
policy and compatible runtime behavior; provider-default runs may require qualified comparison.

The manifest and fingerprint also include the discovered provider/model Thinking-control
interpretation. A valid `/api/show` capability list without thinking records `none`. A broad
`thinking` capability cannot distinguish boolean from level-valued control and therefore falls
back to `unknown` without stronger architecture evidence. The adapter has a deliberately small
exact-architecture compatibility table: Ollama `general.architecture` values `qwen3` and `qwen35`
are boolean-controllable (the latter matches the validated qwen3.5 smoke path), while `gptoss` is
level-valued. This uses provider-reported architecture, never a model alias or substring;
unrecognized architectures remain unknown. Ollama's documented Thinking contract explains that
[most models use booleans while GPT-OSS requires levels](https://github.com/ollama/ollama/blob/main/docs/capabilities/thinking.mdx).

Resume rejects a changed control interpretation. For a non-thinking model, requested `disabled`
is satisfied without a native field. Level-valued and unknown states cannot claim explicit
enforcement under v0.2.1; only `provider_default` is allowed, with omission visible in raw request
payloads. v0.2.1 intentionally does not model reasoning-effort levels.

Result schema v3 introduced this canonical Thinking identity and fingerprint version. Historical
schema-v2 artifacts are verified with their original no-Thinking serialization and fingerprint
schema v1. They may be scored or summarized without their original suite directory, and current
derived artifacts identify schema v2 as their source. They cannot be resumed or rewritten under
v3 runtime semantics. Evaluation provenance propagates through every nested composite child.
Current regenerated summaries use summary artifact schema 3 while separately retaining source
result schema 2; this does not relabel or migrate the physical historical run.

## Canonical hashing

All identities use deterministic UTF-8 JSON with sorted object keys, fixed separators, preserved
array order, explicit null/default values, and rejection of non-finite numbers. Suite content
hashes include validated execution/scoring metadata, ordered cases, relative fixture paths, and
fixture-byte SHA-256 hashes. Snapshot self-hashes include embedded fixture bytes and effective
coverage policy. Request hashes include ordered messages, generation parameters, resolved seed,
thinking policy, timeout, and response constraint. Modification times, inodes, YAML formatting,
dictionary insertion order, and absolute paths do not participate.

The finite generation timeout defaults to 120 seconds. Timing identity and performance evidence
remain separate concepts: request timeout participates in the fingerprint, while observed client
wall latency and provider total/load/prompt/generation durations are recorded results. Cold model
load is not removed from wall latency, and v0.2.1 performs no automatic warmup. A generation read
timeout is recorded without an automatic retry; changing the timeout requires an explicit new-run
configuration and therefore a different fingerprint.

Evaluator semantics are also part of reproducible interpretation. A score of zero is canonical
scored evidence, not missing data, and participates in coverage and aggregation. `invalid` is
reserved for benchmark/evaluator inputs that cannot be trusted; `error` records technical
generation or evaluation failure. Strict parsing failures caused by model output—such as prose
where a numeric-only answer was required or Markdown fences around required raw JSON—are scored
zero without extraction or repair. Evaluator versions and configuration hashes make a later
semantic change visible, while rescoring always retains the original raw response.

## Comparison identity and classes

M4.2a derives comparison schema 1 results under policy `1.1.0`. It hashes the ordered baseline and
candidate evidence identities and benchmark identities, declared intent, selected case
population, canonical comparison evidence, current evaluator resolution/availability, and
evaluator provenance. For verified intersections it also hashes exact fixture-aware case
identities, mismatches, one-sided cases, expected repeats, common weights, weighting semantics,
and intersection coverage. Generated time, output path, and CLI formatting are excluded;
direction is material. The evidence hash uses validated run fingerprint/snapshot/request-plan and canonical
response hashes, never filesystem location or mutable summary data. A canonical response is
accepted only when it exactly matches the final attempt response and its success/error state
agrees with that terminal outcome; contradictory attempt/response artifacts are corrupt input.

Fixture-aware case hashes use fixture content hashes embedded in and validated from each physical
run snapshot. A later edit to a source suite or fixture cannot change comparison identity. Valid
schema-v2 snapshots may participate when they contain the same provable evidence; missing or
changed identity is never synthesized.

### Strictly comparable

Benchmark/snapshot, materialized requests, evaluator definitions, provider/model identity,
quantization, tokenizer, inference settings, and relevant execution semantics match. Run ID/time
may differ. Evaluator versions and status semantics must also match. For model intent, tokenizer
and template differences may instead be expected parts of the complete differing artifacts.

### Qualified comparison

Results remain useful but a known difference may affect output, score, or performance—for
example hardware/runtime, backend version, quantization, generation setting, template, or
evaluator revision. The difference must be disclosed.

### Not directly comparable

Canonical evidence or material identity is missing, corrupt, or incompatible enough that a
direct score claim would mislead. Results can still be inspected independently.

Classification remains derived and never rewrites canonical run artifacts. Physical framework or
result-schema version differences do not independently prevent strict quality comparison; actual
semantic gaps do. In particular, historical schema-v2 evidence lacks explicit Thinking identity
and is qualified rather than silently assigned v3 defaults. See [Same-benchmark
comparison](comparison.md) for intent, population, and exit-status policy.
