AGENTS.md

Purpose

This file defines the persistent engineering rules for Codex when working in this repository.

Task-specific instructions are provided separately through "/plan", "/goal", "/review", or direct user instructions.

Do not duplicate or expand those task instructions unless necessary to complete the work correctly.

---

Instruction Priority

Follow instructions in this order:

1. Explicit instructions from the current user/task
2. The current "/plan", "/goal", or "/review" request
3. This "AGENTS.md"
4. Existing repository conventions and documentation
5. Reasonable engineering defaults

If instructions conflict, follow the higher-priority instruction.

Do not silently reinterpret explicit requirements.

---

Workflow Modes

"/plan"

Planning and investigation only.

When the task begins with "/plan":

- Inspect the relevant repository structure and implementation.
- Identify affected components, dependencies, interfaces, tests, and risks.
- Determine the smallest safe implementation path.
- Verify assumptions against the actual codebase.
- Call out ambiguities, compatibility concerns, or architectural consequences.
- Produce a concrete implementation plan and verification strategy.

Do not modify files unless explicitly requested.

Do not create commits.

Avoid speculative redesigns unrelated to the requested goal.

---

"/goal"

Implementation mode.

When the task begins with "/goal":

- Treat the stated target state and completion criteria as authoritative.
- Inspect the relevant existing code before making changes.
- If a prior plan exists, use it unless repository evidence requires deviation.
- Implement the smallest coherent change that fully satisfies the goal.
- Preserve existing architecture, APIs, behavior, and conventions unless change is required.
- Add or update tests where behavior changes.
- Run the required verification before declaring completion.

Do not repeat a full planning phase when the task is already sufficiently specified.

Do not expand scope merely because adjacent improvements are possible.

---

"/review"

Review mode.

When the task begins with "/review":

- Review the actual current implementation and diff.
- Do not assume the implementation is correct because tests pass.
- Look specifically for:
  - correctness bugs
  - incomplete requirements
  - regressions
  - edge cases
  - unsafe behavior
  - security issues
  - error-handling problems
  - race conditions or state inconsistencies
  - API or compatibility breaks
  - weak or missing tests
  - unnecessary complexity
  - unintended scope changes

Prioritize findings by severity.

For every meaningful finding, provide:

- location
- problem
- why it matters
- evidence or reproduction reasoning
- recommended fix

Do not modify code during "/review" unless explicitly instructed to fix the findings.

If no meaningful problems are found, say so clearly.

Do not invent findings merely to produce review output.

---

Repository Inspection

Before editing:

- Inspect the files directly relevant to the requested change.
- Search for existing implementations before creating new abstractions.
- Check nearby tests and usage sites.
- Check repository documentation when architecture or behavior is unclear.

Prefer targeted inspection over reading the entire repository.

Use precise searches such as "rg", file-specific inspection, and focused test execution.

Avoid repeatedly rereading files whose relevant contents are already known.

Do not recursively inspect generated, dependency, cache, build, or artifact directories unless required.

Examples commonly excluded from broad inspection:

- ".git"
- ".venv"
- "venv"
- "node_modules"
- "dist"
- "build"
- ".pytest_cache"
- ".mypy_cache"
- ".ruff_cache"
- generated benchmark/run artifacts
- model files
- binary assets

---

Scope Discipline

Stay inside the requested scope.

Do not:

- perform unrelated refactors
- rename unrelated symbols
- reformat unrelated files
- replace working architecture without a requirement
- introduce new frameworks for convenience
- add speculative features
- change public APIs unnecessarily
- clean up unrelated technical debt
- modify tests solely to make a broken implementation pass

Small supporting changes are allowed when they are genuinely required for the requested goal.

If an adjacent issue is discovered but is not required for the task, mention it instead of silently expanding scope.

---

Implementation Principles

Prefer:

- simple solutions over clever ones
- existing abstractions over duplicate abstractions
- explicit behavior over hidden magic
- deterministic behavior where practical
- narrow interfaces
- clear ownership of state
- backwards compatibility
- provider-neutral boundaries where architecture expects them
- modular capabilities instead of tightly coupled systems

Avoid premature abstraction.

Do not create an abstraction for a single use unless it materially improves correctness, architecture, or testability.

Do not add dependencies when the standard library or existing dependencies solve the problem adequately.

---

Existing Architecture Is Evidence

The current repository is the primary source of truth for how the system is structured.

Before introducing a new:

- service
- adapter
- provider
- manager
- registry
- abstraction
- data model
- configuration layer
- dependency

