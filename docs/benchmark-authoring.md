# First-party benchmark authoring

## Case convention

Released IDs use `<domain>-<archetype>-<three-digit-number>`. Allowed domain prefixes are
`reasoning`, `instruction`, `coding`, and `cyber`. IDs are lowercase kebab-case, stable after
release, independent of array position, and contain no answer, difficulty, or version. Retired IDs
are never reused, and later recategorization does not rename them.

M3 first-party cases use exactly one difficulty value (`easy`, `medium`, or `hard`) and exactly one
matching `difficulty-*` tag. Every case has weight 1.0 and explicit `CC0-1.0` license and
provenance. The M3.1 provenance record consistently identifies first-party origin, ElaraBench
contributors, creation method and disclosed drafting assistance, lack of a derived source,
independent expected-answer derivation, initial contamination risk, and introduction version.

## Release checklist

Before release, every case must satisfy all of the following:

- There is one defensible expected outcome.
- All necessary semantics appear in the ordered prompt messages.
- The task has no current-date, network, or hidden external-context dependency.
- The expected answer was independently derived rather than copied from a target model.
- The evaluator configuration validates during suite loading.
- A known-correct output produces the intended passing score.
- A known-wrong output produces a deterministic scored failure.
- Malformed output behavior is intentional and tested where formatting matters.
- Difficulty is assigned from capability demand and has a matching tag.
- Category, skill tags, and answer-format tags are accurate.
- Provenance and license are present and use the corpus convention.
- No externally copyrighted benchmark question or public puzzle wording was reused.
- Loading or evaluating the case cannot execute a fixture, command, or model-generated code.
- Unrelated metadata and prompt scaffolding do not accidentally reveal the answer.
- The ID follows the convention and does not duplicate or reuse a released ID.
- Weight is exactly 1.0.

LLM-assisted drafting does not constitute verification. Reviewers must independently recompute
arithmetic, quantitative, and probability results; enumerate finite logic and constraint cases to
check uniqueness; verify symbolic transformations step by step; and compare every instruction
requirement with its evaluator behavior. Ground truth is never adjusted merely because a model
performs poorly.

For first-party cases, an explicit requirement to return "one line" means that the complete
response contains no character Python recognizes as a line boundary: LF, CR, vertical tab, form
feed, the file/group/record separators U+001C through U+001E, NEL U+0085, LINE SEPARATOR U+2028,
or PARAGRAPH SEPARATOR U+2029. CRLF is rejected through its CR and LF characters. A regex-backed
one-line check must exclude this complete set; exact-match cases enforce their exact expected
representation directly. This strictness applies only when the prompt declares a line constraint.

## Golden evidence

Test-only golden JSONL is keyed by case ID and contains a known-correct and known-incorrect model
response, plus a malformed response where useful. Benchmark truth remains in `cases.jsonl`; golden
files are evidence that the declared evaluator implements it.

All correct goldens must be `scored` and normally score 1.0. Incorrect and malformed goldens must
remain `scored`, normally score 0.0, and never disappear from coverage. Full all-correct and mixed
FakeProvider runs verify aggregation as well as individual evaluation.

Released suite IDs, versions, case counts, and hashes are pinned in
`tests/fixtures/builtin_suite_hashes.json`. An intentional benchmark change requires the semantic
version decision described in the methodology, review of every affected golden, regeneration of
the content hash with `elarabench validate`, and an explicit pinned-hash update in the same change.
Never update a pin merely to silence an unexplained failure.

## Fixtures and safety

Reasoning and instruction-following core cases are self-contained and use no fixtures. A future
fixture must be referenced by a case, have compatible provenance and licensing, remain beneath the
suite's `fixtures/` directory, and participate in suite hashing and snapshots. Loading a fixture
never authorizes its execution. Executable coding or cybersecurity evaluation requires a separate
sandbox milestone.

## Review scope

Mechanical QA checks structure, counts, metadata, hashes, and evaluator outcomes. It cannot prove
that wording is unambiguous or uncontaminated. Human review remains responsible for clarity,
independent derivation, meaningful difficulty, and overlap with known public benchmarks.
