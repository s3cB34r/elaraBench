# Reproducibility contract

ElaraBench treats reproducibility metadata as part of every benchmark result. A run is not
fully interpretable without the benchmark, request, model, inference, environment, and scoring
identities that produced it.

## Required identity and controls

The planned v1 run format will record, where applicable:

- ElaraBench version, source revision, and dirty-worktree state.
- Benchmark schema version, suite ID and version, and a content hash covering cases and
  fixtures.
- Exact ordered and materialized model requests.
- Provider type, backend version, and non-secret endpoint identity.
- Model name, immutable digest or file hash, quantization, tokenizer, and chat template.
- All resolved generation parameters, seed, and whether the backend supports each requested
  determinism control.
- Stable case ordering, repeat count, concurrency, timeout, and retry policy.
- Python, dependency, operating-system, architecture, CPU, GPU, driver, and relevant runtime
  versions.
- Evaluator type, version, configuration, fixture hash, and sandbox identity for executable
  evaluation.
- Raw outputs, attempt history, errors, timestamps, and monotonic durations.

Secret values are excluded or redacted. Missing or undiscoverable metadata must be reported as
unknown rather than guessed.

Identical settings do not guarantee bit-for-bit model output across every backend or GPU. Runs
with stochastic or nondeterministic components should use repeats and report observed variance.

## Comparison classes

### Strictly comparable

The benchmark content, materialized prompts, evaluator definitions, model identity,
quantization, inference settings, repeat policy, and material execution conditions match.
Differences are limited to fields that cannot affect model output or scoring, such as run ID or
wall-clock start time.

### Qualified comparison

The runs are useful to compare, but one or more known differences may affect output or scoring.
Examples include a backend version, quantization, generation parameter, hardware/runtime,
prompt template, or evaluator revision. A comparison must enumerate these differences rather
than silently treating the runs as equivalent.

### Not directly comparable

Canonical inputs, raw outputs, model identity, benchmark identity, evaluator provenance, or
other material metadata is missing or incompatible enough that a direct score comparison would
be misleading. Results may still be inspected independently.

Comparison classification is itself derived. It must be reproducible from stored run metadata
and must never modify canonical run artifacts.

## M1 canonical hashing contract

M1 identities use SHA-256 over deterministic UTF-8 JSON. Object keys are sorted, separators are
fixed, non-ASCII text is preserved, non-finite numbers are rejected, and semantically meaningful
array order is retained. Dictionary insertion order, whitespace in YAML/JSONL, absolute suite
paths, modification times, inode numbers, and other filesystem metadata do not participate.

- A case hash includes every validated case field, including ordered messages and evaluator
  specification.
- A generation-request hash includes ordered messages, provider-neutral generation parameters,
  seed, timeout, and optional response format.
- An evaluator hash includes its type, configuration, ordered composite children, and weights.
- A suite hash includes validated execution/scoring metadata, ordered validated cases, fixture
  relative paths, and SHA-256 digests of every referenced fixture's bytes.

Changing a prompt, evaluator configuration, case order, fixture path, or fixture contents changes
the relevant identity. Touching a file without changing its contents does not.
