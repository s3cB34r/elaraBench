# First-party benchmark methodology

## Purpose

ElaraBench first-party suites measure useful model differences with versioned, auditable tasks
whose outputs can be scored deterministically. They complement preserved raw responses and
runtime metadata; they do not replace qualitative analysis or prove general intelligence.

M3 suites prefer original synthetic tasks, objective ground truth, self-contained prompts, and
offline evaluation. Cases do not depend on current facts, network access, hidden context, or an
LLM judge. Final answers are scored. Models are never asked to expose chain-of-thought, and a
reasoning trace is neither required nor rewarded.

## Strictness and brittleness

Strict formatting should determine score only when formatting itself is part of the tested
capability. Exact tokens, raw JSON, fixed line counts, and forbidden content are legitimate
instruction-following requirements. A model that violates such an explicit contract receives a
normal scored failure.

Reasoning tasks should not fail because of irrelevant presentation differences. They therefore
favor numeric, multiple-choice, normalized-text, or structural JSON evaluation. Exact matching is
used only when the requested representation has semantic meaning. Evaluators do not repair model
responses, extract answers from prose, or strip Markdown fences from strict JSON.

## Scores, balance, and coverage

Every first-party core case has weight 1.0. Categories contain equal case counts, so no category
dominates merely because it has more cases. Composite evaluator components divide credit within
one case; they do not create additional globally weighted cases.

ElaraBench first averages scored repeats per case and then computes the weighted macro-average of
scored cases. A trustworthy but wrong output has score zero and counts toward scored coverage.
Provider failures, evaluator errors, invalid benchmark configurations, pending review, and
missing samples do not become zero. The core suites require 95% scored coverage for a headline
score and still expose a partial score and status counts below that threshold.

Action Compliance applies a stricter normative headline gate: all three trusted authorization
populations must be non-empty and completely covered, and their state-specific compliance rates
are balanced equally. Its composition-dependent global pass rate is diagnostic only. A suite that
mixes Action Compliance with another scored evaluator family has no generic headline or partial
score, and sufficient generic coverage does not override either rule.

Category and tag breakdowns are diagnostics. Each M3 category has only three cases, so its score
is a coarse signal rather than a statistically precise estimate. ElaraBench does not manufacture
confidence intervals from these small samples.

## Difficulty

First-party M3 cases use `easy`, `medium`, or `hard`. Difficulty describes intended capability
demand, not the score of one model. Hard cases require more steps or interacting constraints, not
trick wording or ambiguity. A matching `difficulty-*` tag makes difficulty visible through the
current tag aggregation.

Empirical calibration may later be published as derived analysis. Released labels are not
silently retuned to model results. A semantic recalibration requires a major suite release.

## Versioning and identity

Suites use semantic versions independently:

- PATCH is limited to non-semantic documentation or metadata corrections that do not change task
  execution, scoring, or identity meaning.
- MINOR may add cases or reporting metadata without changing existing case semantics.
- MAJOR covers prompt, expected-answer, evaluator, scoring, material weight, category-meaning, or
  difficulty-recalibration changes, as well as removal or replacement of released cases.

Every benchmark-defining content change changes the canonical content hash regardless of semantic
version level. Adding cases therefore changes both the minor version and hash. Scores from
different suite versions are not presented as the same corpus. Released case IDs are never reused
for another task, and run snapshots preserve the exact suite used.

Each suite is an independent measurement. M3 defines no cross-suite grand score.

## Reproducibility and Thinking

The primary M3 profile uses temperature 0, seed 42 when supported, one repeat, Thinking disabled,
a 120-second timeout, no retries, and concurrency one. Top-p, top-k, and stop remain unset. The
recommended output caps are 64 tokens for `reasoning.core`, 128 for
`instruction_following.core` and `coding.core`, and 192 for `cybersecurity.core` and
`refusal_compliance.core`. Output caps are
run-level recommendations because the current suite schema does not contain general generation
parameters.

The manifest and request artifacts, not the recommendation, record what actually ran. A seed does
not guarantee identical GPU inference. Thinking-enabled evaluation deliberately reuses the same
cases with `--think`; it is a separate fingerprinted runtime profile. No case requires visible
reasoning. `provider_default` is less strictly reproducible because provider or model defaults may
change independently of benchmark files.

## Provenance and contamination

M3 cases are original ElaraBench first-party synthetic tasks released publicly under CC0-1.0.
Their wording, values, and structures were created for ElaraBench rather than copied from public
benchmark corpora or online puzzles. Drafting used LLM assistance under manual direction; every
expected answer and evaluator outcome is independently checked by deterministic calculation,
enumeration, review, and golden tests. LLM-assisted drafting is not validation.

Original wording reduces direct memorization risk at initial creation but cannot establish that a
model has never seen related concepts. After public release, cases may enter future training data.
ElaraBench therefore describes these as public first-party benchmarks, not contamination-free
tests. Creation context, provenance, semantic version, content hash, and Git history make later
auditing possible.

## Public-corpus limitations

The first release prioritizes reviewability over size. It does not measure every reasoning or
instruction-following skill, and performance can be affected by model templates, tokenization,
runtime policies, and constrained output ability. Results should be compared only with compatible
suite hashes and documented run settings. Broader conclusions require additional suites and model
evidence.

## Static coding and cybersecurity scope

`coding.core` v1 measures static code comprehension, debugging, algorithm analysis, and selection
among supplied patches. It does not measure unrestricted program synthesis or repository-level
software engineering. Model output is never run, compiled, interpreted, applied as a patch, or
passed to a shell.

`cybersecurity.core` v1 measures defensive analysis of synthetic logs, supplied source code,
configuration fragments, and incident facts. It does not measure operational penetration testing
and contains no live target, scanning, exploit execution, credential use, persistence, evasion, or
malware execution. Any organizational response rule needed for an objective answer appears in the
prompt.

Execution-based coding and cybersecurity scoring is deliberately deferred until a trusted sandbox
can provide resource limits, filesystem isolation, and an explicit network policy. Merely loading a
benchmark or evaluating a response never grants execution authority.
