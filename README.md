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
[Benchmark authoring](docs/benchmark-authoring.md), [Result format](docs/result-format.md), and
[Reproducibility](docs/reproducibility.md) for the implemented contracts and v1 boundaries.

## First-party benchmarks

M3 includes four original, public, deterministic suites:

| Suite | Version | Cases | Categories | Recommended output cap |
| --- | --- | ---: | ---: | ---: |
| `reasoning.core` | 1.0.0 | 18 | 6 | 64 tokens |
| `instruction_following.core` | 1.0.0 | 18 | 6 | 128 tokens |
| `coding.core` | 1.0.0 | 12 | 4 | 128 tokens |
| `cybersecurity.core` | 1.0.0 | 12 | 4 | 192 tokens |

All four use equal case weights, Thinking disabled as the canonical suite policy, a 120-second
timeout, and self-contained prompts with no fixtures or network requirements. The benchmark data
are dedicated under CC0-1.0 separately from the Python framework. `coding.core` measures static
code analysis and never executes model-generated code. `cybersecurity.core` uses synthetic,
defensive static evidence and performs no scanning, exploitation, or live-system interaction.
Agentic, tool-use, and sandboxed execution benchmarks remain later work.

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
```

Validation uses the same IDs:

```bash
elarabench validate reasoning.core
elarabench validate instruction_following.core
elarabench validate coding.core
elarabench validate cybersecurity.core
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
```

`run` uses Ollama's native API and defaults to `http://127.0.0.1:11434`. The service and named
model must already be available; ElaraBench never downloads models. `score` re-evaluates stored
responses, while `summarize` only aggregates stored evaluations. Neither command contacts a
provider.

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

ElaraBench v0.2.1 plus M3 includes local model execution and four real first-party benchmark
suites totaling 60 cases. The engine provides the deterministic core, a synchronous concurrency-one
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
database, web UI, generalized plugin system, distributed execution, and agentic tool-use
evaluation.
