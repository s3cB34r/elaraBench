# Architecture specification

ElaraBench is a standalone Python project for reproducible, provider-neutral model
evaluation. It remains independent from Elara Core: neither package imports the other, and
ElaraBench does not rely on Elara Core configuration, storage, services, or runtime state.

## Approved data flow

```text
Benchmark definitions
        ↓
Runner → Provider adapter → Model/backend
        ↓
Immutable raw run artifacts
        ↓
Deterministic evaluators
        ↓
Derived summaries/comparisons
```

Each layer has one responsibility:

- Benchmark definitions describe model inputs, fixtures, evaluation intent, and provenance.
  They contain no provider-specific behavior.
- The future runner will validate inputs, orchestrate requests, and preserve results. It will
  not decide provider protocol details or scoring semantics.
- Provider adapters will translate normalized requests to model backends and preserve raw
  responses. They contain no evaluation logic.
- Raw model responses are canonical run data. They are immutable once captured.
- Evaluations, summaries, and comparisons are derived. Scoring must be repeatable over stored
  responses without calling the model again.

## v1 constraints

ElaraBench v1 deliberately uses ordinary versioned files for benchmark definitions and
filesystem directories for run artifacts. It has no database and no general plugin framework.
New provider and evaluator implementations will use explicit Python interfaces when their
milestones begin; M0 defines neither interface speculatively.

Deterministic evaluation is preferred. When a task cannot be scored deterministically, human
or LLM-assisted evaluation must be recorded as a distinct evaluation mode with its own
provenance. Such judgments must not be presented as deterministic results.

Future executable coding or cybersecurity evaluation must run in a sandbox isolated from the
host workspace and network according to the benchmark policy. Sandbox implementation is not
part of M0.

Reproducibility metadata is a first-class run output, not optional diagnostic information.
The expected contract is defined in [reproducibility.md](reproducibility.md).
