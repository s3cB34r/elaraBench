"""Structural, provenance, hashing, and golden QA for first-party suites."""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from decimal import Decimal
from fractions import Fraction
from ipaddress import ip_address, ip_network
from itertools import permutations
from pathlib import Path
from typing import NotRequired, TypedDict, cast

import pytest

from elarabench.aggregation import aggregate
from elarabench.benchmark import LoadedBenchmarkSuite, load_benchmark_suite
from elarabench.builtin import get_builtin_suite_path
from elarabench.evaluators import evaluate
from elarabench.models import (
    AggregationSample,
    BenchmarkCase,
    ChatRole,
    EvaluationContext,
    EvaluationSpecification,
    EvaluationStatus,
    GenerationResponse,
    SampleIdentity,
    ThinkingPolicy,
)

PROJECT_ROOT = Path(__file__).parents[2]
GOLDEN_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "builtin_suite_goldens"
HASH_PATH = PROJECT_ROOT / "tests" / "fixtures" / "builtin_suite_hashes.json"
PROVENANCE_PARTS = (
    "origin=ElaraBench first-party",
    "author=ElaraBench contributors",
    "creation_method=manually directed synthetic authoring with LLM drafting assistance",
    "derived_source=none",
    "expected_answer_source=independently derived from supplied task",
    "contamination_risk=low at initial creation",
    "introduced=1.0.0",
)
ALLOWED_EVALUATORS = {
    "composite",
    "exact_match",
    "json_schema",
    "multiple_choice",
    "normalized_match",
    "numeric",
    "regex_full_match",
    "required_content",
    "forbidden_content",
}
ID_PATTERN = re.compile(
    r"^(?:(?:reasoning|instruction|coding|cyber)-[a-z0-9]+"
    r"(?:-[a-z0-9]+)*|refusal-compliance)-\d{3}$"
)
TEMPORAL_PATTERN = re.compile(
    r"\b(?:today|tomorrow|yesterday|currently|current date|as of|20\d{2})\b",
    re.IGNORECASE,
)


class GoldenEntry(TypedDict):
    case_id: str
    correct_response: str
    incorrect_response: str
    malformed_response: NotRequired[str]
    line_separator_responses: NotRequired[dict[str, str]]


SUITES = {
    "reasoning.core": get_builtin_suite_path("reasoning.core"),
    "instruction_following.core": get_builtin_suite_path("instruction_following.core"),
    "coding.core": get_builtin_suite_path("coding.core"),
    "cybersecurity.core": get_builtin_suite_path("cybersecurity.core"),
}
GOLDEN_PATHS = {
    "reasoning.core": GOLDEN_ROOT / "reasoning-core-v1.jsonl",
    "instruction_following.core": GOLDEN_ROOT / "instruction-following-core-v1.jsonl",
    "coding.core": GOLDEN_ROOT / "coding-core-v1.jsonl",
    "cybersecurity.core": GOLDEN_ROOT / "cybersecurity-core-v1.jsonl",
}
EXPECTED_CASE_COUNTS = {
    "reasoning.core": 18,
    "instruction_following.core": 18,
    "coding.core": 12,
    "cybersecurity.core": 12,
}
EXPECTED_DIFFICULTIES = {
    "reasoning.core": {"easy": 6, "medium": 8, "hard": 4},
    "instruction_following.core": {"easy": 6, "medium": 8, "hard": 4},
    "coding.core": {"easy": 4, "medium": 6, "hard": 2},
    "cybersecurity.core": {"easy": 4, "medium": 6, "hard": 2},
}
EXPECTED_CATEGORIES = {
    "reasoning.core": {
        "arithmetic",
        "quantitative",
        "logic",
        "constraints",
        "probability",
        "symbolic",
    },
    "instruction_following.core": {
        "exact-format",
        "content-constraints",
        "transformation",
        "ordering-count",
        "structured-json",
        "multi-constraint",
    },
    "coding.core": {
        "comprehension",
        "debugging",
        "algorithm-analysis",
        "patch-selection",
    },
    "cybersecurity.core": {
        "log-analysis",
        "secure-code-review",
        "configuration-review",
        "incident-triage",
    },
}
ONE_LINE_CASE_IDS = {
    "instruction-content-002",
    "instruction-ordering-001",
    "instruction-multi-002",
}
REGEX_ONE_LINE_CASE_IDS = {
    "instruction-content-002",
    "instruction-multi-002",
}
ONE_LINE_PATTERN = r"[^\n\r\v\f\x1c-\x1e\x85\u2028\u2029]+"
EXPECTED_LINE_SEPARATOR_NAMES = {
    "lf",
    "cr",
    "crlf",
    "vertical_tab",
    "form_feed",
    "file_separator",
    "group_separator",
    "record_separator",
    "next_line",
    "line_separator",
    "paragraph_separator",
}


