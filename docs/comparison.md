# Trustworthy run comparison

ElaraBench compares two physical runs only after checking whether their quality evidence supports
a direct delta. M4.2a retains M4.1 identical-benchmark behavior and adds verified case
intersection for different versions of the same suite. The command is directional:

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

For verified intersections, this completeness test uses only evaluator-available, identity-
verified common cases. A failed one-sided or definition-mismatched case remains visible in
diagnostic source coverage but cannot promote timeout or retry differences into quality reasons.
An incomplete evaluator-available verified case still makes those controls quality-relevant.

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
are evidence, not automatic score confounders. `suite_version_difference` records only that the
versions differ. `verified_intersection_comparison` appears only when the selected population is
`verified_intersection` or `verified_intersection_matched_partial`; a version difference alone
does not claim verified overlap or comparability.

M4.2b adds observed paired latency, token, throughput, and attempt-cost summaries without
turning them into causal claims. Performance is conservatively qualified even on matching
environments because warm/cold state and system load are uncontrolled. Recorded architecture, operating system and
release, Python implementation and version, CPU, GPU/driver, and runtime-version differences are
structured performance evidence. These fields do not automatically degrade quality. Backend or
environment differences are made explicit and may make direct performance claims invalid.
Timing availability means every expected canonical response has at least one non-null field in
its `TimingMetadata`; an absent or all-null timing object is missing performance evidence. Numeric
zero is a recorded value.

### Observed performance metrics

Policy `1.2.0` defines `performance_metrics_v1` over paired `(case_id, repeat_index)` samples and
aggregates them with `paired_sample_median_v1`. Every metric records expected, side-available,
paired, missing, and unpaired counts. Its baseline and candidate count/mean/median/minimum/maximum
are calculated from the same joint paired subset; median is the headline. Absolute delta is
candidate median minus baseline median. Relative delta divides by the baseline median and is
withheld, with `zero_baseline_relative_delta_unavailable`, when that median is zero. No p95/p99,
confidence interval, significance test, or causal direction label is produced.

| Metric | Immutable physical source | Unit |
| --- | --- | --- |
| `client_request_duration_seconds` | terminal response `timing.latency_seconds` | seconds |
| `provider_total_duration_seconds` | terminal response `timing.provider_total_seconds` | seconds |
| `provider_load_duration_seconds` | terminal response `timing.provider_load_seconds` | seconds |
| `provider_prompt_eval_duration_seconds` | terminal response `timing.provider_prompt_eval_seconds` | seconds |
| `provider_generation_duration_seconds` | terminal response `timing.provider_eval_seconds` | seconds |
| `terminal_attempt_active_duration_seconds` | successful terminal attempt `duration_seconds` | seconds |
| `prompt_tokens` | terminal response `usage.input_tokens` | tokens |
| `generated_tokens` | terminal response `usage.output_tokens` | tokens |
| `total_tokens` | terminal response `usage.total_tokens` | tokens |
| `generation_tokens_per_second` | output tokens / provider generation seconds | tokens/second |
| `attempt_count` | count of validated persisted attempts | count |
| `failed_attempt_count` | count of persisted attempts with failed outcome | count |
| `all_attempts_active_duration_seconds` | sum of persisted attempt durations | seconds |

Terminal generation metrics use only a final successful attempt. An all-failed sample supplies no
generation/timing/token metric, but its validated attempt counts and active durations remain
observable. `all_attempts_active_duration_seconds` is not end-to-end time: it excludes retry
backoff, queueing, gaps, and resume pauses. A truncated successful response remains valid
performance evidence, as does a successfully generated answer whose deterministic quality score
is zero.

Throughput is unavailable for missing tokens, missing generation duration, or zero duration;
zero generated tokens over positive duration is valid zero throughput. Non-finite values are
never emitted. Ollama nanoseconds are already normalized to seconds by the adapter and comparison
does not normalize them again. Time to first token is deliberately absent: current non-streaming
Ollama artifacts do not provide trustworthy TTFT evidence.

Token metrics are not directly comparable when known tokenizer identities differ and are
qualified when tokenizer identity is unknown. Provider-native duration and derived throughput
deltas are withheld across different provider/backend semantics. Client-observed and attempt
durations may remain qualified observational evidence, including under backend intent. Remote or
changed hardware/runtime/endpoint evidence qualifies interpretation but never changes quality.
`warm_state_unknown` remains present, so current observed metrics cannot become strict causal
performance claims.

Quality-aligned case identities select terminal-generation metrics. Attempt-count and all-attempt
cost metrics additionally retain the broader verified/common case identity population so failed
execution cost is not hidden. One-sided and definition-mismatched cross-version cases never enter
either paired population. Performance availability is metric-specific and is not quality
coverage.

Legacy schema-v2 runs expose only fields physically present in their canonical responses and
attempts. Recorded attempt duration/count can be summarized, while absent usage/provider timing
remains unavailable. Legacy metrics retain `legacy_identity_gap`; no tokenizer, retry, warm-state,
environment, or provider semantics are synthesized.

## Benchmark and coverage policy

A full-suite comparison requires identical benchmark content hashes, matching ordered cases and
case hashes, compatible current evaluator provenance, equal repeat configuration, and every
expected sample scored on both sides. Suite ID and version never substitute for content identity.
The same ID/version with different content is a `suite_identity_conflict` and yields no headline
delta or intersection fallback.

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

