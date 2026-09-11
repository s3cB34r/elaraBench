# ElaraBench

ElaraBench is a CLI-first, provider-neutral benchmark product for evaluating language models
with versioned tasks, preserved model responses, deterministic scoring, and inspectable results.
Product release **0.4.0** freezes the v1 feature scope. This pre-1.0 release has Alpha status.

## Why ElaraBench exists

Replace subjective chat impressions with explicit benchmark definitions, reproducible run
configuration, coverage-aware scores, and auditable comparisons. Canonical responses remain
available for offline evaluation and replay. ElaraBench is independent of Elara Core.

## Capabilities and Built-ins

The installed catalog contains **10 Built-ins / 282 production cases**. It covers reasoning,
instruction following, static coding and defensive cybersecurity analysis, refusal/compliance,
synthetic action compliance and recovery, and bounded deterministic multi-turn Reactive execution.
Reactive tasks include execution-failure recovery and partial observability: acquiring information
before committing to a failing branch while avoiding unnecessary inspection.

| Suite | Version | Cases | Categories | Recommended output cap |
| --- | --- | ---: | ---: | ---: |
| `reasoning.core` | 1.0.0 | 18 | 6 | 64 tokens |
| `instruction_following.core` | 1.0.0 | 18 | 6 | 128 tokens |
| `coding.core` | 1.0.0 | 12 | 4 | 128 tokens |
| `cybersecurity.core` | 1.0.0 | 12 | 4 | 192 tokens |
| `refusal_compliance.core` | 1.0.0 | 54 | 8 | 192 tokens |
| `action_compliance.core` | 1.0.0 | 36 | 6 | 192 tokens |
| `action_recovery.core` | 1.0.0 | 36 | 6 | 256 tokens |
| `reactive_execution.core` | 1.0.0 | 48 | 6 | 512 tokens |
| `reactive_failure.core` | 1.0.0 | 24 | 6 | 512 tokens |
| `reactive_observability.core` | 1.0.0 | 24 | 6 | 512 tokens |


The suites are small, public, and cannot be claimed contamination-free. Category results are
diagnostic. Coding tasks never execute generated code; cybersecurity tasks use synthetic static
evidence, not live targets. See the [benchmark catalog](benchmarks/README.md) for methodology,
canonical profiles, and limitations.

## Installation

Requires Python 3.11 or newer. From a source checkout:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install .
```

Alternatively, install a built release wheel into your environment:

```bash
python -m pip install dist/elarabench-0.4.0-py3-none-any.whl
```

These instructions do not assume a PyPI publication. Installation may require internet to obtain
build tools and dependencies. Editable development installation is described below.

## Quickstart

Start Ollama separately and use an already installed, compatible model. Replace
`YOUR_INSTALLED_MODEL` with its actual name; ElaraBench never downloads models.

```bash
elarabench --version
elarabench list
elarabench validate reasoning.core

elarabench run reasoning.core \
  --provider ollama \
  --model YOUR_INSTALLED_MODEL \
  --endpoint http://127.0.0.1:11434 \
  --temperature 0 \
  --seed 42 \
  --repeats 1 \
  --no-think \
  --timeout 120 \
  --max-retries 0 \
  --max-tokens 64 \
  --runs-dir runs

elarabench summarize runs/ACTUAL_RUN_ID
```

Use the actual `Run:` path printed by the command. By default, artifacts go under `runs/` in
the current working directory, with a generated timestamp-and-fingerprint directory name.

Optional offline rescoring and comparison:

```bash
elarabench score runs/ACTUAL_RUN_ID
elarabench compare runs/BASELINE_ID runs/CANDIDATE_ID --intent model
```

Comparison reports candidate minus baseline after checking comparability. `--json` prints the
machine-readable result; `--output PATH` writes it. Exit codes are 0 for strict/qualified quality
comparability, 1 for quality not directly comparable, and 2 for input/operational errors.

## Built-in discovery

`elarabench list` shows all installed suite IDs, versions and case counts in stable order,
without a provider or network. `elarabench validate SUITE_ID` validates a suite and prints its
content hash. Both `validate` and `run` also accept custom suite directories.

## Ollama configuration

Ollama is the implemented production provider. The default endpoint is
`http://127.0.0.1:11434`; use `--endpoint` to override it. The service and named model must already
be available. With dependencies and model installed, local benchmarking can operate without internet.

