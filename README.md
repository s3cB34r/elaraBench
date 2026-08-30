# ElaraBench

ElaraBench is a standalone, provider-neutral framework for reproducible evaluation of local
and remote large language models. It exists to replace subjective chat impressions with
versioned benchmark definitions, preserved model outputs, objective scoring where possible,
and explicit reproducibility metadata.

ElaraBench is an independent project. It does not import, embed, or depend on Elara Core.
That boundary allows benchmark results and methodology to be used and audited without an
Elara deployment.

## Architectural principles

ElaraBench is designed around a simple data flow:

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

- Raw model responses are canonical; scores and summaries are derived and repeatable.
- Benchmark definitions are versioned and contain no provider-specific behavior.
- Provider adapters perform inference translation and contain no evaluation logic.
- Deterministic evaluation is preferred whenever it is possible.
- Human and LLM-assisted judgments remain explicit and separate from deterministic scores.
- Reproducibility metadata is a first-class output of every future run.
- ElaraBench v1 will use filesystem artifacts, not a database or general plugin framework.
- Executable benchmark evaluation will require sandbox isolation when it is introduced.

See [Architecture](docs/architecture.md), [Deterministic core](docs/deterministic-core.md),
[Benchmark format](docs/benchmark-format.md), [Benchmark methodology](docs/benchmark-methodology.md),
[Benchmark authoring](docs/benchmark-authoring.md), [Result format](docs/result-format.md),
[Reproducibility](docs/reproducibility.md), [Comparison](docs/comparison.md), and the
[M5.2 Static Action Compliance design](docs/action-compliance.md) for implemented contracts,
planned capability constraints, and v1 boundaries.

## First-party benchmarks

ElaraBench includes five original, public, deterministic suites:

| Suite | Version | Cases | Categories | Recommended output cap |
| --- | --- | ---: | ---: | ---: |
| `reasoning.core` | 1.0.0 | 18 | 6 | 64 tokens |
| `instruction_following.core` | 1.0.0 | 18 | 6 | 128 tokens |
| `coding.core` | 1.0.0 | 12 | 4 | 128 tokens |
| `cybersecurity.core` | 1.0.0 | 12 | 4 | 192 tokens |
| `refusal_compliance.core` | 1.0.0 | 54 | 8 | 192 tokens |

All five use equal case weights, Thinking disabled as the canonical suite policy, a 120-second
timeout, and self-contained prompts with no fixtures or network requirements. The benchmark data
are dedicated under CC0-1.0 separately from the Python framework. `coding.core` measures static
code analysis and never executes model-generated code. `cybersecurity.core` uses synthetic,
defensive static evidence and performs no scanning, exploitation, or live-system interaction.
Interactive agentic tool-use, autonomous agent loops, real or sandboxed tool execution, and
multi-turn action execution benchmarks remain later work. ElaraBench may instead evaluate static
action-plan compliance from a single stored provider response, using provider-neutral structured
plans, externally defined authorization state, and deterministic simulation only.

`refusal_compliance.core` is a static, no-judge behavioral suite: 42 deterministic completion
cases and 12 refusal controls grounded in rules stated directly in each prompt. Its 54 synthetic
CC0-1.0 cases include eight neutral/sensitive/authorized contrastive triplets and 30 observable
policy-trigger probes. It contains no live targets, current facts, tool calls, or executable
payloads. Tool/action refusal recovery is later work beyond M5.2.

Its category distribution is benign technical 6, developer/sysadmin 6, defensive cybersecurity
8, authorized security analysis 8, dual-use benign 6, sensitive wording 4, benign transformation
4, and refusal control 12. Refusal controls cover authorization, privacy/secrecy, audit integrity,
and prohibited destructive changes using narrow reason codes and explicitly configured redirects.

The suites are bundled in wheels and may be addressed by stable suite ID from any working
directory. Run them with the canonical local profile:

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
```

Validation uses the same IDs:

```bash
elarabench validate reasoning.core
elarabench validate instruction_following.core
elarabench validate coding.core
elarabench validate cybersecurity.core
elarabench validate refusal_compliance.core
```

Library callers can obtain the installed filesystem location without depending on the current
working directory:

```python
from elarabench import get_builtin_suite_path

reasoning_path = get_builtin_suite_path("reasoning.core")
```

The suites score final answers rather than reasoning traces. They are deliberately small and
public: category scores are diagnostic, and the corpus cannot be claimed contamination-free. See
the [benchmark catalog](benchmarks/README.md) for interpretation and licensing.

Local models are the initial priority. Planned backends include Ollama, llama.cpp and
llama-server, OpenAI-compatible APIs, and later Unsloth evaluation or fine-tuning workflows.

## Development installation

ElaraBench requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run the current CLI:

```bash
elarabench --help
elarabench --version
elarabench validate tests/fixtures/tiny_suite
elarabench run reasoning.core --provider ollama --model gemma3
elarabench score runs/<run-id>
elarabench summarize runs/<run-id>
elarabench compare runs/<baseline-id> runs/<candidate-id> --intent model
```

`run` uses Ollama's native API and defaults to `http://127.0.0.1:11434`. The service and named
model must already be available; ElaraBench never downloads models. `score` re-evaluates stored
responses, while `summarize` only aggregates stored evaluations. Neither command contacts a
provider.

