# ElaraBench first-party benchmarks

ElaraBench's versioned first-party data are canonically stored as installable package resources
under [`src/elarabench/builtin_benchmarks`](../src/elarabench/builtin_benchmarks). The corpus is
distributed under [CC0 1.0 Universal](../src/elarabench/builtin_benchmarks/LICENSE). That
dedication applies to the benchmark corpus. It does not determine or imply the license of the
ElaraBench Python framework or package, which is a separate work.

## Available suites

| Suite | Version | Cases | Purpose | Canonical thinking | Recommended output cap |
| --- | --- | ---: | --- | --- | ---: |
| `reasoning.core` | 1.0.0 | 18 | Arithmetic, quantitative, logical, constraint, probability, and symbolic reasoning | disabled | 64 |
| `instruction_following.core` | 1.0.0 | 18 | Exact format, content, transformation, ordering, JSON, and combined constraints | disabled | 128 |
| `coding.core` | 1.0.0 | 12 | Static comprehension, debugging, algorithm analysis, and patch selection | disabled | 128 |
| `cybersecurity.core` | 1.0.0 | 12 | Synthetic defensive log, code, configuration, and incident analysis | disabled | 192 |
| `refusal_compliance.core` | 1.0.0 | 54 | Deterministic benign compliance and explicit-rule refusal controls | disabled | 192 |
| `action_compliance.core` | 1.0.0 | 36 | Static action/control proposals under trusted authorization | disabled | 192 |
| `action_recovery.core` | 1.0.0 | 36 | Observation-conditioned recovery and bounded terminal stopping | disabled | 256 |
| `reactive_execution.core` | 1.0.0 | 48 | Causal synthetic execution, adaptation, and terminal stopping across six categories | disabled | 512 |
| `reactive_failure.core` | 1.0.0 | 24 | Retryable failure recovery and terminal failure stopping across six categories | disabled | 512 |

All nine suites (258 production cases) are fully self-contained, deterministic, offline, and
weight every case equally.
They score answers and synthetic behavioral evidence rather than reasoning traces. Their public
and relatively small category
samples are useful diagnostics, not proof of contamination-free capability or statistically
precise rankings.

The [M5.5 Reactive Execution Failure Recovery design](../docs/reactive-failure-recovery.md) is
**IMPLEMENTED**. Its `reactive_failure.core` v1.0.0 contributes 24 cases to the available catalog.

[M5.6 Partially Observable Reactive Execution](../docs/reactive-observability.md) is
**DESIGNED / RATIFIED — NOT YET IMPLEMENTED**. Planned `reactive_observability.core` v1.0.0 has
24 cases: 12 `information_required` in six pairs and 12 ungrouped `information_sufficient`.
Ten Built-ins / 282 cases are planned totals after implementation; the current available catalog
remains nine Built-ins / 258 cases. The future suite and tenth hash are not yet registered.

Canonical local runs use temperature 0, seed 42 when supported, one repeat, Thinking disabled, a
120-second timeout, no retries, concurrency one, and leave top-p, top-k, and stop unset:

```bash
elarabench run reasoning.core \
  --provider ollama --model MODEL --temperature 0 --seed 42 \
  --repeats 1 --no-think --timeout 120 --max-retries 0 --max-tokens 64

elarabench run instruction_following.core \
  --provider ollama --model MODEL --temperature 0 --seed 42 \
  --repeats 1 --no-think --timeout 120 --max-retries 0 --max-tokens 128

elarabench run coding.core \
  --provider ollama --model MODEL --temperature 0 --seed 42 \
  --repeats 1 --no-think --timeout 120 --max-retries 0 --max-tokens 128

elarabench run cybersecurity.core \
  --provider ollama --model MODEL --temperature 0 --seed 42 \
  --repeats 1 --no-think --timeout 120 --max-retries 0 --max-tokens 192

elarabench run refusal_compliance.core \
  --provider ollama --model MODEL --temperature 0 --seed 42 \
  --repeats 1 --no-think --timeout 120 --max-retries 0 --max-tokens 192

elarabench run action_compliance.core \
  --provider ollama --model MODEL --temperature 0 --seed 42 \
  --repeats 1 --no-think --timeout 120 --max-retries 0 --max-tokens 192

elarabench run action_recovery.core \
  --provider ollama --model MODEL --temperature 0 --seed 42 \
  --repeats 1 --no-think --timeout 120 --max-retries 0 --max-tokens 256

elarabench run reactive_execution.core \
  --provider ollama --model MODEL --temperature 0 --seed 42 \
  --repeats 1 --no-think --timeout 120 --max-retries 0 --max-tokens 512

elarabench validate refusal_compliance.core
```

The settings recorded in a run manifest are authoritative. A seed is a requested control, not a
guarantee of bitwise-identical inference. A deliberate `--think` run uses the same cases but is a
different reproducibility profile.

Installed callers may resolve the physical resource path with
`elarabench.get_builtin_suite_path("reasoning.core")`. Repository-relative package-data paths are
for corpus development only; user commands should use suite IDs so they work after wheel
installation.

`coding.core` is a static code-analysis benchmark, not an unrestricted implementation benchmark:
it never executes generated code or applies patches. `cybersecurity.core` is a static defensive
analysis benchmark built from synthetic inputs; it has no live targets, operational exploitation,
credential use, scanning, or malware execution. Execution-based coding and cyber evaluation is
deferred until ElaraBench has a trusted sandbox boundary.
See [Benchmark methodology](../docs/benchmark-methodology.md) and
[Benchmark authoring](../docs/benchmark-authoring.md).
