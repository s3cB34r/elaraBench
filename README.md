<p align="center">
  <img src="assets/elarabench-logo.png" alt="ElaraBench Logo" width="420">
</p>

<h1 align="center">ElaraBench</h1>

<p align="center">
  <strong>Reproducible evaluation for local AI models.</strong>
</p>

<p align="center">
  <a href="https://github.com/s3cB34r/elaraBench/releases">
    <img src="https://img.shields.io/github/v/tag/s3cB34r/elaraBench?label=Release&logo=github" alt="Release">
  </a>
  <a href="https://github.com/s3cB34r/elaraBench/actions/workflows/ci.yml">
    <img src="https://github.com/s3cB34r/elaraBench/actions/workflows/ci.yml/badge.svg" alt="CI">
  </a>
  <img src="https://img.shields.io/badge/Python-3.11%20%7C%203.12-3776AB?logo=python&logoColor=white" alt="Python">
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-Apache--2.0-D22128?logo=apache&logoColor=white" alt="Apache 2.0">
  </a>
  <img src="https://img.shields.io/badge/Provider-Ollama-black?logo=ollama&logoColor=white" alt="Ollama">
</p>

<p align="center">
  CLI-first, provider-neutral benchmarking with deterministic scoring,<br>
  reproducible artifacts, compliance evaluation and inspectable results.
</p>

---

## 🧪 Why ElaraBench?

ElaraBench replaces subjective chat impressions with explicit benchmark definitions, reproducible run configurations, coverage-aware scoring and auditable comparisons.

Canonical responses remain available for offline evaluation and replay.

**v0.4.0** establishes the first public baseline with:

* **10 built-in benchmark suites**
* **282 production benchmark cases**
* deterministic offline scoring
* preserved request/response evidence
* resume and fingerprint safeguards
* model-run comparison
* compliance and refusal evaluation
* bounded synthetic action execution
* Reactive multi-turn evaluation

---

## ✨ Benchmark Suites

| Suite                         | Cases | Focus                       |
| ----------------------------- | ----: | --------------------------- |
| `reasoning.core`              |    18 | 🧠 Reasoning                |
| `instruction_following.core`  |    18 | 📋 Instruction following    |
| `coding.core`                 |    12 | 💻 Static coding            |
| `cybersecurity.core`          |    12 | 🛡️ Defensive cybersecurity |
| `refusal_compliance.core`     |    54 | 🚦 Refusal & compliance     |
| `action_compliance.core`      |    36 | 🛠️ Action compliance       |
| `action_recovery.core`        |    36 | 🔄 Action recovery          |
| `reactive_execution.core`     |    48 | ⚙️ Reactive execution       |
| `reactive_failure.core`       |    24 | 🧯 Failure recovery         |
| `reactive_observability.core` |    24 | 🔍 Partial observability    |

**Total: 282 production benchmark cases**

See the [benchmark catalog](benchmarks/README.md) for methodology, suite versions and limitations.

---

## 🚀 Quick Start

### 1. Clone

```bash
git clone https://github.com/s3cB34r/elaraBench.git
cd elaraBench
```

### 2. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install

```bash
python -m pip install -e .
```

For development:

```bash
python -m pip install -e ".[dev]"
```

### 4. Explore

```bash
elarabench --version
elarabench list
elarabench validate reasoning.core
```

---

## 🦙 Run a Local Model with Ollama

Ollama is the production inference provider implemented in **v0.4.0**.

ElaraBench does not download models automatically. Start Ollama separately and use a model you already have installed.

```bash
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
```

Then summarize the run:

```bash
elarabench summarize runs/ACTUAL_RUN_ID
```

Or rescore it offline:

```bash
elarabench score runs/ACTUAL_RUN_ID
```

Compare two runs:

```bash
elarabench compare \
  runs/BASELINE_ID \
  runs/CANDIDATE_ID \
  --intent model
```

---

## 🔬 Evaluation Capabilities

### 🧠 Reasoning

Bounded reasoning tasks with deterministic scoring.

### 📋 Instruction Following

Tests adherence to explicit task requirements and output constraints.

### 💻 Coding

Static coding evaluation without executing generated code.

### 🛡️ Cybersecurity

Synthetic defensive-security analysis without live targets.

### 🚦 Refusal Compliance

Evaluates whether models comply with or refuse requests according to explicit benchmark criteria.

### 🛠️ Action Compliance

Tests structured model-produced actions inside deterministic synthetic environments.

### 🔄 Action Recovery

Measures recovery behavior when actions cannot be completed as expected.

### ⚙️ Reactive Execution

Bounded multi-turn scenarios with persistent state and evidence.

### 🧯 Failure Recovery