check whether an equivalent mechanism already exists.

Extend established architecture where reasonable rather than building a parallel system.

Architecture documentation may define additional constraints and must be respected when relevant.

---

Correctness

Correctness takes priority over speed of implementation.

Handle:

- invalid input
- empty input
- boundary values
- failure paths
- partial failures
- unexpected provider/runtime responses
- retries and timeouts where relevant
- interrupted operations
- persistent state consistency

Do not hide failures merely to keep execution moving.

Errors should be actionable and should preserve useful context without exposing sensitive information.

---

Security

Treat external input, generated content, file paths, network responses, model output, and configuration as untrusted unless proven otherwise.

Do not:

- expose secrets
- log credentials or tokens
- hardcode credentials
- weaken security checks for convenience
- bypass validation
- execute untrusted content unnecessarily
- introduce shell injection or path traversal risks
- silently broaden permissions

Use least privilege where applicable.

Security-sensitive behavior should fail safely.

---

Tests

Behavior changes should normally have corresponding tests.

Tests must verify meaningful behavior, not merely execute code paths.

Prefer:

- deterministic tests
- focused unit tests
- regression tests for discovered bugs
- integration tests where boundaries matter

Do not:

- delete valid tests to make the suite pass
- weaken assertions without justification
- skip failing tests without explaining why
- mock away the behavior the test is supposed to verify
- alter fixtures merely to conceal a regression

When fixing a bug, add a regression test when practical.

---

Verification

Never declare a task complete without verification appropriate to the change.

Use the repository's established tooling.

Typical Python verification may include:

pytest -q
ruff check .
mypy .

Run focused checks during development when useful, then run the broader relevant verification before completion.

For changes affecting packaging, CLI behavior, providers, persistence, runtime behavior, benchmark execution, scoring, artifact generation, or integration boundaries, perform an appropriate smoke test when feasible.

If a verification step cannot be run, explicitly state:

- what was not run
- why
- what risk remains

Do not claim a command passed unless it was actually executed successfully.

---

Verification Failures

If verification fails:

1. Determine whether the failure was introduced by the current change.
2. Fix failures caused by the current work.
3. Re-run the affected checks.
4. Run the broader verification again when appropriate.

Do not blindly modify unrelated code to obtain a green test suite.

If a pre-existing failure is discovered, distinguish it clearly from regressions caused by the task.

---

Completion Standard

A "/goal" task is complete only when:

- requested behavior is implemented
- completion criteria are satisfied
- affected interfaces remain coherent
- relevant tests exist and pass
- applicable lint/type checks pass
- required smoke tests pass
- no known task-related correctness issue remains
- unintended scope changes have not been introduced

"Mostly working" is not complete unless the user explicitly requested a prototype.

---

Git Safety

Preserve the user's existing work.

Never use destructive Git operations unless explicitly requested.

Do not:

- discard unrelated working-tree changes
- use "git reset --hard"
- use destructive checkout/restore operations on unrelated files
- force-push
- rewrite shared history
- delete branches
- overwrite user changes

Inspect "git status" and relevant diffs when needed.

Do not treat unrelated existing modifications as part of the task.

---

Commits

Do not create a Git commit unless the current task explicitly requests one.

Before committing:

- ensure verification has passed
- inspect the final diff
- ensure unrelated changes are excluded
- use a concise, descriptive commit message

A commit should represent one coherent change.

Never commit secrets, generated junk, caches, local runtime data, benchmark run artifacts, or unrelated modifications.

---

Token and Context Efficiency

Use repository context deliberately.

Do not consume context by repeatedly explaining or rediscovering obvious information.

Prefer:

- targeted searches
- focused file reads
- concise reasoning
- executing verification instead of speculating
- referencing existing repository conventions instead of restating them
- inspecting only the code relevant to the active task

Do not load large documentation trees, generated artifacts, historical logs, benchmark run directories, or unrelated source directories without a concrete reason.

Do not repeat the user's requirements back verbatim unless clarification or verification requires it.

Task-specific prompts define the goal. This file defines persistent engineering behavior.

When a "/goal" already contains a detailed implementation plan, requirements, constraints, completion criteria, and verification steps, execute it rather than reproducing an equivalent plan.

---

Communication

While working:

- report material discoveries
- mention blockers when they affect completion
- call out necessary deviations from the requested plan
- distinguish evidence from assumptions

Avoid excessive narration of routine actions.

The final report for an implementation should be concise and include:

- what changed
- important implementation decisions
- verification performed
- remaining limitations or risks, if any

