# Benchmark format specification

This document describes the planned ElaraBench v1 benchmark layout. M0 does not implement the
corresponding Pydantic models or loader.

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

All suite files will contribute to a canonical content hash. File references must resolve
within the suite and must not depend on machine-specific absolute paths.

## Planned suite fields

The v1 manifest is expected to describe:

- Schema version.
- Stable suite ID, semantic suite version, title, and description.
- Provenance, license, and contamination notes.
- Location of the case file.
- Tags and benchmark categories.
- Conservative defaults such as timeout and repeat count.
- Aggregation policy.

Defaults must be explicit in a resolved run manifest. A suite may not choose a provider or
embed provider-specific request fields.

## Planned case fields

Each JSONL record is expected to contain:

- Stable case ID.
- Category, tags, optional difficulty, provenance, and weight.
- Ordered chat messages with roles and content.
- Optional response-format requirements.
- Optional fixture references.
- Evaluation type and its explicit parameters.
- Optional deterministic case seed or repeat override.

Reference answers and evaluation parameters are benchmark data, not model request data. The
future runner must send only the materialized request to the provider.

Executable fixtures will not be trusted merely because they are versioned. When executable
evaluation is implemented, it must run inside a defined sandbox with captured output, resource
limits, and an explicit network policy.
