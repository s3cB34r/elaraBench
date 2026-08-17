# M1 deterministic core

M1 supplies stable, offline building blocks; it does not execute real language models.

## Domain concepts

Pydantic models in `elarabench.models` validate ordered chat messages, provider-neutral
generation requests and responses, model identity, benchmark suites and cases, evaluator
specifications and results, sample repeat identity, foundational run configuration and manifest
data, and aggregation summaries. Models reject unknown fields and are immutable after creation.

Generation responses keep normalized text, finish reason, optional usage and timing, normalized
errors, and an optional JSON provider payload. Evaluator results keep status, nullable bounded
score, pass/fail, metrics, explanation, evaluator name/version, configuration hash, and optional
artifact metadata.

## Provider contract and fake provider

`ModelProvider` defines only `describe`, `capabilities`, and `generate`. The deterministic
`FakeProvider` has stable identity and no external dependencies. Tests can map canonical request
hashes to exact responses or normalized errors; unconfigured requests return text derived from
their request hash. It does not sleep, access a network, run a model, or evaluate output.

## Evaluation and dispatch

`Evaluator` defines one `evaluate(context)` operation over stored response evidence. A small
explicit mapping connects documented evaluator type names to built-in implementations. Unknown
types and invalid configuration raise clear errors during benchmark loading. There is no dynamic
discovery, entry point, dependency injection, or provider call in evaluation.

## Deliberately deferred

M2 remains responsible for real provider access and full run orchestration: Ollama, request
lifecycle timeouts, retries, resume, scheduling, and the production metadata snapshot. Because
M1 does not yet create that complete snapshot, CLI `score` and `summarize` commands would be
misleading and are not present.