Do not produce artificial praise or declare an implementation "production-ready" without evidence.

---

Final Review Before Completion

Before declaring implementation complete, check:

1. Did the implementation actually satisfy the requested goal?
2. Did anything outside the requested scope change?
3. Were existing architectural patterns respected?
4. Are failure paths handled?
5. Are relevant tests present?
6. Did verification actually pass?
7. Does the final diff contain anything accidental?
8. Are there any unresolved issues that should be disclosed?

If any answer exposes a task-related problem, resolve it before declaring completion.

---

Project-Specific Rules: ElaraBench

Project Mission

ElaraBench is a reproducible, provider-neutral benchmark harness for evaluating local and remote AI models.

The benchmark must produce trustworthy, comparable, inspectable results rather than subjective prompt impressions.

Reproducibility, measurement integrity, stable semantics, and transparent failure handling are core requirements.

---

Provider Neutrality

ElaraBench must remain provider-neutral.

Provider-specific behavior belongs behind provider abstractions or adapters.

Do not allow Ollama, llama.cpp, OpenAI-compatible APIs, or any future runtime to leak unnecessary provider-specific assumptions into:

- benchmark definitions
- scoring
- result schemas
- runner orchestration
- reporting
- comparison logic

Shared benchmark semantics must remain independent of the underlying provider whenever practical.

Do not redesign provider abstractions merely to accommodate one runtime unless the abstraction itself is demonstrably insufficient.

---

Benchmark Reproducibility

Reproducibility is a first-class requirement.

Relevant execution configuration must remain explicit and reproducible where supported, including:

- model identifier
- provider
- model/runtime configuration
- temperature
- seed
- repeats
- timeout
- retry policy
- thinking policy
- suite version
- case definitions
- scoring configuration
- relevant content hashes or fingerprints
- runtime/environment metadata where applicable

Do not silently introduce nondeterministic behavior.

If full determinism is impossible because of the model or provider, preserve enough metadata to make the run meaningfully comparable and auditable.

---

Scoring Integrity

Never silently change scoring semantics.

Changes to:

- scoring algorithms
- normalization
- pass/fail rules
- parser behavior
- malformed-output handling
- aggregation
- coverage requirements
- headline scores
- comparison semantics

must be treated as benchmark-semantic changes.

Such changes require:

- explicit implementation rationale
- relevant tests
- regression consideration
- compatibility consideration for existing artifacts/results

Do not modify scoring simply to improve a model's apparent benchmark performance.

Do not weaken assertions, coverage requirements, parsers, or validation rules to make benchmark results look better.

Benchmark correctness takes priority over favorable scores.

---

Execution Failure vs Model Performance

Execution failures must remain distinguishable from model performance.

Do not silently convert infrastructure or runtime failures into model-quality judgments unless the benchmark specification explicitly defines that behavior.

Examples include:

- provider unavailable
- connection failure
- timeout
- malformed transport response
- interrupted execution
- internal runner failure
- corrupted artifact
- unsupported provider capability

Where benchmark semantics intentionally score malformed model output as zero, preserve the distinction between:

- a successfully executed model response that failed the task
- an execution/infrastructure failure that prevented valid evaluation

Do not allow infrastructure instability to silently distort benchmark conclusions.

---

Thinking Policy

Thinking/reasoning configuration is part of reproducibility where supported.

Preserve established thinking-policy semantics.

Do not silently change the resolution order or default behavior.

Provider-specific representations of thinking configuration must remain behind the relevant provider adapter.

Changes affecting thinking policy require tests covering:

- explicit enabled behavior
- explicit disabled behavior
- provider-default behavior
- precedence/resolution rules
- persistence in hashes/fingerprints/artifacts where applicable
- resume compatibility where applicable

---

Result and Artifact Stability

Persisted benchmark artifacts are part of the product contract.

Treat changes to result schemas and persistent artifacts with backwards-compatibility awareness.

Do not silently:

- rename persistent fields
- remove fields
- reinterpret existing fields
- change artifact structure
- change result meaning
- invalidate resume semantics

without considering existing runs and consumers.

When schema evolution is required:

- make the change explicit
- update schema/version information where appropriate
- add migration or compatibility handling when justified
- add tests covering old/new behavior where applicable

Artifacts should remain inspectable and self-describing.

---

Resume and Durable Execution

Resume behavior must preserve benchmark integrity.

A resumed run must not silently change the effective benchmark configuration.

Configuration relevant to benchmark identity should be checked using the project's established hashes, fingerprints, manifests, or equivalent safeguards.

Do not allow resume behavior to mix incompatible:

