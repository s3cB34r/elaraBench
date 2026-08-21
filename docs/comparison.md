# Same-benchmark comparison

ElaraBench M4.1 compares two physical runs only after checking whether their quality evidence
supports a direct delta. The command is directional:

```bash
elarabench compare BASELINE_RUN CANDIDATE_RUN --intent model
```

Every delta is `candidate - baseline`. A normalized score movement of `+0.0833` is reported as
`+8.33 percentage points`, not `+8.33%`.

## Evidence and evaluation

Comparison opens both runs read-only, verifies each manifest, benchmark snapshot, run
fingerprint, request plan, and canonical request hash with the physical result-schema rules, then
evaluates canonical responses symmetrically with the currently installed evaluator registry.
Stored summaries are not scoring authority and comparison never regenerates evaluations,
summaries, events, or manifests. Its `evaluation_mode` is `current_in_memory`; per-case evaluator
name, version, configuration hash, and physical source-result schema are retained.

Input corruption, a missing run, or an unsupported physical schema is an operational error. Two
valid runs that fail methodological checks instead produce a serializable
`not_directly_comparable` result.

Each directional run reference carries its own suite ID, suite version, benchmark content hash,
and snapshot hash. Same-suite output may display one concise shared identity, while cross-suite
output preserves and displays the baseline and candidate identities separately.

## Intent and controlled variables

`ComparisonIntent` declares the intended independent variable:

- `model` (the default) permits different known model artifacts. It is also the neutral mode for
  a candidate that may be a fine-tune; M4.1 does not verify lineage.
- `repeat` requires the same sufficiently identified artifact for a strict repeated-execution
  comparison. It does not mean different configured repeat counts.
- `quantization` expects different quantization metadata and digests, but remains qualified
  because current provider metadata does not prove common pre-quantization lineage.
- `thinking` expects explicit `enabled` versus `disabled` on the same known artifact with boolean
  control. `provider_default` or unknown control prevents strict attribution.
- `backend` expects a provider/backend difference. Unknown backend versions or uncertain
  cross-provider artifact identity qualify the result.

Other score-semantic settings—temperature, top-p, top-k, output cap, stop sequences, seed and
seed-control evidence, repeats, and Thinking—are controls. Their difference is a qualification
unless it is the selected intent. Equal aliases without model digests never establish strict
identity, and `:latest` has no immutable authority. Known different digests are expected for a
model comparison; unknown identity is not the same as intentional difference.

Chat-template identity uses the provider-discovered template hash when both runs have one. When
hashes are unavailable, ElaraBench compares the raw chat templates exactly; missing or
one-sided template evidence remains unknown rather than becoming an assumed match. Template
differences qualify repeat, Thinking, quantization, and backend attribution. Under model intent,
the template is part of the intentionally different model artifact, so the difference is exposed
without pretending the comparison isolates weights from prompt construction.

Tokenizer identity is also material. Equal known tokenizer values match; different values qualify
repeat, Thinking, quantization, and backend attribution, while missing values remain unknown.
Under model intent, tokenizer and template differences are expected parts of the complete model
artifact comparison. A model A/B result therefore does not claim to isolate weight changes from
tokenization or prompt construction.

Timeout and retry configuration affect quality only when they change actual sample outcomes or
population. With complete successful populations, their difference remains performance
evidence. Configured retry-policy differences and observed attempt-count differences are separate
evidence; extra attempts do not imply that retry configuration changed. A historical ElaraBench
framework-version or result-schema difference is never a confounder by itself; actual missing
semantics, such as schema-v2 Thinking provenance, produce typed qualifications.

## Classifications

Quality and performance have separate `ComparabilityAssessment` values:

- `strict`: benchmark/evaluator evidence and the complete scored population agree; controlled
  quality variables are known and match; only the declared independent variable differs.
- `qualified`: a useful delta exists, but a material unknown or confounder prevents a strict
  controlled claim.
- `not_directly_comparable`: valid evidence exists, but a direct headline comparison would be
  misleading.

Every decision includes typed reason codes and field evidence with a canonical path, baseline
and candidate values, methodological dimension, impact, and state (`match`, `difference`,
`expected_difference`, `unknown`, `incomplete`, or `not_applicable`). Source framework versions
are evidence, not automatic score confounders.

