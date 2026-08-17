# Benchmark format specification

This document describes the benchmark layout implemented by the ElaraBench M1 loader. All
manifests and cases are validated with strict Pydantic models; unknown fields are rejected.

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

## Suite fields

The M1 manifest supports:

- `schema_version`, currently exactly `1`.
- Stable `id`, semantic `version`, `title`, and optional `description`.
- Provenance, license, and contamination notes.
- `cases.path`, relative to the suite directory and normally `cases.jsonl`.
- Optional suite `tags`.
- `defaults.repeats` and `defaults.timeout_seconds`.
- `aggregation.method`, currently `weighted_macro`, and `unscored_policy`, currently `exclude`.

Defaults must be explicit in a resolved run manifest. A suite may not choose a provider or
embed provider-specific request fields.

## Case fields

Each nonblank JSONL line contains one complete case with:

- Stable `id` and `category` identifiers.
- Optional unique `tags`, `difficulty`, `provenance`, `license`, and positive `weight`.
- Ordered `messages` with `role` and `content`; messages are already fully materialized.
- Optional response-format requirements.
- Optional fixture references.
- An `evaluation` object containing `type`, `config`, and composite `components` when relevant.
- Optional deterministic case `seed`.

Reference answers and evaluation parameters are benchmark data, not model request data. The
future runner must send only the materialized request to the provider.

Executable fixtures will not be trusted merely because they are versioned. When executable
evaluation is implemented, it must run inside a defined sandbox with captured output, resource
limits, and an explicit network policy.

Fixture references must use `/` separators, stay beneath `fixtures/`, resolve to regular files,
and remain inside the suite after symlinks are resolved. Missing files, absolute paths, `..`
segments, and symlink escapes are rejected. M1 reads fixture bytes for hashing but never executes
them.

## Evaluator specifications

M1 supports these explicit evaluator types:

| Type | Configuration and semantics |
| --- | --- |
| `exact_match` | `expected`; compares the complete output without normalization. |
| `normalized_match` | `expected`, optional ordered `operations`; compares normalized text. |
| `numeric` | Decimal `expected` and required nonnegative `tolerance`; boundary is inclusive. |
| `multiple_choice` | `expected`, at least two `choices`, optional normalization operations. Output outside the choices is invalid. |
| `regex_full_match` | `pattern`, optional `flags`; uses full-match semantics. |
| `json_parse` | Empty config; valid JSON scores one, malformed JSON is invalid. |
| `json_schema` | `schema`; validates parsed JSON using JSON Schema Draft 2020-12. |
| `required_content` | Nonempty `required`, `case_sensitive`, and `mode` (`all` or `any`). |
| `forbidden_content` | Nonempty `forbidden` and `case_sensitive`. |
| `composite` | Empty config plus positively weighted child `components`. All children must produce scores before a composite score is produced. |

Normalization is limited to the explicitly ordered operations `strip`, `lowercase`, and
`collapse_whitespace`. No fuzzy, embedding, semantic, human, or LLM-judge evaluation is
implemented.

Malformed output produces `invalid` for evaluators that require a parseable representation.
Provider failures produce `error`. Neither is silently assigned score zero.
