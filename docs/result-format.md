# Result format specification

ElaraBench v1 will store each run as an inspectable filesystem artifact:

```text
runs/<run-id>/
├── manifest.json
├── events.jsonl
├── samples/
│   └── <case-id>/
│       └── <repeat-id>/
│           ├── request.json
│           ├── response.json
│           ├── evaluation.json
│           └── artifacts/
└── summary.json
```

The precise schema will be versioned when its domain models are implemented.

## Artifact roles

- `manifest.json` records run identity, resolved configuration, benchmark identity and hash,
  model/backend identity, environment, reproducibility controls, and run status.
- `events.jsonl` is an append-oriented lifecycle and diagnostic record suitable for recovering
  the history of interrupted or retried work.
- `samples/` preserves the exact materialized request, canonical raw response, attempt or error
  metadata, and derived evaluation for every case and repeat.
- `summary.json` contains regenerated aggregate scores and comparison-oriented statistics.

Raw provider responses are canonical. They must be preserved without requiring scores or
reports to reconstruct them. Evaluations are derived from the canonical request, response,
fixtures, and evaluator configuration and may be regenerated without invoking a model.

`summary.json` is always derived and disposable. It must never contain the only copy of a raw
response, individual score, error, model identity, or other canonical run data.

Credentials, authorization headers, and secret environment values must never be stored in run
artifacts. Redaction must preserve enough non-secret endpoint and configuration identity for
comparison.