M4.1 only classifies performance comparability; it does not calculate latency, throughput, token,
or memory deltas. Performance is conservatively qualified even on matching environments because
warm/cold state and system load are uncontrolled. Recorded architecture, operating system and
release, Python implementation and version, CPU, GPU/driver, and runtime-version differences are
structured performance evidence. These fields do not automatically degrade quality. Backend or
environment differences are made explicit and may make direct performance claims invalid.
Timing availability means every expected canonical response has at least one non-null field in
its `TimingMetadata`; an absent or all-null timing object is missing performance evidence. Numeric
zero is a recorded value. M4.1 only classifies timing support and does not calculate timing deltas.

## Benchmark and coverage policy

A full-suite comparison requires identical benchmark content hashes, matching ordered cases and
case hashes, compatible current evaluator provenance, equal repeat configuration, and every
expected sample scored on both sides. Suite ID and version never substitute for content identity.
The same ID/version with different content is a `suite_identity_conflict` and yields no headline
delta. Different benchmark contents are deferred to M4.2 case-intersection comparison.

If the benchmark is identical but sample availability differs, ElaraBench may recompute a
clearly labeled matched-case partial comparison. A case enters that population only when every
configured repeat is present and scored on both sides; partial-repeat means remain diagnostic
case evidence and do not enter headline, category, or tag deltas. It never subtracts stored
category summaries, maps missing evidence to zero, or calls a 17/18 population—or 99/100
expected samples—a full-suite delta. Category and tag aggregates are rebuilt over exactly the
selected common cases. A deterministic bad model answer with score zero remains scored, covered,
and comparable; provider/evaluator errors do not.

Scored-population equality and completeness are distinct. If both runs omit the same repeat, the
population membership is a `match` but its completeness is `incomplete`, with
`incomplete_sample_population`; no full-suite delta is emitted. The
`scored_case_set_difference` reason is reserved for different baseline/candidate scored-sample
membership. Thus 99/100 on both sides can satisfy the ordinary 95% coverage threshold while still
being an incomplete matched-case partial comparison.

Coverage evidence separately records each run's measured ratio, configured minimum, and boolean
sufficiency. `coverage_insufficient` means at least one ratio fails its own configured minimum;
it does not imply that baseline and candidate ratios or thresholds differ. Population
completeness remains the stricter and separate requirement for a full-suite delta.

Sequential runs may be interrupted before a planned request is materialized. A sample with no
request, response, or attempt artifact is valid missing evidence and reduces coverage; comparison
does not create it. A response or attempt without its required canonical request is instead an
artifact-integrity error. An evaluation without its source response is also corruption, not
ordinary missing evidence. Materialized attempts must reference the canonical request hash from
the immutable request plan. When a canonical response exists, it must exactly equal the terminal
attempt response—including text, finish reason, usage, timing, normalized error, and raw provider
payloads—and its success/error state must agree with the terminal outcome. Earlier failed retries
remain valid history; only the final attempt produces the canonical response.

## Output and exit status

Text output summarizes quality and performance classifications with their separate reason-code
lists, coverage, available score delta, and category deltas. Complete case, category, tag,
evidence, and provenance records use comparison schema 1
and policy version `1.0.0`:

```bash
elarabench compare runs/base runs/candidate --intent thinking --json
elarabench compare runs/base runs/candidate --output comparison.json
```

With both flags, identical JSON is written and printed. Comparison fingerprints include ordered
source evidence hashes, directional benchmark identities, intent, policy version, selected case
population, canonical field evidence, current evaluator resolution/availability, and evaluator
provenance. Evaluator resolution records specification hashes and resolved versions without
exception text. Fingerprints exclude generation time, output path, and formatting; reversing
baseline and candidate changes identity. No automatic comparison directory is created.

Exit status reflects **quality comparability only** in M4.1: 0 means a strict or qualified quality
comparison was produced, 1 means quality is not directly comparable, and 2 means an
input/integrity/operational failure. Performance comparability is reported independently and does
not affect process exit status; for example, qualified quality plus not-directly-comparable
performance exits 0.

`higher`, `lower`, and `unchanged` are literal numeric directions, not significance claims.
ElaraBench reports no p-values, confidence intervals, causal fine-tune claims, or automatic
meaningfulness thresholds. Small suites and three-case categories require appropriately cautious
human interpretation.

M4.2 is reserved for different-version verified case intersections and actual latency,
throughput, token, warm-state, and richer lineage analysis.