`compare` validates both source runs read-only and applies current evaluators symmetrically in
memory before reporting `candidate - baseline`. It separates strict, qualified, and
not-directly-comparable quality evidence from performance comparability. Full-suite deltas require
identical benchmark content and complete scored populations. Different versions of the same suite
may instead produce a clearly labeled, snapshot-fixture-verified case-intersection delta; different
suite IDs and same-version identity conflicts never intersect. `--json` and `--output PATH` expose
the versioned machine-readable result. Compare exit status reflects quality only: 0 for
strict/qualified quality, 1 for quality that is not directly comparable, and 2 for input or
operational failure. Performance comparability remains independently reported. See
[Same-benchmark comparison](docs/comparison.md).

Policy `1.2.0` also reports observed paired performance medians from immutable attempts and
responses: generated tokens, explicit client/provider durations, generation throughput, and
attempt/retry active cost. Each metric carries its own availability and comparability; unknown
warm state keeps observations qualified, and CLI exit status remains quality-based.

## Runtime policy

ElaraBench v0.2.1 treats model reasoning/thinking as explicit inference configuration:

```bash
# Default: explicitly disable thinking
elarabench run path/to/suite --provider ollama --model qwen3.5:9b

# Deliberately enable thinking for a reasoning-oriented comparison
elarabench run path/to/suite --provider ollama --model qwen3.5:9b --think

# Preserve the backend/model default, with reduced comparability made explicit
elarabench run path/to/suite --provider ollama --model qwen3.5:9b \
  --thinking provider-default
```

The three policies are `enabled`, `disabled`, and `provider_default`. CLI policy overrides a
suite default; a suite default overrides ElaraBench's `disabled` default. Provider defaults are
less reproducible because behavior may change with the model or backend without a benchmark-file
change. Local reasoning defaults can also consume substantial hidden work, so initial
deterministic and instruction-following benchmarks do not enable them implicitly.

Ollama enforcement is model-capability dependent and never inferred from model aliases. Its broad
`thinking` capability does not prove boolean control. ElaraBench classifies control as none,
boolean, level-valued, or unknown using a small exact provider-architecture compatibility rule.
Only confirmed boolean control receives top-level `think: true` or `think: false`. A model whose
valid capability list does not advertise thinking satisfies `disabled` without sending an
unnecessary field, but rejects `enabled`. Unknown or level-valued control rejects explicit
policies; `provider_default` remains available and omits the field. ElaraBench v0.2.1 deliberately
does not model reasoning-effort levels.

The default generation timeout is finite at 120 seconds and remains overrideable with
`--timeout`. ElaraBench records client wall latency separately from provider-reported total,
model-load, prompt-evaluation, and token-generation durations. Load time remains part of canonical
latency; M2.1 performs no silent warmup. A generation read timeout is a final sample outcome and
is not automatically retried merely because the model was slow.

## Evaluation semantics

`scored` means the evaluator had trustworthy benchmark inputs and could determine an outcome;
it includes both correct answers and model failures with score `0.0`. Wrong answers, strict
numeric-format failures, malformed or Markdown-fenced JSON, invalid model choices, regex
mismatches, missing required content, and forbidden content are ordinary scored failures.
Deterministic evaluators do not extract, repair, or silently normalize output beyond operations
declared by the benchmark.

`invalid` is reserved for an unusable benchmark or evaluator specification, such as invalid
ground truth, regex, or JSON Schema. `error` represents a provider failure or unexpected technical
evaluation failure. `pending_review` remains unscored. Consequently, a valid zero-score model
failure counts toward scored coverage, while invalid, error, pending, and missing samples do not.
This prevents malformed model output from disappearing from the score denominator.

Run the development checks:

```bash
ruff check .
mypy src
python -m pytest
```

## Project status

ElaraBench v0.2.1 plus M3, M4.1, M4.2a, and M5.1 includes local model execution, trustworthy
same-benchmark and verified cross-version intersection comparison, and five first-party benchmark
suites totaling 114 cases. The engine provides the deterministic core, a synchronous concurrency-one
runner, complete schema-v3 run artifacts, bounded retries, durable attempt history, Ctrl-C
recovery, strict resume, environment discovery, coverage-aware summaries, offline rescoring, and
a native Ollama provider. Historical M2 schema-v2 runs remain available to offline `score` and
`summarize`, but cannot resume under v3 runtime semantics. A deterministic fake provider keeps the
complete runner and first-party suite paths testable without network or model hardware.

M3 coding and cybersecurity coverage is intentionally static. It does not include executable
benchmark sandboxes, arbitrary model-generated execution, live security targets, parallel or
distributed execution, OpenAI-compatible or llama.cpp-specific providers, model downloading,
LLM judges, a database, or a web interface.

## ElaraBench v1 scope

The v1 roadmap covers validated and versioned benchmark data, reproducible local-first runs,
immutable filesystem artifacts, deterministic evaluators, Ollama and OpenAI-compatible model
access, resumable execution, and auditable result comparison. It explicitly excludes a
database, web UI, generalized plugin system, distributed execution, interactive agentic tool-use
evaluation, real tool execution, autonomous agent loops, multi-turn action execution, and
production tool or sandbox behavior. Static action-plan compliance evaluation remains in scope
when it is offline and read-only: it evaluates a single stored provider response per sample against
externally defined authorization/gating semantics through deterministic, pure, side-effect-free
simulation. It executes no tools and introduces no agent loop.