Tests behavior after synthetic execution failures.

### 🔍 Partial Observability

Evaluates whether models acquire necessary information before acting without unnecessary inspection.

---

## 📦 Results & Artifacts

Every benchmark run produces inspectable artifacts:

| Artifact         | Purpose                                       |
| ---------------- | --------------------------------------------- |
| `benchmark.json` | Benchmark snapshot and identity               |
| `manifest.json`  | Configuration and provenance                  |
| `samples/`       | Requests, responses, attempts and evaluations |
| `summary.json`   | Aggregate results                             |
| `events.jsonl`   | Diagnostic lifecycle history                  |

Offline-capable commands:

```text
list
validate
score
summarize
compare
```

---

## 🔁 Reproducibility

ElaraBench records enough execution context to support reproducible evaluation without pretending that model generation itself is always bit-identical.

Key mechanisms include:

* benchmark content hashes
* versioned benchmark identity
* preserved canonical responses
* run fingerprints
* execution provenance
* durable attempt evidence
* compatible resume validation
* offline rescoring
* historical artifact handling
* two-run comparison

> A deterministic benchmark configuration does not guarantee identical model output across runtimes, hardware or providers.

---

## 📐 Versioning

| Version axis           | Current   |
| ---------------------- | --------- |
| ElaraBench             | **0.4.0** |
| Reactive evaluator     | **1.3.0** |
| Built-in suites        | **1.0.0** |
| Physical result schema | **v4**    |
| Summary schema         | **v9**    |
| Fingerprint schema     | **v3**    |

These version axes are intentionally independent.

---

## ✅ Quality

ElaraBench v0.4.0 was release-verified with:

* ✅ **1,380 passing tests**
* ✅ Ruff
* ✅ strict mypy
* ✅ clean Git archive build
* ✅ wheel installation smoke tests
* ✅ GitHub Actions CI
* ✅ Python 3.11
* ✅ Python 3.12

Development checks:

```bash
ruff check .
mypy src tests
pytest -q
```

---

## 🔌 Providers

| Provider              | Status       |
| --------------------- | ------------ |
| 🦙 Ollama             | ✅ Production |
| OpenAI-compatible API | 🔭 Planned   |
| llama.cpp             | 🔭 Planned   |

Benchmark definitions remain provider-neutral.

---

## ⚠️ Project Status

**ElaraBench v0.4.0 is the first public release.**

It is still pre-1.0 software.

The CLI and benchmark corpus form the current public baseline, but broad Python API stability and indefinite compatibility with every historical artifact format are not yet guaranteed.

---

## 🗺️ Roadmap

Potential future work includes:

* 🔌 additional inference providers
* 🧪 additional benchmark suites
* 📊 richer reporting and analysis
* 📈 expanded evaluation coverage
* 🧠 additional reasoning benchmarks
* 🤖 additional agentic evaluation scenarios
* 📉 CI coverage thresholds

The goal is to expand capability without sacrificing reproducibility or inspectability.

---

## 📚 Documentation

* [Benchmark Catalog](benchmarks/README.md)
* [Architecture](docs/architecture.md)
* [Benchmark Methodology](docs/benchmark-methodology.md)
* [Benchmark Authoring](docs/benchmark-authoring.md)
* [Reproducibility](docs/reproducibility.md)
* [Result Format](docs/result-format.md)
* [Action Compliance](docs/action-compliance.md)
* [Action Recovery](docs/action-recovery.md)
* [Reactive Execution](docs/reactive-execution.md)
* [Failure Recovery](docs/reactive-failure-recovery.md)
* [Reactive Observability](docs/reactive-observability.md)
* [Changelog](CHANGELOG.md)

---

## ⚖️ License

ElaraBench uses separate licenses for framework code and bundled benchmark data.

**Framework**

[Apache License 2.0](LICENSE)

**Bundled benchmark corpus/data**

[CC0 1.0](src/elarabench/builtin_benchmarks/LICENSE)

---

## 🤝 Contributing

Bug reports, reproducible issues, benchmark proposals and technical discussion are welcome.

[Open an issue](https://github.com/s3cB34r/elaraBench/issues)

---

<p align="center">
  <a href="https://github.com/s3cB34r/elaraBench">GitHub</a>
  ·
  <a href="https://github.com/s3cB34r/elaraBench/releases">Releases</a>
  ·
  <a href="https://github.com/s3cB34r/elaraBench/actions">CI</a>
  ·
  <a href="https://github.com/s3cB34r/elaraBench/issues">Issues</a>
</p>

<p align="center">
  <strong>🧪 ElaraBench — Reproducible evaluation for local AI models.</strong>
</p>