def load_goldens(suite_id: str) -> dict[str, GoldenEntry]:
    entries = [
        cast(GoldenEntry, json.loads(line))
        for line in GOLDEN_PATHS[suite_id].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {entry["case_id"]: entry for entry in entries}


def all_evaluator_types(specification: EvaluationSpecification) -> set[str]:
    types = {specification.type}
    for component in specification.components:
        types.update(all_evaluator_types(component.specification))
    return types


def loaded_suites() -> dict[str, LoadedBenchmarkSuite]:
    return {suite_id: load_benchmark_suite(path) for suite_id, path in SUITES.items()}


@pytest.mark.parametrize("suite_id", SUITES)
def test_builtin_suite_identity_defaults_and_order(suite_id: str) -> None:
    loaded = load_benchmark_suite(SUITES[suite_id])
    cases = loaded.suite.cases

    assert loaded.suite.id == suite_id
    assert loaded.suite.version == "1.0.0"
    assert len(cases) == EXPECTED_CASE_COUNTS[suite_id]
    assert loaded.suite.defaults.repeats == 1
    assert loaded.suite.defaults.timeout_seconds == 120
    assert loaded.suite.defaults.thinking is ThinkingPolicy.DISABLED
    assert [case.id for case in cases] == [
        json.loads(line)["id"]
        for line in (SUITES[suite_id] / "cases.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def test_case_ids_are_globally_unique_and_conventional() -> None:
    ids = [case.id for loaded in loaded_suites().values() for case in loaded.suite.cases]

    assert len(ids) == 60
    assert len(ids) == len(set(ids))
    assert all(ID_PATTERN.fullmatch(case_id) for case_id in ids)


@pytest.mark.parametrize("suite_id", SUITES)
def test_difficulty_distribution_and_tags(suite_id: str) -> None:
    cases = load_benchmark_suite(SUITES[suite_id]).suite.cases

    assert Counter(case.difficulty for case in cases) == EXPECTED_DIFFICULTIES[suite_id]
    for case in cases:
        difficulty_tags = [tag for tag in case.tags if tag.startswith("difficulty-")]
        assert difficulty_tags == [f"difficulty-{case.difficulty}"]


@pytest.mark.parametrize("suite_id", SUITES)
def test_categories_are_balanced(suite_id: str) -> None:
    cases = load_benchmark_suite(SUITES[suite_id]).suite.cases

    assert set(case.category for case in cases) == EXPECTED_CATEGORIES[suite_id]
    assert Counter(case.category for case in cases) == {
        category: 3 for category in EXPECTED_CATEGORIES[suite_id]
    }


@pytest.mark.parametrize("suite_id", SUITES)
def test_case_metadata_and_engine_boundaries(suite_id: str) -> None:
    cases = load_benchmark_suite(SUITES[suite_id]).suite.cases

    for case in cases:
        assert case.weight == 1.0
        assert case.license == "CC0-1.0"
        assert case.provenance is not None
        assert all(part in case.provenance for part in PROVENANCE_PARTS)
        assert case.fixtures == ()
        assert case.response_format is None
        assert case.seed is None
        assert all(message.role is not ChatRole.TOOL for message in case.messages)
        assert not TEMPORAL_PATTERN.search("\n".join(message.content for message in case.messages))
        assert all_evaluator_types(case.evaluation) <= ALLOWED_EVALUATORS


@pytest.mark.parametrize("suite_id", ("coding.core", "cybersecurity.core"))
def test_static_analysis_suites_have_no_execution_path(suite_id: str) -> None:
    cases = load_benchmark_suite(SUITES[suite_id]).suite.cases

    for case in cases:
        assert "static-analysis" in case.tags
        assert case.fixtures == ()
        assert case.response_format is None
        assert all(message.role in {ChatRole.SYSTEM, ChatRole.USER} for message in case.messages)
        assert all_evaluator_types(case.evaluation) <= ALLOWED_EVALUATORS


def test_coding_language_distribution() -> None:
    cases = load_benchmark_suite(SUITES["coding.core"]).suite.cases
    language_tags = [
        tag
        for case in cases
        for tag in case.tags
        if tag.startswith("language-")
    ]

    assert Counter(language_tags) == {"language-python": 10, "language-sql": 2}


@pytest.mark.parametrize(
    ("suite_id", "expected_labels"),
    (
        ("coding.core", {"A": 2, "B": 2, "C": 2, "D": 3}),
        ("cybersecurity.core", {"A": 3, "B": 3, "C": 2, "D": 2}),
    ),
)
def test_m32_multiple_choice_labels_are_balanced(
    suite_id: str,
    expected_labels: dict[str, int],
) -> None:
    cases = load_benchmark_suite(SUITES[suite_id]).suite.cases
    labels = [
        cast(str, case.evaluation.config["expected"])
        for case in cases
        if case.evaluation.type == "multiple_choice"
    ]

    assert Counter(labels) == expected_labels


def test_coding_author_ground_truth_checks() -> None:
    cases = {case.id: case for case in load_benchmark_suite(SUITES["coding.core"]).suite.cases}
    values = [2, 5, 1]
    total = sum(value * 2 if value % 2 == 0 else value for value in values)
    rows = [("A", 1), ("A", 0), ("B", 1), ("B", 1), ("C", 0)]
    active_by_department = Counter(dept for dept, active in rows if active == 1)
    selected_rows = [
        f"{dept}|{count}"
        for dept, count in sorted(active_by_department.items())
        if count >= 2
    ]

    assert total == cases["coding-comprehension-001"].evaluation.config["expected"]
    assert selected_rows == [cases["coding-comprehension-003"].evaluation.config["expected"]]

    def recurrence_work(n: int) -> int:
        return 0 if n <= 1 else n + 2 * recurrence_work(n // 2)

    def recursion_depth(n: int) -> int:
        return 0 if n <= 1 else 1 + recursion_depth(n // 2)

    for exponent in range(1, 11):
        size = 2**exponent
        assert recurrence_work(size) == size * exponent
        assert recursion_depth(size) == exponent

    complexity = cases["coding-algorithm-analysis-003"].evaluation.config["schema"]
    assert complexity == {
        "const": {"time": "O(n log n)", "auxiliary_space": "O(log n)"}
    }


def test_cyber_ground_truth_is_pinned_to_supplied_policy() -> None:
    cases = {
        case.id: case
        for case in load_benchmark_suite(SUITES["cybersecurity.core"]).suite.cases
    }

    assert cases["cyber-log-analysis-001"].evaluation.config["expected"] == "B"
    assert cases["cyber-log-analysis-002"].evaluation.config["expected"] == "D"
    assert cases["cyber-log-analysis-003"].evaluation.config["expected"] == "A"
    assert cases["cyber-incident-triage-001"].evaluation.config["expected"] == "high"
    incident = cases["cyber-incident-triage-003"].evaluation.config["schema"]
    unauthorized_publish_reached_production = True
    source_claim_is_contradicted_by_signed_telemetry = True
    derived_result = {
        "severity": "critical" if unauthorized_publish_reached_production else "high",
        "first_action": (
            "disable-service-token"
            if unauthorized_publish_reached_production
            else "investigate"
        ),
        "affected_asset": (
            "unknown"
            if source_claim_is_contradicted_by_signed_telemetry
            else "build-07"
        ),
    }

    assert incident == {"const": derived_result}


def test_derived_json_output_instructions_do_not_disclose_complete_answers() -> None:
    def collect_strings(value: object) -> list[str]:
        if isinstance(value, str):
            return [value] if len(value) >= 2 else []
        if isinstance(value, list):
            return [text for item in value for text in collect_strings(item)]
        if isinstance(value, dict):
            return [text for item in value.values() for text in collect_strings(item)]
        return []

    obvious_leakage = re.compile(
        r"\b(?:the correct answer is|the expected result is|use values .+ respectively)\b",
        re.IGNORECASE | re.DOTALL,
    )
    for suite_id, loaded in loaded_suites().items():
        for case in loaded.suite.cases:
            prompt = "\n".join(message.content for message in case.messages)
            assert not obvious_leakage.search(prompt), case.id
            if suite_id == "instruction_following.core":
                continue
            schema = case.evaluation.config.get("schema")
            if not isinstance(schema, dict) or "const" not in schema:
                continue
            expected = schema["const"]
            scalar_strings = collect_strings(expected)
            _, separator, instruction = case.messages[-1].content.rpartition("Return")
            final_instruction = f"{separator}{instruction}"
            assert not scalar_strings or not all(
                value.casefold() in final_instruction.casefold()
                for value in scalar_strings
            ), case.id


def test_cyber_network_addresses_are_documentation_only() -> None:
    cases = load_benchmark_suite(SUITES["cybersecurity.core"]).suite.cases
    address_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    addresses = {
        ip_address(match)
        for case in cases
        for message in case.messages
        for match in address_pattern.findall(message.content)
    }
    documentation_networks = (
        ip_network("192.0.2.0/24"),
        ip_network("198.51.100.0/24"),
        ip_network("203.0.113.0/24"),
    )

    assert addresses
    assert all(
        any(address in network for network in documentation_networks)
        for address in addresses
    )


def test_reasoning_multiple_choice_labels_are_balanced() -> None:
    cases = load_benchmark_suite(SUITES["reasoning.core"]).suite.cases
    labels = [
        cast(str, case.evaluation.config["expected"])
        for case in cases
        if case.evaluation.type == "multiple_choice"
    ]

    assert Counter(labels) == {"A": 1, "B": 1, "C": 1, "D": 1}


def test_multistep_reasoning_ground_truth_is_independently_derived() -> None:
    cases = {
        case.id: case
        for case in load_benchmark_suite(SUITES["reasoning.core"]).suite.cases
    }
    fraction_result = (Fraction(7, 12) + Fraction(5, 18)) / Fraction(11, 9)
    discounted_subtotal = Decimal(8) * Decimal("37.50") * Decimal("0.88")
    total = discounted_subtotal + Decimal(18) + discounted_subtotal * Decimal("0.05")
    conditional_probability = Fraction(2 * (4 + 3), 4 * 3 + 4 * 2 + 3 * 2)
    pair = (5, 2)
    for _ in range(3):
        pair = (pair[0] + 2 * pair[1], pair[0] - pair[1])

    assert str(fraction_result) == cases["reasoning-arithmetic-003"].evaluation.config[
        "expected"
    ]
    assert format(total, ".2f") == "295.20"
    assert str(conditional_probability) == cases[
        "reasoning-probability-003"
    ].evaluation.config["expected"]
    assert pair == (27, 9)


def test_finite_reasoning_solutions_are_unique() -> None:
    presentation_orders = [
        order
        for order in permutations(("Lina", "Miro", "Noor"))
        if order[0] != "Lina" and order.index("Noor") == order.index("Miro") + 1
    ]
    task_orders = [
        order
        for order in permutations(("A", "B", "C", "D"))
        if order.index("B") == order.index("D") + 1
        and order.index("A") < order.index("C")
        and order[3] != "C"
    ]
    faulty_sensors = []
    for faulty in ("A", "B", "C"):
        reports = (faulty == "B", faulty == "C", faulty != "C")
        if sum(reports) == 2:
            faulty_sensors.append(faulty)

    assert presentation_orders == [("Miro", "Noor", "Lina")]
    assert task_orders == [("A", "C", "D", "B")]
    assert faulty_sensors == ["B"]


def test_all_explicit_one_line_cases_have_complete_enforcement() -> None:
    cases = load_benchmark_suite(SUITES["instruction_following.core"]).suite.cases
    declared_one_line = {
        case.id
        for case in cases
        if re.search(
            r"\b(?:one line|one non-empty line)\b",
            "\n".join(message.content for message in case.messages),
            re.IGNORECASE,
        )
    }

    assert declared_one_line == ONE_LINE_CASE_IDS
    for case in cases:
        if case.id in REGEX_ONE_LINE_CASE_IDS:
            line_component = case.evaluation.components[2].specification
            assert line_component.type == "regex_full_match"
            assert line_component.config["pattern"] == ONE_LINE_PATTERN
        elif case.id == "instruction-ordering-001":
            assert case.evaluation.type == "exact_match"


def line_separator_parameters() -> list[tuple[BenchmarkCase, str, str]]:
    loaded = load_benchmark_suite(SUITES["instruction_following.core"])
    cases = {case.id: case for case in loaded.suite.cases}
    goldens = load_goldens("instruction_following.core")
    parameters: list[tuple[BenchmarkCase, str, str]] = []
    for case_id in sorted(REGEX_ONE_LINE_CASE_IDS):
        responses = goldens[case_id]["line_separator_responses"]
        assert set(responses) == EXPECTED_LINE_SEPARATOR_NAMES
        parameters.extend(
            (cases[case_id], separator_name, response)
            for separator_name, response in responses.items()
        )
    return parameters


@pytest.mark.parametrize(
    ("case", "separator_name", "response"),
    line_separator_parameters(),
    ids=lambda value: value.id if isinstance(value, BenchmarkCase) else None,
)
def test_one_line_cases_reject_every_recognized_separator(
    case: BenchmarkCase,
    separator_name: str,
    response: str,
) -> None:
    del separator_name
    result = evaluate(
        EvaluationContext(
            response=GenerationResponse(text=response),
            specification=case.evaluation,
        )
    )

    assert result.status is EvaluationStatus.SCORED
    assert result.score == pytest.approx(2 / 3)
    assert result.passed is False
    assert result.metrics["component_2_score"] == 0.0


@pytest.mark.parametrize("case_id", sorted(REGEX_ONE_LINE_CASE_IDS))
def test_one_line_control_response_still_passes(case_id: str) -> None:
    loaded = load_benchmark_suite(SUITES["instruction_following.core"])
    case = next(case for case in loaded.suite.cases if case.id == case_id)
    response = load_goldens("instruction_following.core")[case_id]["correct_response"]

    result = evaluate(
        EvaluationContext(
            response=GenerationResponse(text=response),
            specification=case.evaluation,
        )
    )

    assert result.status is EvaluationStatus.SCORED
    assert result.score == 1.0
    assert result.passed is True


def test_one_line_separator_failure_counts_toward_coverage() -> None:
    loaded = load_benchmark_suite(SUITES["instruction_following.core"])
    case = next(
        case for case in loaded.suite.cases if case.id == "instruction-multi-002"
    )
    result = evaluate(
        EvaluationContext(
            response=GenerationResponse(text="DELTA\rRIVER"),
            specification=case.evaluation,
        )
    )
    summary = aggregate(
        [
            AggregationSample(
                identity=SampleIdentity(case_id=case.id, repeat_index=0),
                category=case.category,
                tags=case.tags,
                case_weight=case.weight,
                result=result,
            )
        ],
        expected_samples=1,
    )

    assert summary.coverage.ratio == 1.0
    assert summary.coverage.scored_samples == 1
    assert summary.score == pytest.approx(2 / 3)


def golden_parameters() -> list[tuple[str, BenchmarkCase, GoldenEntry]]:
    parameters: list[tuple[str, BenchmarkCase, GoldenEntry]] = []
    for suite_id, loaded in loaded_suites().items():
        goldens = load_goldens(suite_id)
        assert set(goldens) == {case.id for case in loaded.suite.cases}
        parameters.extend((suite_id, case, goldens[case.id]) for case in loaded.suite.cases)
    return parameters


@pytest.mark.parametrize(
    ("suite_id", "case", "golden"),
    golden_parameters(),
    ids=lambda value: value.id if isinstance(value, BenchmarkCase) else None,
)
def test_every_golden_response_has_intended_semantics(
    suite_id: str,
    case: BenchmarkCase,
    golden: GoldenEntry,
) -> None:
    del suite_id
    correct = evaluate(
        EvaluationContext(
            response=GenerationResponse(text=golden["correct_response"]),
            specification=case.evaluation,
        )
    )
    incorrect = evaluate(
        EvaluationContext(
            response=GenerationResponse(text=golden["incorrect_response"]),
            specification=case.evaluation,
        )
    )

    assert correct.status is EvaluationStatus.SCORED
    assert correct.score == 1.0
    assert correct.passed is True
    assert incorrect.status is EvaluationStatus.SCORED
    assert incorrect.score == 0.0
    assert incorrect.passed is False

    malformed_text = golden.get("malformed_response")
    if malformed_text is not None:
        malformed = evaluate(
            EvaluationContext(
                response=GenerationResponse(text=malformed_text),
                specification=case.evaluation,
            )
        )
        assert malformed.status is EvaluationStatus.SCORED
        assert malformed.score == 0.0
        assert malformed.passed is False


@pytest.mark.parametrize("suite_id", SUITES)
def test_released_suite_hash_is_pinned(suite_id: str) -> None:
    pins = cast(dict[str, dict[str, object]], json.loads(HASH_PATH.read_text(encoding="utf-8")))
    loaded = load_benchmark_suite(SUITES[suite_id])
    pin = pins[suite_id]

    assert pin == {
        "version": loaded.suite.version,
        "case_count": len(loaded.suite.cases),
        "content_hash": loaded.content_hash,
    }


@pytest.mark.parametrize(
    ("suite_id", "original_text", "changed_text"),
    (
        (
            "reasoning.core",
            "Six crates each contain 14 sensors.",
            "Seven crates each contain 14 sensors.",
        ),
        (
            "coding.core",
            "values = [2, 5, 1]",
            "values = [2, 5, 2]",
        ),
    ),
)
def test_meaningful_case_mutation_changes_suite_hash(
    tmp_path: Path,
    suite_id: str,
    original_text: str,
    changed_text: str,
) -> None:
    source = SUITES[suite_id]
    mutated = tmp_path / f"{suite_id.replace('.', '-')}-v1"
    shutil.copytree(source, mutated)
    case_path = mutated / "cases.jsonl"
    original = load_benchmark_suite(source).content_hash
    content = case_path.read_text(encoding="utf-8")
    changed = content.replace(original_text, changed_text, 1)
    assert changed != content
    case_path.write_text(changed, encoding="utf-8")

    assert load_benchmark_suite(mutated).content_hash != original