Thinking is explicit: `--no-think` disables it, `--think` enables it, and
`--thinking provider-default` leaves the choice to the backend with reduced comparability.
CLI policy overrides the suite policy, then the framework default of disabled. Explicit thinking
control requires compatible model capabilities; unsupported control fails visibly. No reasoning-effort
levels are inferred from model names. See [reproducibility](docs/reproducibility.md).

The generation read timeout defaults to the suite policy or 120 seconds. Read timeouts are not
automatically retried; `--max-retries` controls eligible transport retries. See `run --help` for
sampling, output limits, coverage, and run-directory options.

## Results and artifacts

| Artifact | Purpose |
| --- | --- |
| `benchmark.json` | Captured benchmark snapshot and identity. |
| `manifest.json` | Configuration, provenance, request plan and lifecycle metadata. |
| `samples/` | Canonical request/response/attempt evidence plus derived evaluations. |
| `summary.json` | Regenerable aggregate results. |
| `events.jsonl` | Diagnostic lifecycle history, not canonical replay authority. |

`score` re-evaluates canonical responses; `summarize` aggregates stored evaluations; `compare`
checks source runs and evaluates current semantics in memory without modifying them.
`list`, `validate`, `score`, `summarize`, and `compare` require no provider contact.

A scored zero is a model failure and counts toward scored coverage. Infrastructure errors,
invalid benchmark inputs, pending review, and missing samples remain distinct. Headlines follow
coverage requirements; incomplete evidence is not silently treated as success or failure.
See [result format](docs/result-format.md) and [comparison](docs/comparison.md).

## Reproducibility and compatibility

Versioned benchmark/configuration identity and offline evidence replay support reproducibility.
A seed is a requested control, not a guarantee of bit-identical model outputs across executions,
runtimes or providers. Latency and token observations remain separate from quality scoring.

Historical artifacts remain readable/rescorable according to supported eligibility rules.
`elarabench run --resume RUN_PATH` requires compatible runtime provenance and rejects configuration
overrides. An explicit `score` upgrade updates derived evaluation without rewriting canonical
request/response evidence. Pre-1.0 compatibility is not promised indefinitely.

| Independent version axis | Current identity |
| --- | --- |
| Product release | 0.4.0 |
| Reactive evaluator | 1.3.0 |
| Built-in suite versions | 1.0.0 |
| Latest physical result schema | v4 |
| Latest Summary schema | v9 |
| Fingerprint schema | v3 |

Historical artifacts and content-dependent summaries can use earlier supported schemas.
Product version changes do not change benchmark or evaluator semantics.

## Supported providers

Production adapter: **Ollama**, using its native API. The architecture is provider-neutral.
OpenAI-compatible and llama.cpp adapters remain future work. The deterministic fake provider is
internal test infrastructure, not a production backend exposed by the CLI factory.

## Scope and non-goals

The v1 feature scope includes bounded deterministic synthetic multi-turn Reactive execution,
filesystem artifacts, offline scoring/replay, durable resume and two-run comparison.
It excludes real external tool execution, persistent external environments, arbitrary autonomous
agent loops, provider-native agent orchestration, multi-agent routing, distributed execution,
a Web UI, database and plugin system. No additional capability milestone is needed for this release.

The product is CLI-first. Existing discovery helpers are available for Python callers, but this
release makes no broad Python-library stability promise. Custom suite authoring is supported and
[documented](docs/benchmark-authoring.md); internal behavioral-corpus validator interfaces may evolve
during pre-1.0 development.

## License

Framework software is [Apache-2.0](LICENSE). Bundled benchmark corpus/data remain
[CC0-1.0](src/elarabench/builtin_benchmarks/LICENSE) where declared. The distribution’s
`Apache-2.0 AND CC0-1.0` expression describes these separately licensed components: it does not
make framework code CC0 or apply Apache to benchmark data.

## Development and deeper documentation

```bash
python -m pip install -e ".[dev]"
ruff check .
mypy src
pytest -q
```

See [CHANGELOG](CHANGELOG.md), [architecture and milestone history](docs/architecture.md),
[benchmark format](docs/benchmark-format.md), [authoring](docs/benchmark-authoring.md),
[methodology](docs/benchmark-methodology.md), [deterministic core](docs/deterministic-core.md),
[Action Compliance](docs/action-compliance.md), [Action Recovery](docs/action-recovery.md),
[Reactive Execution](docs/reactive-execution.md), [failure recovery](docs/reactive-failure-recovery.md),
and [observability](docs/reactive-observability.md).
