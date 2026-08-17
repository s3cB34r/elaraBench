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
[Benchmark format](docs/benchmark-format.md), [Result format](docs/result-format.md), and
[Reproducibility](docs/reproducibility.md) for the implemented M2 contracts and planned v1
boundaries.

## Planned benchmark categories

- Coding
- Cybersecurity
- Reasoning
- Instruction following
- Agentic and tool-use capabilities in a later milestone

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
elarabench run path/to/suite --provider ollama --model gemma3
elarabench score runs/<run-id>
elarabench summarize runs/<run-id>
```

`run` uses Ollama's native API and defaults to `http://127.0.0.1:11434`. The service and named
model must already be available; ElaraBench never downloads models. `score` re-evaluates stored
responses, while `summarize` only aggregates stored evaluations. Neither command contacts a
provider.

Run the development checks:

```bash
ruff check .
mypy src
python -m pytest
```

## Project status

ElaraBench is unreleased and currently at M2: local model execution. It implements the M1
deterministic core plus a synchronous concurrency-one runner, complete schema-v2 run artifacts,
bounded retries, durable attempt history, Ctrl-C recovery, strict resume, environment discovery,
coverage-aware summaries, offline rescoring, and a native Ollama provider. A deterministic fake
provider keeps the complete runner path testable without network or model hardware.

M2 does not include parallel or distributed execution, OpenAI-compatible or llama.cpp-specific
providers, model downloading, executable benchmark sandboxes, LLM judges, a database, or a web
interface.

## ElaraBench v1 scope

The v1 roadmap covers validated and versioned benchmark data, reproducible local-first runs,
immutable filesystem artifacts, deterministic evaluators, Ollama and OpenAI-compatible model
access, resumable execution, and auditable result comparison. It explicitly excludes a
database, web UI, generalized plugin system, distributed execution, and agentic tool-use
evaluation.
