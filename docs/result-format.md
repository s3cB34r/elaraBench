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

M1 implements validated foundational models and an `ArtifactStore` API for this shape. It does
not implement the runner that will populate complete production runs.

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

Manifest, request, and response files are canonical and use atomic create-without-overwrite
writes. An existing canonical file raises an error. Evaluations are derived but also refuse
replacement unless the caller explicitly requests it. Summaries are derived, atomically
replaceable, and expected to be regenerated. JSON is UTF-8, sorted, indented, and terminated by
a newline for inspection.

Credentials, authorization headers, and secret environment values must never be stored in run
artifacts. Redaction must preserve enough non-secret endpoint and configuration identity for
comparison.

## M1 aggregation

Aggregation first averages scored repeats within each case. It then applies positive case
weights to a macro score over cases that have at least one scored repeat. Category and tag
breakdowns use the same case-based weighting. Repeat statistics include count, mean, minimum,
maximum, and population variance/standard deviation when at least two scores exist.

Counts for `scored`, `invalid`, `error`, and `pending_review` are preserved independently.
Unscored states are excluded under the only M1 policy and never silently converted to zero.
No confidence interval is manufactured.
