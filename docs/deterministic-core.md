# Deterministic core and local execution

## Domain and benchmark core

Strict frozen Pydantic models define ordered messages, generation requests/responses/errors,
provider capabilities and model identity, suites/cases/evaluator specifications, sample and
attempt identity, run configuration/manifest/events/snapshot, evaluation results, coverage, and
aggregation summaries. Unknown fields and invalid bounds are rejected.

The loader validates `suite.yaml` and every nonblank `cases.jsonl` line, preserves case order,
rejects duplicate IDs or unsupported schema versions, validates evaluator configuration, and
safely resolves fixtures beneath the suite. It hashes but never executes fixtures. The M2
snapshot embeds complete suite definitions and fixture bytes for source-independent rescoring.

## Providers

`ModelProvider` exposes adapter version, `describe`, `capabilities`, `generate`, sanitized endpoint
metadata, and `close`. The deterministic `FakeProvider` maps canonical request hashes to scripted
responses/errors and keeps runner tests entirely offline.

`OllamaProvider` uses the native non-streaming `/api/chat` endpoint through one synchronous HTTPX
client. Lifetime-cached preflight calls `/api/version`, `/api/show`, and `/api/tags` discover
version, digest, quantization, format/family/size, tokenizer, capabilities, and hashes of large
parameters/template metadata. Missing metadata remains unknown rather than guessed. Messages and
all supported generation controls are translated explicitly; tools are unsupported in M2, and
unknown request fields cannot enter strict models.

Timeout, connection/protocol, HTTP, provider-payload, malformed JSON/success, configuration,
interruption, and internal errors remain distinct. HTTP 408, 429, 500, 502, 503, and 504 plus
transient transport/timeouts are retryable; authentication, invalid requests, malformed success,
and non-transient provider failures are not. Retries have no jitter and preserve every attempt.

## Evaluation and aggregation

The explicit evaluator mapping implements exact and normalized match, numeric tolerance,
multiple choice, regex full match, JSON parse/Schema, required/forbidden content, and weighted
composite evaluation. Evaluators receive stored response evidence only. There is no fuzzy match,
embedding, LLM judge, human-review UI, plugin discovery, or dependency-injection framework.

Evaluation results retain evaluator name/version and configuration hash. Aggregation excludes
unscored states, averages repeats per case, applies case weights, exposes category/tag breakdowns,
reports status counts and repeat variance, and gates the headline score on sample coverage.

## Services and testability

The sequential `Runner` owns orchestration, retry/backoff, interruption, resume, and lifecycle
updates while knowing only the provider protocol. `scoring.py` owns offline evaluation and summary
regeneration. The filesystem store owns safe paths, atomic JSON writes, canonical overwrite
protection, typed event append, and guarded lifecycle updates; it never generates or evaluates.

Normal tests use FakeProvider and HTTPX `MockTransport`. They require no network, model, GPU,
Ollama service, Docker, llama.cpp, remote API, or Elara Core. Live Ollama verification is optional
and is not part of normal CI.
