# Result format: schema version 2

M2 produces inspectable filesystem runs:

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

Unsupported production result schemas fail validation rather than being reinterpreted.

## Canonical and derived artifacts

`benchmark.json`, requests, attempt records, and final responses are canonical. They are written
atomically and cannot be silently overwritten. An interrupted provider call has an interrupted
attempt but no fabricated `response.json`. A finalized provider failure is itself a canonical
response and is not retried during normal resume.

Evaluations and `summary.json` are derived. Evaluation replacement must be explicit; summaries
are atomically replaceable. `summary.json` must never hold the only copy of a response, error,
individual evaluation, benchmark definition, or identity field.

All JSON is UTF-8, sorted, indented, and newline-terminated where practical. Atomic-write
temporary files have a reserved name prefix. Resume safely removes only those leftovers and
aborts on corrupt finalized JSON.

## Manifest

The manifest has immutable identity/configuration sections and one guarded mutable `lifecycle`
section. It records result schema version, physical run ID, deterministic run fingerprint,
ElaraBench/Git source identity, suite and snapshot hashes, resolved run configuration, sanitized
provider endpoint and capabilities, model digest/quantization/backend/template metadata,
seed support/application status, ordered request hashes, execution environment, timestamps,
status, and resume count. Hardware lives in `environment`; it is not silently folded into the
run fingerprint.

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

## Summary and coverage

Repeats are averaged per case, then positive case weights form a macro average. Category and tag
breakdowns use the same case weighting. Repeat statistics include count, mean, min, max, and
population variance/standard deviation for at least two scores.

Coverage is `scored samples / (cases × repeats)`. A valid score of zero is covered; provider
errors, invalid output, pending review, and missing results are not. The default minimum is 0.95.
At or above it, `score` is the headline aggregate. Below it, `score` is null and `partial_score`
retains the available aggregate. Status counts remain separate; unscored samples never silently
become zero.