For a verified intersection, whole-source coverage remains diagnostic, while
`coverage.verified_intersection` records each selected-population ratio, effective configured
minimum, and per-side sufficiency. The separate quality-scoped
`coverage.verified_intersection.minimum_required` evidence emits
`coverage_threshold_difference` whenever those effective thresholds differ, even when both
intersection ratios satisfy them. Threshold difference and `coverage_insufficient` are
independent and may therefore appear together.

Sample coverage sufficiency does not guarantee a selectable case population. If verified,
evaluator-available cases exist but none is fully scored on both sides,
`empty_matched_scored_population` supplies quality-impacting `incomplete` evidence with per-case
scored repeat indexes, expected repeats, observed/required coverage, and selected count zero. This
is distinct from `no_verified_case_intersection` and `evaluator_unavailable`.

### Verified cross-version intersection

Different versions of the same suite may be compared only over shared case IDs whose canonical
case identities are exactly equal. Identity is suite-namespaced and combines the case ID with a
hash of every validated `BenchmarkCase` field and every referenced fixture path/content hash from
the immutable validated run snapshot. Comparison never reads a live source fixture. Different
suite IDs never intersect, even when IDs and hashes happen to match.

Aggregate `benchmark.case_definitions` evidence is `match` only when at least one case ID is
shared and every definition in that shared set matches. With no shared IDs it is
`not_applicable`, accompanied by both ordered case-ID populations; empty overlap is never treated
as vacuous definition equality.

Weight, evaluator specification, category, and tags are case identity. A change to any of them,
the prompt, response format, seed, provenance, license, or referenced fixture bytes excludes that
case. Case ordering is not individual case identity, so a properly versioned ordering-only change
can retain the full verified intersection. Current evaluator availability is applied per verified
case; unavailable cases are recorded and excluded rather than replaced with stale stored scores.
Evaluator-resolution failures for one-sided or definition-mismatched cases remain in per-side
`evaluator_resolution` records and diagnostic evidence, but do not add `evaluator_unavailable` to
quality reasons. That quality reason is reserved for unavailable evaluators that affect an
identity-verified common case (or the existing identical-benchmark population).

`verified_intersection` means every evaluator-available verified case has a complete equal-repeat
population. `verified_intersection_matched_partial` means only fully scored common cases are used,
or configured repeat counts differ. Intersection coverage is calculated only over evaluator-
available verified cases; one-sided cases do not reduce it. Source-run coverage remains separately
reported. Cross-version quality is always at most `qualified`.

Intersection scoring averages scored repeats per selected case, then performs a weighted macro
average using the one identical verified case weight and normalizes over selected weight. Category
and tag deltas use only selected verified cases. Each category records selected count plus its
baseline and candidate full-suite denominators, so `4/4 baseline, 4/5 candidate` cannot be read as
a full candidate-category comparison.

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
lists, coverage, available score delta, paired performance observations, and category deltas.
Complete case, category, tag, evidence, intersection, performance, and provenance records use
comparison schema 1 and policy version `1.2.0`:

```bash
elarabench compare runs/base runs/candidate --intent thinking --json
elarabench compare runs/base runs/candidate --output comparison.json
```

Full-suite text keeps one concise `Coverage` line. When intersection evidence exists, text labels
that same diagnostic as `Source coverage` and also prints authoritative `Intersection coverage`
from the stored selected-population evidence. If intersection coverage is insufficient, its
baseline/candidate minimums are printed as well, so `coverage_insufficient` cannot appear beside
only an unrelated whole-source ratio. JSON continues to expose both scopes unchanged.

Schema-1 artifacts written by policy `1.0.0` remain readable. Their category/tag breakdowns did
not record intersection population mode or baseline/candidate denominators, so those additive
fields deserialize as unknown (`null`) rather than receiving synthetic intersection semantics.
Policy-`1.1.0` output always populated them for emitted breakdowns. Policy-`1.2.0` keeps that
shape and adds optional typed performance analysis; artifacts from both earlier policies load
with `performance_analysis: null` rather than synthesized measurements.

With both flags, identical JSON is written and printed. Comparison fingerprints include ordered
source evidence hashes, directional benchmark identities, intent, policy version, selected case
population, fixture-aware verified/mismatched/one-sided case identities, expected repeats, common
weights, intersection coverage semantics, canonical field evidence, current evaluator
resolution/availability, evaluator provenance, performance semantic/aggregation versions,
directional performance evidence hashes, selected sample populations, metric missingness,
comparability, and observed summaries/deltas. Evaluator resolution records specification
hashes and resolved versions without exception text. Fingerprints exclude generation time, output
path, formatting, and unrelated wall timestamps; changing active duration or usage changes
identity, and reversing baseline and candidate changes identity. No automatic comparison
directory is created.

Exit status reflects **quality comparability only**: 0 means a strict or qualified quality
comparison was produced, 1 means quality is not directly comparable, and 2 means an
input/integrity/operational failure. Performance comparability is reported independently and does
not affect process exit status; for example, qualified quality plus not-directly-comparable
performance exits 0.

`higher`, `lower`, and `unchanged` are literal numeric directions, not significance claims.
ElaraBench reports no p-values, confidence intervals, causal fine-tune claims, or automatic
meaningfulness thresholds. Small suites and three-case categories require appropriately cautious
human interpretation.

Time-to-first-token instrumentation, streaming redesign, warmup orchestration, hardware/energy
sampling, p95/p99, confidence intervals, and significance testing remain future work.