- suites
- case definitions
- model configurations
- scoring semantics
- thinking policies
- runtime-critical settings

Interrupted execution should preserve as much valid completed work as safely possible.

Do not duplicate completed attempts unless the resume semantics explicitly require it.

---

Benchmark Coverage

Coverage is a correctness signal.

Do not lower coverage thresholds or manipulate coverage calculations merely to obtain a headline score.

Partial benchmark execution must remain distinguishable from complete execution.

Headline scoring should follow the repository's established coverage policy.

If coverage semantics change, treat that as a benchmark-semantic change and test it accordingly.

---

Runtime and Hardware Metrics

Runtime and hardware measurement may include information such as:

- GPU model
- VRAM
- system RAM
- CPU
- runtime/provider
- quantization
- context length
- tokens per second
- time to first token
- total latency
- temperature
- power or energy measurements

These measurements are valuable metadata but must not compromise benchmark correctness.

Measurement failure should not silently corrupt benchmark scoring.

Keep performance telemetry conceptually separate from task-quality scoring unless a benchmark explicitly combines them.

Hardware/runtime collection should degrade gracefully when a metric is unavailable.

Do not fabricate unavailable measurements.

---

Benchmark Suites

Benchmark suites must remain explicit and versionable.

Cases should define evaluation intent clearly enough that results can be reproduced and reviewed.

Avoid ambiguous scoring rules.

Changes to an existing suite that materially alter what it measures should normally require a suite version change or equivalent explicit compatibility signal.

Do not silently mutate historical benchmark meaning.

---

Parsers and Structured Output

Parsers are part of benchmark semantics.

They should be:

- deterministic
- strict enough to preserve benchmark meaning
- tested against valid and invalid outputs
- independent from provider-specific formatting where practical

Do not make parsers progressively permissive simply because a model emits inconvenient formatting.

If tolerant parsing is desired, define the tolerated syntax explicitly and apply it consistently across models.

Malformed output should follow the benchmark's established scoring semantics.

---

Comparability

Changes must preserve meaningful comparison between benchmark runs wherever possible.

Before changing execution or scoring behavior, consider whether the change affects:

- historical comparisons
- model-to-model comparisons
- provider comparisons
- quantization comparisons
- hardware/runtime comparisons
- repeated runs

If comparability is intentionally broken, make that explicit rather than silently presenting incompatible results as equivalent.

---

Performance Optimizations

Do not trade correctness or reproducibility for benchmark speed.

Performance improvements must preserve:

- scoring semantics
- case identity
- deterministic behavior where supported
- artifact integrity
- retry/resume semantics
- provider isolation

Parallelism or concurrency must not introduce race conditions, result ordering ambiguity, duplicate execution, or corrupted persistent state.

Benchmark throughput is secondary to benchmark integrity.

---

External Projects and Dependencies

External benchmark frameworks, runtime projects, or AI-agent systems may be used as inspiration.

Do not migrate ElaraBench onto another framework merely because it provides overlapping functionality.

Prefer selectively adopting useful concepts while retaining ElaraBench's own provider-neutral architecture.

Avoid introducing a dependency when the required functionality is small, stable, and reasonably implemented within the existing architecture.

New dependencies must provide clear value and should not unnecessarily constrain future provider/runtime support.

---

Future Architecture Compatibility

When practical, new work should remain compatible with future ElaraBench capabilities such as:

- additional local model providers
- llama.cpp/OpenAI-compatible runtimes
- additional benchmark suites
- model capability comparisons
- hardware/runtime efficiency measurements
- model-tier evaluation
- automated comparison/report generation
- reproducible shared benchmark results

Do not prematurely implement these future features unless requested.

Architecture should allow reasonable extension without building speculative infrastructure now.

---

ElaraBench Completion Check

Before declaring an ElaraBench implementation complete, additionally ask:

1. Did benchmark semantics change?
2. If yes, was that change intentional, tested, and documented appropriately?
3. Is reproducibility preserved?
4. Can execution failures still be distinguished from model performance?
5. Are persisted artifacts and schemas still coherent?
6. Is provider neutrality preserved?
7. Did any change artificially improve or degrade benchmark scores?
8. Are resume and retry semantics still safe where affected?
9. Are historical comparisons still meaningful?
10. Did runtime or hardware measurement accidentally affect scoring correctness?
11. Were relevant regression tests added?
12. Can another developer inspect the resulting artifacts and understand what actually happened?

If any answer reveals a correctness or benchmark-integrity problem, resolve it before declaring completion.