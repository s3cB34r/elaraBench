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

Configuration, authentication/authorization, and internal adapter failures terminate a run
after available evidence is persisted. Ordinary finalized provider/backend failures affect one
sample, remain visible as evaluator errors, and never become zero scores. Conservative retries
apply only to explicitly retryable connection, timeout, protocol, and transient HTTP failures.

`KeyboardInterrupt` is handled at the orchestration boundary. Completed artifacts remain valid,
an in-flight attempt is recorded as interrupted when possible, no response is fabricated for
incomplete generation, a partial summary is regenerated, and lifecycle becomes `interrupted`.
Ollama client cancellation cannot guarantee that server-side generation has already stopped.

## Resume and offline derivation

Resume reads configuration only from stored canonical artifacts. Before lifecycle mutation it
checks the manifest, self-contained snapshot, fingerprint, adapter/model/source identity,
request plan, and all existing finalized JSON. Matching completed responses—including finalized
provider errors—are never regenerated. Missing or stale evaluations are derived again. A
matching request without a response continues, and a terminal attempt left just before response
finalization can be recovered without another provider call. Request mismatch, fingerprint
incompatibility, or corrupt finalized JSON aborts resume.

`score` dispatches evaluators over canonical stored responses and replaces only derived
evaluations and summary. `summarize` reads evaluations and replaces only the summary. Neither
service constructs a provider, reads the original suite directory, or uses the network.

## Deliberate v1/M2 limits

M2 uses a filesystem store, not a database, and an explicit provider factory, not a plugin
framework. There is no parallelism or locking; `events.jsonl` has one writer. Events are useful
diagnostics, not event-sourced recovery state—canonical sample files remain authoritative.

Human/LLM-assisted judgment remains separate and unimplemented. Future executable coding or
cybersecurity evaluators must use sandbox isolation; M2 never executes fixtures. OpenAI-compatible
and llama.cpp-specific adapters, tool/agent benchmarks, distributed execution, Unsloth, Elara
Core integration, dashboards, and leaderboards are outside M2.
