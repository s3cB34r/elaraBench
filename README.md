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

See [Architecture](docs/architecture.md), [Benchmark format](docs/benchmark-format.md),
[Result format](docs/result-format.md), and [Reproducibility](docs/reproducibility.md) for the
approved v1 specifications.

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
```

Run the development checks:

```bash
ruff check .
mypy src
python -m pytest
```

## Project status

ElaraBench is unreleased and currently at M0: specification and project foundation. The CLI
exposes only help and version information. Benchmark loading, model providers, execution,
evaluation, and reporting have intentionally not been implemented yet.

## ElaraBench v1 scope

The v1 roadmap covers validated and versioned benchmark data, reproducible local-first runs,
immutable filesystem artifacts, deterministic evaluators, Ollama and OpenAI-compatible model
access, resumable execution, and auditable result comparison. It explicitly excludes a
database, web UI, generalized plugin system, distributed execution, and agentic tool-use
evaluation.
