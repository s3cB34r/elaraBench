"""Production and generic Action Recovery corpus validation tests."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from elarabench.action_recovery_corpus import (
    CorpusFindingCode,
    CorpusFindingSeverity,
    validate_action_recovery_corpus,
)
from elarabench.benchmark import load_benchmark_suite
from elarabench.builtin import get_builtin_suite_path
from elarabench.hashing import hash_suite
from elarabench.models import EvaluationSpecification


def _loaded():
    return load_benchmark_suite(get_builtin_suite_path("action_recovery.core"))


def test_production_corpus_satisfies_full_profile_without_findings() -> None:
    loaded = _loaded()
    findings = validate_action_recovery_corpus(loaded)
    assert len(loaded.suite.cases) == 36
    assert findings.valid
    assert findings.errors == ()
    assert findings.warnings == ()


def test_findings_are_stably_sorted_and_machine_readable() -> None:
    loaded = _loaded()
    first = loaded.suite.cases[0]
    broken = first.model_copy(update={"tags": (*first.tags, "contrastive-group-ar-broken")})
    suite = loaded.suite.model_copy(update={"cases": (broken, *loaded.suite.cases[1:])})
    findings = validate_action_recovery_corpus(replace(loaded, suite=suite))
    assert any(
        finding.code is CorpusFindingCode.MALFORMED_RESERVED_TAG
        and finding.severity is CorpusFindingSeverity.ERROR
        for finding in findings.errors
    )
    assert findings.errors == tuple(
        sorted(
            findings.errors,
            key=lambda finding: (
                finding.severity.value,
                finding.code.value,
                finding.group_id or "",
                finding.case_ids,
                finding.message,
            ),
        )
    )


def test_controlled_pair_mismatch_is_objectively_invalid_for_custom_suite() -> None:
    loaded = _loaded()
    first = loaded.suite.cases[0]
    changed = first.model_copy(update={"difficulty": "hard"})
    suite = loaded.suite.model_copy(
        update={
            "id": "custom.recovery",
            "version": "0.1.0",
            "cases": (changed, *loaded.suite.cases[1:2]),
        }
    )
    findings = validate_action_recovery_corpus(replace(loaded, suite=suite))
    assert any(
        finding.code is CorpusFindingCode.CONTROLLED_PAIR_MISMATCH for finding in findings.errors
    )
    assert not any(finding.code is CorpusFindingCode.CORE_CASE_COUNT for finding in findings.errors)


def test_production_has_matched_authorization_and_trust_payload_probes() -> None:
    loaded = _loaded()
    findings = validate_action_recovery_corpus(loaded)
    assert not any(
        finding.code
        in {
            CorpusFindingCode.AUTHORIZATION_DISTRIBUTION_LEAKAGE,
            CorpusFindingCode.MISSING_TRUST_PROBE,
            CorpusFindingCode.CLASS_EXCLUSIVE_LEAKAGE,
        }
        for finding in (*findings.errors, *findings.warnings)
    )
    probes = [case for case in loaded.suite.cases if "trust-payload-probe" in case.tags]
    assert len(probes) == 2
    assert all("approval" in case.messages[-1].content for case in probes)
    assert all("authorization" in case.messages[-1].content for case in probes)


def test_recovery_content_identity_covers_messages_metadata_and_trusted_config() -> None:
    loaded = _loaded()
    original = loaded.suite.cases[0]
    mutations = []
    messages = list(original.messages)
    messages[1] = messages[1].model_copy(
        update={"content": messages[1].content + "\nIdentity mutation."}
    )
    mutations.append(original.model_copy(update={"messages": tuple(messages)}))
    mutations.append(original.model_copy(update={"category": "record-lifecycle"}))
    mutations.append(original.model_copy(update={"tags": (*original.tags, "identity-probe")}))
    for key, value in (
        ("recoverability", "unrecoverable"),
        ("resulting_state", {"completed": False, "phase": "changed"}),
        ("expected_state", {"completed": True, "phase": "changed"}),
        ("outcome_semantic", "changed-semantic"),
    ):
        config = deepcopy(original.evaluation.config)
        config[key] = value
        mutations.append(
            original.model_copy(
                update={"evaluation": original.evaluation.model_copy(update={"config": config})}
            )
        )
    config = deepcopy(original.evaluation.config)
    config["attempted_actions"][0]["arguments"]["target"] = "changed"
    mutations.append(
        original.model_copy(
            update={
                "evaluation": EvaluationSpecification.model_construct(
                    type="action_recovery",
                    config=config,
                    components=(),
                )
            }
        )
    )
    config = deepcopy(original.evaluation.config)
    config["outcome_per_action"][0] = "not_executed"
    mutations.append(
        original.model_copy(
            update={
                "evaluation": EvaluationSpecification.model_construct(
                    type="action_recovery",
                    config=config,
                    components=(),
                )
            }
        )
    )
    for changed in mutations:
        suite = loaded.suite.model_copy(
            update={
                "cases": tuple(
                    changed if case.id == original.id else case for case in loaded.suite.cases
                )
            }
        )
        assert hash_suite(suite, loaded.fixture_files) != loaded.content_hash
