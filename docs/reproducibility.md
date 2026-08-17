# Reproducibility contract

ElaraBench separates physical execution identity, logical reproducibility identity, and observed
execution environment.

## Run ID

A run ID names one physical execution. The generated form is a UTC, human-sortable timestamp plus
the first twelve run-fingerprint characters. A user may provide another safe unique identifier.
The timestamp and run ID never participate in deterministic identity.

## Run fingerprint

Fingerprint schema version 1 is SHA-256 over canonical JSON containing:

- Benchmark snapshot/content hashes, ordered case IDs, evaluator configuration hashes, and the
  ordered sample/request-hash plan.
- Provider type and adapter version, sanitized endpoint identity, declared capabilities, model
  name/digest, quantization, backend version, tokenizer/family/format/size, model capabilities,
  and hashes of model parameters/template metadata when discoverable.
- Resolved generation parameters and global seed, provider seed requested/supported/applied
  state, repeats, timeout, deterministic retry policy, concurrency (exactly one), and minimum
  scored coverage.
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

## Canonical hashing

All identities use deterministic UTF-8 JSON with sorted object keys, fixed separators, preserved
array order, explicit null/default values, and rejection of non-finite numbers. Suite content
hashes include validated execution/scoring metadata, ordered cases, relative fixture paths, and
fixture-byte SHA-256 hashes. Snapshot self-hashes include embedded fixture bytes and effective
coverage policy. Request hashes include ordered messages, generation parameters, resolved seed,
timeout, and response constraint. Modification times, inodes, YAML formatting, dictionary
insertion order, and absolute paths do not participate.

## Comparison classes

Comparison classification remains a future derived feature, but stored M2 evidence supports the
approved contract:

### Strictly comparable

Benchmark/snapshot, materialized requests, evaluator definitions, provider/model identity,
quantization, inference settings, and relevant execution semantics match. Run ID/time may differ.

### Qualified comparison

Results remain useful but a known difference may affect output, score, or performance—for
example hardware/runtime, backend version, quantization, generation setting, template, or
evaluator revision. The difference must be disclosed.

### Not directly comparable

Canonical evidence or material identity is missing, corrupt, or incompatible enough that a
direct score claim would mislead. Results can still be inspected independently.

Classification must remain derived and never rewrite canonical run artifacts.
