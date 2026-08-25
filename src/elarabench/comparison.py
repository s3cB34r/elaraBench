"""Read-only, same-benchmark quality comparison and comparability policy."""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from pydantic import BaseModel, JsonValue

from elarabench.comparison_models import (
    BehavioralRateComparison,
    BenchmarkIdentityEvidence,
    BreakdownComparison,
    CaseComparison,
    CaseDefinitionMismatch,
    CaseDirection,
    CaseEvidenceStatus,
    CasePopulationMode,
    ComparabilityAssessment,
    ComparabilityClassification,
    ComparabilityDimension,
    ComparisonIntent,
    ComparisonReasonCode,
    ComparisonResult,
    CoverageComparison,
    EvaluatorProvenance,
    EvaluatorResolutionEvidence,
    EvidenceImpact,
    EvidenceState,
    FieldEvidence,
    IntersectionCoverage,
    ModelIdentityAssessment,
    ModelIdentityConfidence,
    PerformanceAnalysis,
    RefusalComplianceAnalysis,
    RunComparisonReference,
    ScoreComparison,
    SourceRunScore,
    VerifiedCaseIdentity,
    VerifiedIntersectionEvidence,
)
from elarabench.comparison_performance import (
    PerformanceComparabilityContext,
    SamplePerformanceEvidence,
    analyze_performance,
)
from elarabench.evaluators.registry import evaluate, resolve_evaluator_identity
from elarabench.evidence import (
    RunIntegrityError,
    StoredManifest,
    StoredSnapshot,
    open_run_path,
    plan_by_identity,
    validate_stored_run,
    verify_attempt_request_hashes,
    verify_sample_artifact_dependencies,
    verify_stored_request,
    verify_terminal_attempt_response,
)
from elarabench.hashing import (
    hash_canonical,
    hash_case_with_fixture_hashes,
    hash_evaluation_specification,
)
from elarabench.legacy_v2 import LegacyV2RunManifest
from elarabench.models import (
    AggregationSample,
    BehavioralRate,
    BenchmarkCase,
    EvaluationContext,
    EvaluationResult,
    EvaluationStatus,
    GenerationResponse,
    SampleIdentity,
    ThinkingControlKind,
    ThinkingPolicy,
    TimingMetadata,
)
from elarabench.refusal_compliance import (
    RefusalCaseExpectation,
    derive_refusal_compliance_summary,
    expectation_from_specification,
)
from elarabench.storage import ArtifactStoreError, RunArtifactStore

COMPARISON_POLICY_VERSION = "1.3.0"
Clock = Callable[[], datetime]
ModelIdentityFieldRole = Literal[
    "material_identity",
    "material_profile",
    "material_fallback",
    "descriptive",
]
MODEL_IDENTITY_FIELD_POLICY: dict[str, ModelIdentityFieldRole] = {
    "provider": "material_profile",
    "backend": "material_profile",
    "model": "material_identity",
    "model_digest": "material_identity",
    "quantization": "material_identity",
    "backend_version": "material_profile",
    "tokenizer": "material_identity",
    "chat_template": "material_fallback",
    "architecture": "material_identity",
    "format": "material_identity",
    "family": "material_identity",
    "parameter_size": "material_identity",
    "capabilities": "descriptive",
    "parameters_hash": "material_identity",
    "template_hash": "material_identity",
}
EnvironmentFieldRole = Literal["performance", "descriptive"]
ENVIRONMENT_FIELD_POLICY: dict[str, EnvironmentFieldRole] = {
    "python_version": "performance",
    "python_implementation": "performance",
    "operating_system": "performance",
    "os_release": "performance",
    "architecture": "performance",
    "cpu": "performance",
    "gpus": "performance",
    "gpu_driver": "performance",
    "runtime_versions": "performance",
    "diagnostics": "descriptive",
}


class ComparisonError(ValueError):
    """Comparison input could not be loaded or validated."""


class _CaseEvidence:
    def __init__(
        self,
        case: BenchmarkCase,
        results_by_repeat: Mapping[int, EvaluationResult],
        *,
        expected_repeats: int,
    ) -> None:
        self.case = case
        self.results_by_repeat = dict(sorted(results_by_repeat.items()))
        self.results = tuple(self.results_by_repeat.values())
        self.expected_repeats = expected_repeats
        scores = [
            result.score
            for result in self.results
            if result.status is EvaluationStatus.SCORED and result.score is not None
        ]
        self.scored_repeat_indexes = tuple(
            repeat_index
            for repeat_index, result in self.results_by_repeat.items()
            if result.status is EvaluationStatus.SCORED and result.score is not None
        )
        self.scored_repeats = len(scores)
        self.score = math.fsum(scores) / len(scores) if scores else None
        self.fully_scored = (
            len(self.results) == expected_repeats and len(scores) == expected_repeats
        )

    @property
    def sample_statuses(self) -> tuple[str, ...]:
        return tuple(
            self.results_by_repeat[repeat_index].status.value
            if repeat_index in self.results_by_repeat
            else CaseEvidenceStatus.MISSING.value
            for repeat_index in range(self.expected_repeats)
        )

    @property
    def status(self) -> CaseEvidenceStatus:
        if self.fully_scored:
            return CaseEvidenceStatus.SCORED
        if len(self.results) < self.expected_repeats:
            return (
                CaseEvidenceStatus.INCOMPLETE
                if self.results
                else CaseEvidenceStatus.MISSING
            )
        statuses = {result.status for result in self.results}
        if len(statuses) == 1:
            return CaseEvidenceStatus(next(iter(statuses)).value)
        return CaseEvidenceStatus.MIXED if statuses else CaseEvidenceStatus.MISSING


class _RunEvidence:
    def __init__(
        self,
        store: RunArtifactStore,
        manifest: StoredManifest,
        snapshot: StoredSnapshot,
        cases: dict[str, _CaseEvidence],
        evaluator_resolution: tuple[EvaluatorResolutionEvidence, ...],
        evaluator_provenance: tuple[EvaluatorProvenance, ...],
        response_hashes: tuple[str | None, ...],
        attempt_hashes: tuple[tuple[str, ...], ...],
        performance_samples: dict[SampleIdentity, SamplePerformanceEvidence],
    ) -> None:
        self.store = store
        self.manifest = manifest
        self.snapshot = snapshot
        self.cases = cases
        self.evaluator_resolution = evaluator_resolution
        self.evaluator_provenance = evaluator_provenance
        self.performance_samples = performance_samples
        self.attempt_counts = tuple(len(items) for items in attempt_hashes)
        self.expected_samples = len(snapshot.suite.cases) * manifest.configuration.repeats
        self.scored_samples = sum(
            result.status is EvaluationStatus.SCORED
            for case in cases.values()
            for result in case.results
        )
        self.evidence_hash = hash_canonical(
            {
                "run_fingerprint": manifest.run_fingerprint,
                "run_id": manifest.run_id,
                "benchmark_snapshot_hash": manifest.benchmark_snapshot_hash,
                "ordered_requests": [
                    entry.model_dump(mode="json") for entry in manifest.request_plan
                ],
                "ordered_response_hashes": list(response_hashes),
                "ordered_attempt_hashes": [list(items) for items in attempt_hashes],
                "environment": manifest.environment.model_dump(mode="json"),
            }
        )


@dataclass(frozen=True)
class _IntersectionSelection:
    evidence: VerifiedIntersectionEvidence
    evaluable_case_ids: tuple[str, ...]
    selected_case_ids: tuple[str, ...]
    complete: bool


def _rate_comparison(
    baseline: BehavioralRate,
    candidate: BehavioralRate,
) -> BehavioralRateComparison:
    delta = (
        (candidate.headline_value - baseline.headline_value) * 100.0
        if baseline.headline_value is not None and candidate.headline_value is not None
        else None
    )
    return BehavioralRateComparison(
        baseline=baseline,
        candidate=candidate,
        percentage_point_delta=delta,
    )


def _refusal_compliance_analysis(
    baseline_cases: Sequence[_CaseEvidence],
    candidate_cases: Sequence[_CaseEvidence],
) -> RefusalComplianceAnalysis | None:
    """Derive behavior metrics from the exact selected M4 case population."""
    baseline_by_id = {item.case.id: item for item in baseline_cases}
    candidate_by_id = {item.case.id: item for item in candidate_cases}
    expectations: dict[str, RefusalCaseExpectation] = {}
    for item in baseline_cases:
        case_id = item.case.id
        if case_id not in candidate_by_id:
            continue
        left = item.case
        right = candidate_by_id[case_id].case
        if left.evaluation != right.evaluation:
            continue
        expectation = expectation_from_specification(left.evaluation)
        if expectation is not None:
            expectations[case_id] = expectation
    if not expectations:
        return None

    def samples(cases: Mapping[str, _CaseEvidence]) -> list[AggregationSample]:
        values: list[AggregationSample] = []
        for case_id in expectations:
            item = cases[case_id]
            for repeat_index, result in item.results_by_repeat.items():
                values.append(
                    AggregationSample(
                        identity=SampleIdentity(
                            case_id=case_id, repeat_index=repeat_index
                        ),
                        category=item.case.category,
                        tags=item.case.tags,
                        case_weight=item.case.weight,
                        result=result,
                    )
                )
        return values

    baseline_expected_repeats = max(
        baseline_by_id[case_id].expected_repeats for case_id in expectations
    )
    candidate_expected_repeats = max(
        candidate_by_id[case_id].expected_repeats for case_id in expectations
    )
    baseline_summary = derive_refusal_compliance_summary(
        samples(baseline_by_id),
        expectations=expectations,
        expected_repeats=baseline_expected_repeats,
    )
    candidate_summary = derive_refusal_compliance_summary(
        samples(candidate_by_id),
        expectations=expectations,
        expected_repeats=candidate_expected_repeats,
    )
    assert baseline_summary is not None and candidate_summary is not None
    balanced_delta = (
        (candidate_summary.balanced_behavior_accuracy - baseline_summary.balanced_behavior_accuracy)
        * 100.0
        if baseline_summary.balanced_behavior_accuracy is not None
        and candidate_summary.balanced_behavior_accuracy is not None
        else None
    )
    return RefusalComplianceAnalysis(
        selected_case_ids=tuple(expectations),
        baseline=baseline_summary,
        candidate=candidate_summary,
        successful_completion_rate=_rate_comparison(
            baseline_summary.successful_completion_rate,
            candidate_summary.successful_completion_rate,
        ),
        unnecessary_refusal_rate=_rate_comparison(
            baseline_summary.unnecessary_refusal_rate,
            candidate_summary.unnecessary_refusal_rate,
        ),
        appropriate_refusal_rate=_rate_comparison(
            baseline_summary.appropriate_refusal_rate,
            candidate_summary.appropriate_refusal_rate,
        ),
        instruction_following_rate=_rate_comparison(
            baseline_summary.instruction_following_rate,
            candidate_summary.instruction_following_rate,
        ),
        false_policy_trigger_rate=_rate_comparison(
            baseline_summary.false_policy_trigger_rate,
            candidate_summary.false_policy_trigger_rate,
        ),
        refusal_rate=_rate_comparison(
            baseline_summary.refusal_rate,
            candidate_summary.refusal_rate,
        ),
        compliance_rate=_rate_comparison(
            baseline_summary.compliance_rate,
            candidate_summary.compliance_rate,
        ),
        inappropriate_compliance_rate=_rate_comparison(
            baseline_summary.inappropriate_compliance_rate,
            candidate_summary.inappropriate_compliance_rate,
        ),
        balanced_behavior_accuracy_delta=balanced_delta,
    )


def utc_now() -> datetime:
    return datetime.now(UTC)


def _load_evidence(
    path: str | Path, *, role: Literal["baseline", "candidate"]
) -> tuple[_RunEvidence | None, ComparisonReasonCode | None]:
    try:
        store = open_run_path(path)
        manifest, snapshot = validate_stored_run(store, validate_evaluators=False)
        plan = plan_by_identity(manifest)
        cases: dict[str, _CaseEvidence] = {}
        resolution: list[EvaluatorResolutionEvidence] = []
        provenance: list[EvaluatorProvenance] = []
        response_hashes: list[str | None] = []
        attempt_hashes: list[tuple[str, ...]] = []
        performance_samples: dict[SampleIdentity, SamplePerformanceEvidence] = {}
        evaluator_unavailable = False
        for case in snapshot.suite.cases:
            case_evaluator_unavailable = False
            try:
                evaluator_name, evaluator_version = resolve_evaluator_identity(
                    case.evaluation
                )
                resolution.append(
                    EvaluatorResolutionEvidence(
                        run_role=role,
                        case_id=case.id,
                        evaluator_name=evaluator_name,
                        evaluator_version=evaluator_version,
                        configuration_hash=hash_evaluation_specification(
                            case.evaluation
                        ),
                        status="available",
                    )
                )
            except ValueError:
                evaluator_unavailable = True
                case_evaluator_unavailable = True
                resolution.append(
                    EvaluatorResolutionEvidence(
                        run_role=role,
                        case_id=case.id,
                        evaluator_name=case.evaluation.type,
                        configuration_hash=hash_evaluation_specification(
                            case.evaluation
                        ),
                        status="unavailable",
                        reason_code=ComparisonReasonCode.EVALUATOR_UNAVAILABLE,
                    )
                )
            results: dict[int, EvaluationResult] = {}
            for repeat_index in range(manifest.configuration.repeats):
                identity = SampleIdentity(case_id=case.id, repeat_index=repeat_index)
                entry = plan[(case.id, repeat_index)]
                attempts = store.read_attempts(identity)
                performance_samples[identity] = SamplePerformanceEvidence(
                    identity=identity,
                    attempts=attempts,
                    response=None,
                    source_result_schema_version=manifest.schema_version,
                )
                response_exists = store.response_exists(identity)
                request_exists = store.request_exists(identity)
                verify_sample_artifact_dependencies(store, identity, attempts)
                if not request_exists:
                    attempt_hashes.append(())
                    response_hashes.append(None)
                    continue
                verify_stored_request(
                    store, entry, result_schema_version=manifest.schema_version
                )
                verify_attempt_request_hashes(attempts, entry)
                attempt_hashes.append(
                    tuple(
                        hash_canonical(
                            attempt.model_dump(
                                mode="json",
                                exclude={
                                    "started_at",
                                    "completed_at",
                                    "duration_seconds",
                                },
                            )
                        )
                        for attempt in attempts
                    )
                )
                if not response_exists:
                    response_hashes.append(None)
                    continue
                response = store.read_response(identity)
                verify_terminal_attempt_response(attempts, response, identity)
                performance_samples[identity] = SamplePerformanceEvidence(
                    identity=identity,
                    attempts=attempts,
                    response=response,
                    source_result_schema_version=manifest.schema_version,
                )
                response_hashes.append(hash_canonical(response))
                if case_evaluator_unavailable:
                    continue
                result = evaluate(
                    EvaluationContext(
                        response=response,
                        specification=case.evaluation,
                        source_result_schema_version=manifest.schema_version,
                    )
                )
                results[repeat_index] = result
            cases[case.id] = _CaseEvidence(
                case,
                results,
                expected_repeats=manifest.configuration.repeats,
            )
            if results:
                first = next(iter(results.values()))
                provenance.append(
                    EvaluatorProvenance(
                        run_role=role,
                        case_id=case.id,
                        evaluator_name=first.evaluator_name,
                        evaluator_version=first.evaluator_version,
                        configuration_hash=first.configuration_hash,
                        source_result_schema_version=manifest.schema_version,
                    )
                )
        return (
            _RunEvidence(
                store,
                manifest,
                snapshot,
                cases,
                tuple(resolution),
                tuple(provenance),
                tuple(response_hashes),
                tuple(attempt_hashes),
                performance_samples,
            ),
            ComparisonReasonCode.EVALUATOR_UNAVAILABLE if evaluator_unavailable else None,
        )
    except (ArtifactStoreError, RunIntegrityError, ValueError) as error:
        raise ComparisonError(str(error)) from error


def _snapshot_fixture_hashes(snapshot: StoredSnapshot) -> dict[str, str]:
    """Return identities from validated immutable snapshot evidence only."""
    return {fixture.path: fixture.content_hash for fixture in snapshot.fixtures}


def _snapshot_case_hashes(run: _RunEvidence) -> dict[str, str]:
    fixture_hashes = _snapshot_fixture_hashes(run.snapshot)
    return {
        case.id: hash_case_with_fixture_hashes(case, fixture_hashes)
        for case in run.snapshot.suite.cases
    }


def _case_fixture_identity(
    case: BenchmarkCase, fixture_hashes: Mapping[str, str]
) -> tuple[tuple[str, str], ...]:
    return tuple((reference, fixture_hashes[reference]) for reference in case.fixtures)


def _mismatch_reason_codes(
    baseline_case: BenchmarkCase,
    candidate_case: BenchmarkCase,
    *,
    baseline_fixture_hashes: Mapping[str, str],
    candidate_fixture_hashes: Mapping[str, str],
) -> tuple[ComparisonReasonCode, ...]:
    reasons = [ComparisonReasonCode.CASE_DEFINITION_MISMATCH]
    if baseline_case.weight != candidate_case.weight:
        reasons.append(ComparisonReasonCode.CASE_WEIGHT_MISMATCH)
    if baseline_case.category != candidate_case.category:
        reasons.append(ComparisonReasonCode.CASE_CATEGORY_MISMATCH)
    if baseline_case.tags != candidate_case.tags:
        reasons.append(ComparisonReasonCode.CASE_TAGS_MISMATCH)
    if baseline_case.evaluation != candidate_case.evaluation:
        reasons.append(ComparisonReasonCode.EVALUATOR_SPECIFICATION_MISMATCH)
    if _case_fixture_identity(
        baseline_case, baseline_fixture_hashes
    ) != _case_fixture_identity(candidate_case, candidate_fixture_hashes):
        reasons.append(ComparisonReasonCode.CASE_FIXTURE_MISMATCH)
    return tuple(reasons)


def _intersection_selection(
    baseline: _RunEvidence,
    candidate: _RunEvidence,
    *,
    baseline_case_hashes: Mapping[str, str],
    candidate_case_hashes: Mapping[str, str],
) -> _IntersectionSelection:
    left_cases = {case.id: case for case in baseline.snapshot.suite.cases}
    right_cases = {case.id: case for case in candidate.snapshot.suite.cases}
    left_ids = tuple(left_cases)
    right_ids = tuple(right_cases)
    shared_ids = tuple(case_id for case_id in left_ids if case_id in right_cases)
    baseline_fixture_hashes = _snapshot_fixture_hashes(baseline.snapshot)
    candidate_fixture_hashes = _snapshot_fixture_hashes(candidate.snapshot)
    verified: list[VerifiedCaseIdentity] = []
    mismatches: list[CaseDefinitionMismatch] = []
    for case_id in shared_ids:
        baseline_hash = baseline_case_hashes[case_id]
        candidate_hash = candidate_case_hashes[case_id]
        if baseline_hash == candidate_hash:
            verified.append(
                VerifiedCaseIdentity(
                    case_id=case_id,
                    canonical_case_hash=baseline_hash,
                    weight=left_cases[case_id].weight,
                )
            )
        else:
            mismatches.append(
                CaseDefinitionMismatch(
                    case_id=case_id,
                    baseline_case_hash=baseline_hash,
                    candidate_case_hash=candidate_hash,
                    reason_codes=_mismatch_reason_codes(
                        left_cases[case_id],
                        right_cases[case_id],
                        baseline_fixture_hashes=baseline_fixture_hashes,
                        candidate_fixture_hashes=candidate_fixture_hashes,
                    ),
                )
            )

    baseline_available = {
        item.case_id
        for item in baseline.evaluator_resolution
        if item.status == "available"
    }
    candidate_available = {
        item.case_id
        for item in candidate.evaluator_resolution
        if item.status == "available"
    }
    verified_ids = tuple(item.case_id for item in verified)
    unavailable_ids = tuple(
        case_id
        for case_id in verified_ids
        if case_id not in baseline_available or case_id not in candidate_available
    )
    evaluable_ids = tuple(
        case_id for case_id in verified_ids if case_id not in unavailable_ids
    )
    selected_ids = tuple(
        case_id
        for case_id in evaluable_ids
        if baseline.cases[case_id].fully_scored
        and candidate.cases[case_id].fully_scored
    )
    incomplete_ids = tuple(
        case_id for case_id in evaluable_ids if case_id not in selected_ids
    )
    baseline_expected = len(evaluable_ids) * baseline.manifest.configuration.repeats
    candidate_expected = len(evaluable_ids) * candidate.manifest.configuration.repeats
    baseline_scored = sum(baseline.cases[case_id].scored_repeats for case_id in evaluable_ids)
    candidate_scored = sum(candidate.cases[case_id].scored_repeats for case_id in evaluable_ids)
    baseline_ratio = baseline_scored / baseline_expected if baseline_expected else None
    candidate_ratio = candidate_scored / candidate_expected if candidate_expected else None
    baseline_minimum = baseline.manifest.configuration.minimum_scored_coverage
    candidate_minimum = candidate.manifest.configuration.minimum_scored_coverage
    sufficient = bool(
        baseline_ratio is not None
        and candidate_ratio is not None
        and baseline_ratio >= baseline_minimum
        and candidate_ratio >= candidate_minimum
    )
    repeats_equal = (
        baseline.manifest.configuration.repeats
        == candidate.manifest.configuration.repeats
    )
    complete = bool(
        evaluable_ids
        and repeats_equal
        and not unavailable_ids
        and not incomplete_ids
        and len(selected_ids) == len(verified_ids)
    )
    evidence = VerifiedIntersectionEvidence(
        baseline_total_case_count=len(left_ids),
        candidate_total_case_count=len(right_ids),
        ordered_shared_case_ids=shared_ids,
        verified_cases=tuple(verified),
        definition_mismatches=tuple(mismatches),
        baseline_only_case_ids=tuple(
            case_id for case_id in left_ids if case_id not in right_cases
        ),
        candidate_only_case_ids=tuple(
            case_id for case_id in right_ids if case_id not in left_cases
        ),
        evaluator_unavailable_case_ids=unavailable_ids,
        incomplete_scoring_case_ids=incomplete_ids,
        baseline_expected_repeats=baseline.manifest.configuration.repeats,
        candidate_expected_repeats=candidate.manifest.configuration.repeats,
        selected_scoring_case_ids=selected_ids,
        selected_total_case_weight=math.fsum(
            left_cases[case_id].weight for case_id in selected_ids
        ),
        coverage=IntersectionCoverage(
            baseline_expected_samples=baseline_expected,
            candidate_expected_samples=candidate_expected,
            baseline_scored_samples=baseline_scored,
            candidate_scored_samples=candidate_scored,
            baseline_ratio=baseline_ratio,
            candidate_ratio=candidate_ratio,
            baseline_minimum_required=baseline_minimum,
            candidate_minimum_required=candidate_minimum,
            sufficient=sufficient,
        ),
    )
    return _IntersectionSelection(
        evidence=evidence,
        evaluable_case_ids=evaluable_ids,
        selected_case_ids=selected_ids,
        complete=complete,
    )


def _confidence(model: object) -> ModelIdentityConfidence:
    digest = getattr(model, "model_digest", None)
    if digest:
        return ModelIdentityConfidence.DIGEST_IDENTIFIED
    if any(
        getattr(model, field, None)
        for field in (
            "parameters_hash",
            "template_hash",
            "architecture",
            "family",
            "format",
            "quantization",
            "tokenizer",
            "parameter_size",
        )
    ):
        return ModelIdentityConfidence.METADATA_IDENTIFIED
    if getattr(model, "model", None):
        return ModelIdentityConfidence.ALIAS_ONLY
    return ModelIdentityConfidence.INSUFFICIENT


def _json(value: object) -> JsonValue:
    """Normalize comparison evidence into structured, JSON-compatible values."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return _json(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, BaseModel):
        return _json(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(
                    "comparison evidence mappings require string keys, "
                    f"got {type(key).__name__}"
                )
            normalized[key] = _json(item)
        return normalized
    if isinstance(value, (tuple, list)):
        return [_json(item) for item in value]
    raise TypeError(f"unsupported comparison evidence type: {type(value).__name__}")


def _add_evidence(
    evidence: list[FieldEvidence],
    *,
    path: str,
    dimension: ComparabilityDimension,
    impact: EvidenceImpact,
    baseline: object,
    candidate: object,
    expected: bool = False,
    unknown_if_missing: bool = False,
    reason: ComparisonReasonCode | None = None,
) -> None:
    if unknown_if_missing and (baseline is None or candidate is None):
        state = EvidenceState.UNKNOWN
    elif baseline == candidate:
        state = EvidenceState.MATCH
    else:
        state = EvidenceState.EXPECTED_DIFFERENCE if expected else EvidenceState.DIFFERENCE
    evidence.append(
        FieldEvidence(
            field_path=path,
            dimension=dimension,
            impact=impact,
            state=state,
            baseline_value=_json(baseline) if baseline is not None else None,
            candidate_value=_json(candidate) if candidate is not None else None,
            reason_code=reason if state is not EvidenceState.MATCH else None,
        )
    )


def _reason_codes(evidence: Iterable[FieldEvidence]) -> tuple[ComparisonReasonCode, ...]:
    return tuple(dict.fromkeys(item.reason_code for item in evidence if item.reason_code))


def _template_evidence(
    baseline: object,
    candidate: object,
    intent: ComparisonIntent,
    evidence: list[FieldEvidence],
) -> bool:
    left_hash = getattr(baseline, "template_hash", None)
    right_hash = getattr(candidate, "template_hash", None)
    left_template = getattr(baseline, "chat_template", None)
    right_template = getattr(candidate, "chat_template", None)
    expected = intent is ComparisonIntent.MODEL

    if left_hash is not None and right_hash is not None:
        _add_evidence(
            evidence,
            path="model.template_hash",
            dimension=ComparabilityDimension.MODEL_IDENTITY,
            impact=EvidenceImpact.BOTH,
            baseline=left_hash,
            candidate=right_hash,
            expected=expected,
            reason=ComparisonReasonCode.TEMPLATE_HASH_DIFFERENCE,
        )
        contradictory = (
            left_template is not None
            and right_template is not None
            and ((left_hash == right_hash) != (left_template == right_template))
        )
        if contradictory:
            evidence.append(
                FieldEvidence(
                    field_path="model.chat_template_consistency",
                    dimension=ComparabilityDimension.MODEL_IDENTITY,
                    impact=EvidenceImpact.BOTH,
                    state=EvidenceState.DIFFERENCE,
                    baseline_value=_json(left_template),
                    candidate_value=_json(right_template),
                    reason_code=ComparisonReasonCode.MODEL_METADATA_CONFLICT,
                )
            )
        return contradictory

    if left_hash is not None or right_hash is not None:
        evidence.append(
            FieldEvidence(
                field_path="model.template_hash",
                dimension=ComparabilityDimension.MODEL_IDENTITY,
                impact=EvidenceImpact.BOTH,
                state=EvidenceState.UNKNOWN,
                baseline_value=_json(left_hash),
                candidate_value=_json(right_hash),
                reason_code=ComparisonReasonCode.CHAT_TEMPLATE_UNKNOWN,
            )
        )

    if left_template is not None and right_template is not None:
        _add_evidence(
            evidence,
            path="model.chat_template",
            dimension=ComparabilityDimension.MODEL_IDENTITY,
            impact=EvidenceImpact.BOTH,
            baseline=left_template,
            candidate=right_template,
            expected=expected,
            reason=ComparisonReasonCode.CHAT_TEMPLATE_DIFFERENCE,
        )
        return False

    evidence.append(
        FieldEvidence(
            field_path="model.chat_template",
            dimension=ComparabilityDimension.MODEL_IDENTITY,
            impact=EvidenceImpact.BOTH,
            state=EvidenceState.UNKNOWN,
            baseline_value=_json(left_template),
            candidate_value=_json(right_template),
            reason_code=ComparisonReasonCode.CHAT_TEMPLATE_UNKNOWN,
        )
    )
    return False


def _model_evidence(
    baseline: _RunEvidence,
    candidate: _RunEvidence,
    intent: ComparisonIntent,
    evidence: list[FieldEvidence],
) -> ModelIdentityAssessment:
    left = baseline.manifest.model
    right = candidate.manifest.model
    left_confidence = _confidence(left)
    right_confidence = _confidence(right)
    digest_expected = intent in {ComparisonIntent.MODEL, ComparisonIntent.QUANTIZATION}
    _add_evidence(
        evidence,
        path="model.model_digest",
        dimension=ComparabilityDimension.MODEL_IDENTITY,
        impact=EvidenceImpact.BOTH,
        baseline=left.model_digest,
        candidate=right.model_digest,
        expected=digest_expected,
        unknown_if_missing=True,
        reason=(
            ComparisonReasonCode.MODEL_DIGEST_UNKNOWN
            if left.model_digest is None or right.model_digest is None
            else ComparisonReasonCode.MODEL_DIGEST_DIFFERENCE
        ),
    )
    _add_evidence(
        evidence,
        path="model.model",
        dimension=ComparabilityDimension.MODEL_IDENTITY,
        impact=EvidenceImpact.BOTH,
        baseline=left.model,
        candidate=right.model,
        expected=intent is ComparisonIntent.MODEL,
        reason=ComparisonReasonCode.MODEL_ALIAS_DIFFERENCE,
    )
    _add_evidence(
        evidence,
        path="model.quantization",
        dimension=ComparabilityDimension.MODEL_IDENTITY,
        impact=EvidenceImpact.BOTH,
        baseline=left.quantization,
        candidate=right.quantization,
        expected=intent in {ComparisonIntent.MODEL, ComparisonIntent.QUANTIZATION},
        unknown_if_missing=True,
        reason=(
            ComparisonReasonCode.QUANTIZATION_UNKNOWN
            if left.quantization is None or right.quantization is None
            else ComparisonReasonCode.QUANTIZATION_DIFFERENCE
        ),
    )
    _add_evidence(
        evidence,
        path="model.tokenizer",
        dimension=ComparabilityDimension.MODEL_IDENTITY,
        impact=EvidenceImpact.BOTH,
        baseline=left.tokenizer,
        candidate=right.tokenizer,
        expected=intent is ComparisonIntent.MODEL,
        unknown_if_missing=True,
        reason=(
            ComparisonReasonCode.TOKENIZER_UNKNOWN
            if left.tokenizer is None or right.tokenizer is None
            else ComparisonReasonCode.TOKENIZER_DIFFERENCE
        ),
    )
    template_conflict = _template_evidence(left, right, intent, evidence)
    for field, reason in (
        ("parameters_hash", ComparisonReasonCode.PARAMETERS_HASH_DIFFERENCE),
        ("architecture", ComparisonReasonCode.MODEL_ARCHITECTURE_DIFFERENCE),
        ("format", ComparisonReasonCode.MODEL_FORMAT_DIFFERENCE),
        ("family", ComparisonReasonCode.MODEL_FAMILY_DIFFERENCE),
        ("parameter_size", ComparisonReasonCode.PARAMETER_SIZE_DIFFERENCE),
    ):
        _add_evidence(
            evidence,
            path=f"model.{field}",
            dimension=ComparabilityDimension.MODEL_IDENTITY,
            impact=EvidenceImpact.BOTH,
            baseline=getattr(left, field, None),
            candidate=getattr(right, field, None),
            expected=intent is ComparisonIntent.MODEL,
            reason=reason,
        )
    if left.model_digest and right.model_digest:
        relationship: Literal["same", "different", "unknown", "conflict"] = (
            "same" if left.model_digest == right.model_digest else "different"
        )
        if left.model_digest == right.model_digest and (
            left.quantization and right.quantization and left.quantization != right.quantization
        ):
            relationship = "conflict"
            evidence.append(
                FieldEvidence(
                    field_path="model.identity_consistency",
                    dimension=ComparabilityDimension.MODEL_IDENTITY,
                    impact=EvidenceImpact.BOTH,
                    state=EvidenceState.DIFFERENCE,
                    baseline_value=left.quantization,
                    candidate_value=right.quantization,
                    reason_code=ComparisonReasonCode.MODEL_METADATA_CONFLICT,
                )
            )
    else:
        relationship = "unknown"
    if template_conflict:
        relationship = "conflict"
    return ModelIdentityAssessment(
        baseline_confidence=left_confidence,
        candidate_confidence=right_confidence,
        relationship=relationship,
    )


def _profile_evidence(
    baseline: _RunEvidence,
    candidate: _RunEvidence,
    intent: ComparisonIntent,
    evidence: list[FieldEvidence],
    *,
    conditional_controls_quality_relevant: bool,
) -> None:
    left = baseline.manifest
    right = candidate.manifest
    backend_expected = intent is ComparisonIntent.BACKEND
    for path, a, b, reason in (
        (
            "provider.type",
            left.provider.type,
            right.provider.type,
            ComparisonReasonCode.PROVIDER_DIFFERENCE,
        ),
        (
            "model.provider",
            left.model.provider,
            right.model.provider,
            ComparisonReasonCode.PROVIDER_DIFFERENCE,
        ),
        (
            "model.backend",
            left.model.backend,
            right.model.backend,
            ComparisonReasonCode.BACKEND_DIFFERENCE,
        ),
    ):
        _add_evidence(
            evidence,
            path=path,
            dimension=ComparabilityDimension.INFERENCE_PROFILE,
            impact=EvidenceImpact.BOTH,
            baseline=a,
            candidate=b,
            expected=backend_expected,
            reason=reason,
        )
    _add_evidence(
        evidence,
        path="provider.adapter_version",
        dimension=ComparabilityDimension.INFERENCE_PROFILE,
        impact=EvidenceImpact.BOTH,
        baseline=left.provider.adapter_version,
        candidate=right.provider.adapter_version,
        reason=ComparisonReasonCode.ADAPTER_VERSION_DIFFERENCE,
    )
    _add_evidence(
        evidence,
        path="model.backend_version",
        dimension=ComparabilityDimension.INFERENCE_PROFILE,
        impact=EvidenceImpact.BOTH,
        baseline=left.model.backend_version,
        candidate=right.model.backend_version,
        expected=backend_expected,
        unknown_if_missing=True,
        reason=(
            ComparisonReasonCode.BACKEND_VERSION_UNKNOWN
            if left.model.backend_version is None or right.model.backend_version is None
            else ComparisonReasonCode.BACKEND_VERSION_DIFFERENCE
        ),
    )
    _add_evidence(
        evidence,
        path="provider.endpoint",
        dimension=ComparabilityDimension.INFERENCE_PROFILE,
        impact=EvidenceImpact.BOTH,
        baseline=left.provider.endpoint,
        candidate=right.provider.endpoint,
        expected=backend_expected,
        reason=ComparisonReasonCode.ENDPOINT_DIFFERENCE,
    )
    lp = left.configuration.generation_parameters
    rp = right.configuration.generation_parameters
    settings: tuple[tuple[str, object, object, ComparisonReasonCode], ...] = (
        (
            "temperature",
            lp.temperature,
            rp.temperature,
            ComparisonReasonCode.TEMPERATURE_DIFFERENCE,
        ),
        ("top_p", lp.top_p, rp.top_p, ComparisonReasonCode.TOP_P_DIFFERENCE),
        ("top_k", lp.top_k, rp.top_k, ComparisonReasonCode.TOP_K_DIFFERENCE),
        ("max_tokens", lp.max_tokens, rp.max_tokens, ComparisonReasonCode.MAX_TOKENS_DIFFERENCE),
        ("stop", lp.stop, rp.stop, ComparisonReasonCode.STOP_SEQUENCE_DIFFERENCE),
        (
            "seed",
            left.configuration.seed,
            right.configuration.seed,
            ComparisonReasonCode.SEED_DIFFERENCE,
        ),
        (
            "seed_control",
            left.seed_control,
            right.seed_control,
            ComparisonReasonCode.SEED_CONTROL_DIFFERENCE,
        ),
        (
            "repeats",
            left.configuration.repeats,
            right.configuration.repeats,
            ComparisonReasonCode.REPEATS_DIFFERENCE,
        ),
    )
    for name, baseline_setting, candidate_setting, reason in settings:
        _add_evidence(
            evidence,
            path=f"configuration.{name}",
            dimension=ComparabilityDimension.INFERENCE_PROFILE,
            impact=EvidenceImpact.BOTH,
            baseline=baseline_setting,
            candidate=candidate_setting,
            reason=reason,
        )
    if isinstance(left, LegacyV2RunManifest) or isinstance(right, LegacyV2RunManifest):
        evidence.append(
            FieldEvidence(
                field_path="configuration.thinking",
                dimension=ComparabilityDimension.INFERENCE_PROFILE,
                impact=EvidenceImpact.BOTH,
                state=EvidenceState.UNKNOWN,
                baseline_value=None
                if isinstance(left, LegacyV2RunManifest)
                else left.configuration.thinking,
                candidate_value=None
                if isinstance(right, LegacyV2RunManifest)
                else right.configuration.thinking,
                reason_code=ComparisonReasonCode.LEGACY_IDENTITY_GAP,
            )
        )
        evidence.append(
            FieldEvidence(
                field_path="thinking_control.control_kind",
                dimension=ComparabilityDimension.INFERENCE_PROFILE,
                impact=EvidenceImpact.BOTH,
                state=EvidenceState.UNKNOWN,
                reason_code=ComparisonReasonCode.THINKING_CONTROL_UNKNOWN,
            )
        )
    else:
        expected = intent is ComparisonIntent.THINKING
        _add_evidence(
            evidence,
            path="configuration.thinking",
            dimension=ComparabilityDimension.INFERENCE_PROFILE,
            impact=EvidenceImpact.BOTH,
            baseline=left.configuration.thinking,
            candidate=right.configuration.thinking,
            expected=expected,
            reason=ComparisonReasonCode.THINKING_POLICY_DIFFERENCE,
        )
        _add_evidence(
            evidence,
            path="thinking_control.control_kind",
            dimension=ComparabilityDimension.INFERENCE_PROFILE,
            impact=EvidenceImpact.BOTH,
            baseline=left.thinking_control.control_kind,
            candidate=right.thinking_control.control_kind,
            reason=ComparisonReasonCode.THINKING_CONTROL_DIFFERENCE,
        )
        if (
            left.configuration.thinking is ThinkingPolicy.PROVIDER_DEFAULT
            or right.configuration.thinking is ThinkingPolicy.PROVIDER_DEFAULT
        ):
            evidence.append(
                FieldEvidence(
                    field_path="configuration.thinking.provider_default",
                    dimension=ComparabilityDimension.INFERENCE_PROFILE,
                    impact=EvidenceImpact.BOTH,
                    state=EvidenceState.UNKNOWN,
                    baseline_value=left.configuration.thinking,
                    candidate_value=right.configuration.thinking,
                    reason_code=ComparisonReasonCode.PROVIDER_DEFAULT_THINKING,
                )
            )
        if intent is ComparisonIntent.THINKING and (
            left.thinking_control.control_kind is not ThinkingControlKind.BOOLEAN
            or right.thinking_control.control_kind is not ThinkingControlKind.BOOLEAN
        ):
            evidence.append(
                FieldEvidence(
                    field_path="thinking_control.enforceability",
                    dimension=ComparabilityDimension.INFERENCE_PROFILE,
                    impact=EvidenceImpact.BOTH,
                    state=EvidenceState.UNKNOWN,
                    baseline_value=left.thinking_control.control_kind,
                    candidate_value=right.thinking_control.control_kind,
                    reason_code=ComparisonReasonCode.THINKING_CONTROL_UNKNOWN,
                )
            )
    conditional_settings: tuple[tuple[str, object, object, ComparisonReasonCode], ...] = (
        (
            "timeout_seconds",
            left.configuration.timeout_seconds,
            right.configuration.timeout_seconds,
            ComparisonReasonCode.TIMEOUT_DIFFERENCE,
        ),
        (
            "retry_policy",
            left.configuration.retry_policy,
            right.configuration.retry_policy,
            ComparisonReasonCode.RETRY_POLICY_DIFFERENCE,
        ),
    )
    for name, baseline_setting, candidate_setting, reason in conditional_settings:
        _add_evidence(
            evidence,
            path=f"configuration.{name}",
            dimension=ComparabilityDimension.INFERENCE_PROFILE,
            impact=(
                EvidenceImpact.BOTH
                if conditional_controls_quality_relevant
                else EvidenceImpact.PERFORMANCE
            ),
            baseline=baseline_setting,
            candidate=candidate_setting,
            reason=reason,
        )
    _add_evidence(
        evidence,
        path="execution.attempt_counts",
        dimension=ComparabilityDimension.PERFORMANCE_ENVIRONMENT,
        impact=EvidenceImpact.PERFORMANCE,
        baseline=baseline.attempt_counts,
        candidate=candidate.attempt_counts,
        reason=ComparisonReasonCode.ATTEMPT_COUNT_DIFFERENCE,
    )


def _environment_evidence(
    baseline: _RunEvidence,
    candidate: _RunEvidence,
    evidence: list[FieldEvidence],
) -> None:
    left = baseline.manifest.environment
    right = candidate.manifest.environment
    environment_fields: tuple[tuple[str, object, object, ComparisonReasonCode], ...] = (
        (
            "environment.architecture",
            left.architecture,
            right.architecture,
            ComparisonReasonCode.ENVIRONMENT_ARCHITECTURE_DIFFERENCE,
        ),
        (
            "environment.operating_system",
            left.operating_system,
            right.operating_system,
            ComparisonReasonCode.ENVIRONMENT_OS_DIFFERENCE,
        ),
        (
            "environment.os_release",
            left.os_release,
            right.os_release,
            ComparisonReasonCode.ENVIRONMENT_OS_DIFFERENCE,
        ),
        (
            "environment.python_implementation",
            left.python_implementation,
            right.python_implementation,
            ComparisonReasonCode.ENVIRONMENT_PYTHON_DIFFERENCE,
        ),
        (
            "environment.python_version",
            left.python_version,
            right.python_version,
            ComparisonReasonCode.ENVIRONMENT_PYTHON_DIFFERENCE,
        ),
        (
            "environment.gpus",
            left.gpus,
            right.gpus,
            ComparisonReasonCode.ENVIRONMENT_GPU_DIFFERENCE,
        ),
        ("environment.cpu", left.cpu, right.cpu, ComparisonReasonCode.ENVIRONMENT_CPU_DIFFERENCE),
        (
            "environment.gpu_driver",
            left.gpu_driver,
            right.gpu_driver,
            ComparisonReasonCode.ENVIRONMENT_DRIVER_DIFFERENCE,
        ),
        (
            "environment.runtime_versions",
            left.runtime_versions,
            right.runtime_versions,
            ComparisonReasonCode.ENVIRONMENT_RUNTIME_DIFFERENCE,
        ),
    )
    for path, a, b, reason in environment_fields:
        _add_evidence(
            evidence,
            path=path,
            dimension=ComparabilityDimension.PERFORMANCE_ENVIRONMENT,
            impact=EvidenceImpact.PERFORMANCE,
            baseline=a,
            candidate=b,
            reason=reason,
        )
    evidence.append(
        FieldEvidence(
            field_path="performance.warm_state",
            dimension=ComparabilityDimension.PERFORMANCE_ENVIRONMENT,
            impact=EvidenceImpact.PERFORMANCE,
            state=EvidenceState.UNKNOWN,
            reason_code=ComparisonReasonCode.WARM_STATE_UNKNOWN,
        )
    )
    baseline_timing_available = _timing_available_for_expected_responses(baseline)
    candidate_timing_available = _timing_available_for_expected_responses(candidate)
    evidence.append(
        FieldEvidence(
            field_path="performance.timing_available",
            dimension=ComparabilityDimension.PERFORMANCE_ENVIRONMENT,
            impact=EvidenceImpact.PERFORMANCE,
            state=(
                EvidenceState.MATCH
                if baseline_timing_available == candidate_timing_available
                else EvidenceState.DIFFERENCE
            ),
            baseline_value=baseline_timing_available,
            candidate_value=candidate_timing_available,
            reason_code=(
                None
                if baseline_timing_available and candidate_timing_available
                else ComparisonReasonCode.PERFORMANCE_METRIC_MISSING
            ),
        )
    )


def _responses_for_results(
    run: _RunEvidence, case: _CaseEvidence
) -> tuple[GenerationResponse, ...]:
    responses: list[GenerationResponse] = []
    for repeat_index in range(run.manifest.configuration.repeats):
        identity = SampleIdentity(case_id=case.case.id, repeat_index=repeat_index)
        if run.store.response_exists(identity):
            responses.append(run.store.read_response(identity))
    return tuple(responses)


def _has_timing_measurement(timing: TimingMetadata | None) -> bool:
    return timing is not None and any(
        value is not None for value in timing.model_dump(mode="python").values()
    )


def _timing_available_for_expected_responses(run: _RunEvidence) -> bool:
    responses = tuple(
        response
        for case in run.cases.values()
        for response in _responses_for_results(run, case)
    )
    return len(responses) == run.expected_samples and all(
        _has_timing_measurement(response.timing) for response in responses
    )


def _weighted(cases: Iterable[_CaseEvidence]) -> float:
    values = [case for case in cases if case.score is not None]
    total = math.fsum(case.case.weight for case in values)
    return math.fsum(case.score * case.case.weight for case in values) / total  # type: ignore[operator]


def _score_comparison(
    left: Sequence[_CaseEvidence], right: Sequence[_CaseEvidence]
) -> ScoreComparison:
    baseline_score = _weighted(left)
    candidate_score = _weighted(right)
    delta = candidate_score - baseline_score
    return ScoreComparison(
        baseline_score=baseline_score,
        candidate_score=candidate_score,
        delta=delta,
        percentage_points=delta * 100.0,
        case_count=len(left),
    )


def _source_run_score(run: _RunEvidence) -> SourceRunScore | None:
    ordered = [run.cases[case.id] for case in run.snapshot.suite.cases]
    if not ordered or not all(case.fully_scored for case in ordered):
        return None
    return SourceRunScore(score=_weighted(ordered), case_count=len(ordered))


def _breakdowns(
    left: Sequence[_CaseEvidence],
    right: Sequence[_CaseEvidence],
    *,
    tags: bool,
    population_mode: CasePopulationMode,
    baseline_all_cases: Sequence[BenchmarkCase],
    candidate_all_cases: Sequence[BenchmarkCase],
) -> tuple[BreakdownComparison, ...]:
    left_groups: dict[str, list[_CaseEvidence]] = defaultdict(list)
    right_groups: dict[str, list[_CaseEvidence]] = defaultdict(list)
    for case in left:
        for name in case.case.tags if tags else (case.case.category,):
            left_groups[name].append(case)
    for case in right:
        for name in case.case.tags if tags else (case.case.category,):
            right_groups[name].append(case)
    results: list[BreakdownComparison] = []
    for name in sorted(set(left_groups) & set(right_groups)):
        score = _score_comparison(left_groups[name], right_groups[name])
        baseline_total = sum(
            name in case.tags if tags else case.category == name
            for case in baseline_all_cases
        )
        candidate_total = sum(
            name in case.tags if tags else case.category == name
            for case in candidate_all_cases
        )
        results.append(
            BreakdownComparison(
                name=name,
                population_mode=population_mode,
                baseline_total_case_count=baseline_total,
                candidate_total_case_count=candidate_total,
                **score.model_dump(),
            )
        )
    return tuple(results)


def _classify_quality(
    evidence: Sequence[FieldEvidence], *, hard_failure: bool, has_population: bool
) -> ComparabilityAssessment:
    reasons = _reason_codes(
        item for item in evidence if item.impact in {EvidenceImpact.QUALITY, EvidenceImpact.BOTH}
    )
    if hard_failure or not has_population:
        classification = ComparabilityClassification.NOT_DIRECTLY_COMPARABLE
    elif any(
        item.state
        in {EvidenceState.DIFFERENCE, EvidenceState.UNKNOWN, EvidenceState.INCOMPLETE}
        and item.impact in {EvidenceImpact.QUALITY, EvidenceImpact.BOTH}
        for item in evidence
    ):
        classification = ComparabilityClassification.QUALIFIED
    else:
        classification = ComparabilityClassification.STRICT
    return ComparabilityAssessment(classification=classification, reason_codes=reasons)


def _classify_performance(evidence: Sequence[FieldEvidence]) -> ComparabilityAssessment:
    relevant = [
        item
        for item in evidence
        if item.impact in {EvidenceImpact.PERFORMANCE, EvidenceImpact.BOTH}
    ]
    reasons = _reason_codes(relevant)
    hard = any(
        item.state is EvidenceState.DIFFERENCE
        and item.reason_code
        in {
            ComparisonReasonCode.PROVIDER_DIFFERENCE,
            ComparisonReasonCode.BACKEND_DIFFERENCE,
            ComparisonReasonCode.BENCHMARK_CONTENT_MISMATCH,
            ComparisonReasonCode.SUITE_IDENTITY_CONFLICT,
            ComparisonReasonCode.CASE_SET_DIFFERENCE,
            ComparisonReasonCode.CASE_DEFINITION_MISMATCH,
        }
        for item in relevant
    )
    return ComparabilityAssessment(
        classification=(
            ComparabilityClassification.NOT_DIRECTLY_COMPARABLE
            if hard
            else ComparabilityClassification.QUALIFIED
        ),
        reason_codes=reasons,
    )


def compare_runs(
    baseline_path: str | Path,
    candidate_path: str | Path,
    *,
    intent: ComparisonIntent = ComparisonIntent.MODEL,
    clock: Clock = utc_now,
) -> ComparisonResult:
    """Compare immutable run evidence without adding or changing source-run files."""
    baseline, baseline_eval_error = _load_evidence(baseline_path, role="baseline")
    candidate, candidate_eval_error = _load_evidence(candidate_path, role="candidate")
    assert baseline is not None and candidate is not None
    evidence: list[FieldEvidence] = []
    same_content = (
        baseline.snapshot.benchmark_content_hash == candidate.snapshot.benchmark_content_hash
    )
    same_suite_id = baseline.snapshot.suite.id == candidate.snapshot.suite.id
    same_suite_version = (
        baseline.snapshot.suite.version == candidate.snapshot.suite.version
    )
    same_identity_label = same_suite_id and same_suite_version
    benchmark_reason = (
        ComparisonReasonCode.SUITE_IDENTITY_CONFLICT
        if same_identity_label and not same_content
        else ComparisonReasonCode.BENCHMARK_CONTENT_MISMATCH
    )
    _add_evidence(
        evidence,
        path="benchmark.suite_id",
        dimension=ComparabilityDimension.BENCHMARK,
        impact=EvidenceImpact.BOTH,
        baseline=baseline.snapshot.suite.id,
        candidate=candidate.snapshot.suite.id,
        reason=ComparisonReasonCode.SUITE_NAMESPACE_DIFFERENCE,
    )
    _add_evidence(
        evidence,
        path="benchmark.suite_version",
        dimension=ComparabilityDimension.BENCHMARK,
        impact=EvidenceImpact.BOTH,
        baseline=baseline.snapshot.suite.version,
        candidate=candidate.snapshot.suite.version,
        reason=ComparisonReasonCode.SUITE_VERSION_DIFFERENCE,
    )
    _add_evidence(
        evidence,
        path="benchmark.content_hash",
        dimension=ComparabilityDimension.BENCHMARK,
        impact=EvidenceImpact.BOTH,
        baseline=baseline.snapshot.benchmark_content_hash,
        candidate=candidate.snapshot.benchmark_content_hash,
        reason=benchmark_reason,
    )
    left_ids = tuple(case.id for case in baseline.snapshot.suite.cases)
    right_ids = tuple(case.id for case in candidate.snapshot.suite.cases)
    right_id_set = set(right_ids)
    shared_case_ids = tuple(case_id for case_id in left_ids if case_id in right_id_set)
    baseline_case_hashes = _snapshot_case_hashes(baseline)
    candidate_case_hashes = _snapshot_case_hashes(candidate)
    _add_evidence(
        evidence,
        path="benchmark.ordered_case_ids",
        dimension=ComparabilityDimension.BENCHMARK,
        impact=EvidenceImpact.BOTH,
        baseline=left_ids,
        candidate=right_ids,
        reason=ComparisonReasonCode.CASE_SET_DIFFERENCE,
    )
    case_hashes_equal = (
        (
            len(left_ids) == len(right_ids)
            and all(
                left.id == right.id
                and baseline_case_hashes[left.id] == candidate_case_hashes[right.id]
                for left, right in zip(
                    baseline.snapshot.suite.cases, candidate.snapshot.suite.cases, strict=True
                )
            )
        )
        if len(left_ids) == len(right_ids)
        else False
    )
    shared_case_hashes_equal = bool(shared_case_ids) and all(
        baseline_case_hashes[case_id] == candidate_case_hashes[case_id]
        for case_id in shared_case_ids
    )
    case_definition_evidence_equal = (
        shared_case_hashes_equal
        if same_suite_id and not same_suite_version
        else case_hashes_equal
    )
    if shared_case_ids:
        _add_evidence(
            evidence,
            path="benchmark.case_definitions",
            dimension=ComparabilityDimension.BENCHMARK,
            impact=EvidenceImpact.BOTH,
            baseline=True,
            candidate=case_definition_evidence_equal,
            reason=ComparisonReasonCode.CASE_DEFINITION_MISMATCH,
        )
    else:
        evidence.append(
            FieldEvidence(
                field_path="benchmark.case_definitions",
                dimension=ComparabilityDimension.BENCHMARK,
                impact=EvidenceImpact.DIAGNOSTIC,
                state=EvidenceState.NOT_APPLICABLE,
                baseline_value=list(left_ids),
                candidate_value=list(right_ids),
            )
        )
    model_identity = _model_evidence(baseline, candidate, intent, evidence)
    _environment_evidence(baseline, candidate, evidence)
    if (
        baseline.manifest.framework.version != candidate.manifest.framework.version
        or baseline.manifest.framework.source != candidate.manifest.framework.source
    ):
        evidence.append(
            FieldEvidence(
                field_path="framework.source_identity",
                dimension=ComparabilityDimension.EVALUATION,
                impact=EvidenceImpact.QUALITY,
                state=EvidenceState.NOT_APPLICABLE,
                baseline_value=baseline.manifest.framework.model_dump(mode="json"),
                candidate_value=candidate.manifest.framework.model_dump(mode="json"),
                reason_code=ComparisonReasonCode.SOURCE_IDENTITY_DIFFERENCE,
            )
        )
    if intent is ComparisonIntent.QUANTIZATION:
        evidence.append(
            FieldEvidence(
                field_path="model.lineage",
                dimension=ComparabilityDimension.MODEL_IDENTITY,
                impact=EvidenceImpact.QUALITY,
                state=EvidenceState.UNKNOWN,
                baseline_value="unverified",
                candidate_value="unverified",
                reason_code=ComparisonReasonCode.MODEL_LINEAGE_UNVERIFIED,
            )
        )
    if intent is ComparisonIntent.THINKING and not (
        not isinstance(baseline.manifest, LegacyV2RunManifest)
        and not isinstance(candidate.manifest, LegacyV2RunManifest)
        and {
            baseline.manifest.configuration.thinking,
            candidate.manifest.configuration.thinking,
        }
        == {ThinkingPolicy.ENABLED, ThinkingPolicy.DISABLED}
    ):
        evidence.append(
            FieldEvidence(
                field_path="intent.thinking.independent_variable",
                dimension=ComparabilityDimension.INFERENCE_PROFILE,
                impact=EvidenceImpact.QUALITY,
                state=EvidenceState.UNKNOWN,
                reason_code=ComparisonReasonCode.INTENDED_VARIABLE_NOT_DIFFERENT,
            )
        )
    if (
        intent is ComparisonIntent.QUANTIZATION
        and baseline.manifest.model.quantization == candidate.manifest.model.quantization
    ):
        evidence.append(
            FieldEvidence(
                field_path="intent.quantization.independent_variable",
                dimension=ComparabilityDimension.MODEL_IDENTITY,
                impact=EvidenceImpact.QUALITY,
                state=EvidenceState.UNKNOWN,
                reason_code=ComparisonReasonCode.INTENDED_VARIABLE_NOT_DIFFERENT,
            )
        )
    if intent is ComparisonIntent.BACKEND and (
        baseline.manifest.provider.type == candidate.manifest.provider.type
        and baseline.manifest.model.backend == candidate.manifest.model.backend
    ):
        evidence.append(
            FieldEvidence(
                field_path="intent.backend.independent_variable",
                dimension=ComparabilityDimension.INFERENCE_PROFILE,
                impact=EvidenceImpact.QUALITY,
                state=EvidenceState.UNKNOWN,
                reason_code=ComparisonReasonCode.INTENDED_VARIABLE_NOT_DIFFERENT,
            )
        )
    if (
        intent is ComparisonIntent.BACKEND
        and baseline.manifest.provider.type != candidate.manifest.provider.type
    ):
        evidence.append(
            FieldEvidence(
                field_path="model.cross_provider_digest_namespace",
                dimension=ComparabilityDimension.MODEL_IDENTITY,
                impact=EvidenceImpact.QUALITY,
                state=EvidenceState.UNKNOWN,
                baseline_value=baseline.manifest.provider.type,
                candidate_value=candidate.manifest.provider.type,
                reason_code=ComparisonReasonCode.MODEL_LINEAGE_UNVERIFIED,
            )
        )

    repeat_equal = (
        baseline.manifest.configuration.repeats == candidate.manifest.configuration.repeats
    )
    common_ids = [case_id for case_id in left_ids if case_id in candidate.cases]
    matched_ids = [
        case_id
        for case_id in common_ids
        if baseline.cases[case_id].fully_scored
        and candidate.cases[case_id].fully_scored
    ]
    baseline_population_complete = all(
        baseline.cases[case_id].fully_scored for case_id in left_ids
    )
    candidate_population_complete = all(
        candidate.cases[case_id].fully_scored for case_id in right_ids
    )
    complete = (
        same_content
        and case_hashes_equal
        and repeat_equal
        and baseline_population_complete
        and candidate_population_complete
        and len(common_ids) == len(left_ids) == len(right_ids)
    )
    aggregation_compatible = (
        baseline.snapshot.suite.aggregation.method
        == candidate.snapshot.suite.aggregation.method
        and baseline.snapshot.suite.aggregation.unscored_policy
        == candidate.snapshot.suite.aggregation.unscored_policy
    )
    _add_evidence(
        evidence,
        path="benchmark.aggregation_semantics",
        dimension=ComparabilityDimension.BENCHMARK,
        impact=EvidenceImpact.QUALITY,
        baseline={
            "method": baseline.snapshot.suite.aggregation.method,
            "unscored_policy": baseline.snapshot.suite.aggregation.unscored_policy,
        },
        candidate={
            "method": candidate.snapshot.suite.aggregation.method,
            "unscored_policy": candidate.snapshot.suite.aggregation.unscored_policy,
        },
        reason=ComparisonReasonCode.AGGREGATION_SEMANTICS_MISMATCH,
    )
    intersection_allowed = (
        same_suite_id and not same_suite_version and not same_content
    )
    intersection_selection = (
        _intersection_selection(
            baseline,
            candidate,
            baseline_case_hashes=baseline_case_hashes,
            candidate_case_hashes=candidate_case_hashes,
        )
        if intersection_allowed and aggregation_compatible
        else None
    )
    if intersection_selection is not None:
        conditional_controls_quality_relevant = bool(
            intersection_selection.evaluable_case_ids
        ) and bool(intersection_selection.evidence.incomplete_scoring_case_ids)
    else:
        conditional_controls_quality_relevant = not (
            baseline_population_complete and candidate_population_complete
        )
    _profile_evidence(
        baseline,
        candidate,
        intent,
        evidence,
        conditional_controls_quality_relevant=conditional_controls_quality_relevant,
    )
    if baseline_eval_error or candidate_eval_error:
        evidence.append(
            FieldEvidence(
                field_path="evaluation.current_registry",
                dimension=ComparabilityDimension.EVALUATION,
                impact=EvidenceImpact.DIAGNOSTIC,
                state=EvidenceState.UNKNOWN,
                baseline_value=baseline_eval_error is None,
                candidate_value=candidate_eval_error is None,
                reason_code=ComparisonReasonCode.EVALUATOR_UNAVAILABLE,
            )
        )
        if same_content and case_hashes_equal:
            quality_case_ids = set(left_ids)
        elif intersection_selection is not None:
            quality_case_ids = {
                item.case_id for item in intersection_selection.evidence.verified_cases
            }
        else:
            quality_case_ids = set()
        baseline_quality_unavailable = tuple(
            item.case_id
            for item in baseline.evaluator_resolution
            if item.status == "unavailable" and item.case_id in quality_case_ids
        )
        candidate_quality_unavailable = tuple(
            item.case_id
            for item in candidate.evaluator_resolution
            if item.status == "unavailable" and item.case_id in quality_case_ids
        )
        if baseline_quality_unavailable or candidate_quality_unavailable:
            evidence.append(
                FieldEvidence(
                    field_path="evaluation.quality_population.current_registry",
                    dimension=ComparabilityDimension.EVALUATION,
                    impact=EvidenceImpact.QUALITY,
                    state=EvidenceState.UNKNOWN,
                    baseline_value=list(baseline_quality_unavailable),
                    candidate_value=list(candidate_quality_unavailable),
                    reason_code=ComparisonReasonCode.EVALUATOR_UNAVAILABLE,
                )
            )
    if intersection_selection is not None:
        for mismatch in intersection_selection.evidence.definition_mismatches:
            for reason in mismatch.reason_codes:
                evidence.append(
                    FieldEvidence(
                        field_path=f"benchmark.cases.{mismatch.case_id}.identity",
                        dimension=(
                            ComparabilityDimension.EVALUATION
                            if reason
                            is ComparisonReasonCode.EVALUATOR_SPECIFICATION_MISMATCH
                            else ComparabilityDimension.BENCHMARK
                        ),
                        impact=EvidenceImpact.QUALITY,
                        state=EvidenceState.DIFFERENCE,
                        baseline_value=mismatch.baseline_case_hash,
                        candidate_value=mismatch.candidate_case_hash,
                        reason_code=reason,
                    )
                )
        if not intersection_selection.evidence.verified_cases:
            evidence.append(
                FieldEvidence(
                    field_path="quality.verified_intersection.population",
                    dimension=ComparabilityDimension.BENCHMARK,
                    impact=EvidenceImpact.QUALITY,
                    state=EvidenceState.UNKNOWN,
                    baseline_value=0,
                    candidate_value=0,
                    reason_code=ComparisonReasonCode.NO_VERIFIED_CASE_INTERSECTION,
                )
            )
        elif (
            intersection_selection.evaluable_case_ids
            and not intersection_selection.selected_case_ids
        ):
            intersection_population = intersection_selection.evidence
            intersection_coverage = intersection_population.coverage
            evidence.append(
                FieldEvidence(
                    field_path="quality.verified_intersection.selected_population",
                    dimension=ComparabilityDimension.COVERAGE,
                    impact=EvidenceImpact.QUALITY,
                    state=EvidenceState.INCOMPLETE,
                    baseline_value={
                        "verified_case_count": len(intersection_population.verified_cases),
                        "evaluator_available_case_count": len(
                            intersection_selection.evaluable_case_ids
                        ),
                        "expected_repeats": intersection_population.baseline_expected_repeats,
                        "scored_repeat_indexes": {
                            case_id: list(baseline.cases[case_id].scored_repeat_indexes)
                            for case_id in intersection_selection.evaluable_case_ids
                        },
                        "scored_samples": intersection_coverage.baseline_scored_samples,
                        "expected_samples": intersection_coverage.baseline_expected_samples,
                        "coverage_ratio": intersection_coverage.baseline_ratio,
                        "minimum_required": intersection_coverage.baseline_minimum_required,
                        "coverage_sufficient": bool(
                            intersection_coverage.baseline_ratio is not None
                            and intersection_coverage.baseline_ratio
                            >= intersection_coverage.baseline_minimum_required
                        ),
                        "selected_case_count": 0,
                    },
                    candidate_value={
                        "verified_case_count": len(intersection_population.verified_cases),
                        "evaluator_available_case_count": len(
                            intersection_selection.evaluable_case_ids
                        ),
                        "expected_repeats": intersection_population.candidate_expected_repeats,
                        "scored_repeat_indexes": {
                            case_id: list(candidate.cases[case_id].scored_repeat_indexes)
                            for case_id in intersection_selection.evaluable_case_ids
                        },
                        "scored_samples": intersection_coverage.candidate_scored_samples,
                        "expected_samples": intersection_coverage.candidate_expected_samples,
                        "coverage_ratio": intersection_coverage.candidate_ratio,
                        "minimum_required": intersection_coverage.candidate_minimum_required,
                        "coverage_sufficient": bool(
                            intersection_coverage.candidate_ratio is not None
                            and intersection_coverage.candidate_ratio
                            >= intersection_coverage.candidate_minimum_required
                        ),
                        "selected_case_count": 0,
                    },
                    reason_code=ComparisonReasonCode.EMPTY_MATCHED_SCORED_POPULATION,
                )
            )
        intersection_coverage = intersection_selection.evidence.coverage
        baseline_intersection_sufficient = bool(
            intersection_coverage.baseline_ratio is not None
            and intersection_coverage.baseline_ratio
            >= intersection_coverage.baseline_minimum_required
        )
        candidate_intersection_sufficient = bool(
            intersection_coverage.candidate_ratio is not None
            and intersection_coverage.candidate_ratio
            >= intersection_coverage.candidate_minimum_required
        )
        evidence.append(
            FieldEvidence(
                field_path="coverage.verified_intersection",
                dimension=ComparabilityDimension.COVERAGE,
                impact=EvidenceImpact.QUALITY,
                state=(
                    EvidenceState.MATCH
                    if intersection_coverage.sufficient
                    else EvidenceState.INCOMPLETE
                ),
                baseline_value={
                    "scored": intersection_coverage.baseline_scored_samples,
                    "expected": intersection_coverage.baseline_expected_samples,
                    "ratio": intersection_coverage.baseline_ratio,
                    "minimum_required": intersection_coverage.baseline_minimum_required,
                    "sufficient": baseline_intersection_sufficient,
                },
                candidate_value={
                    "scored": intersection_coverage.candidate_scored_samples,
                    "expected": intersection_coverage.candidate_expected_samples,
                    "ratio": intersection_coverage.candidate_ratio,
                    "minimum_required": intersection_coverage.candidate_minimum_required,
                    "sufficient": candidate_intersection_sufficient,
                },
                reason_code=(
                    None
                    if intersection_coverage.sufficient
                    else ComparisonReasonCode.COVERAGE_INSUFFICIENT
                ),
            )
        )
        if intersection_selection.evidence.verified_cases:
            _add_evidence(
                evidence,
                path="coverage.verified_intersection.minimum_required",
                dimension=ComparabilityDimension.COVERAGE,
                impact=EvidenceImpact.QUALITY,
                baseline=intersection_coverage.baseline_minimum_required,
                candidate=intersection_coverage.candidate_minimum_required,
                reason=ComparisonReasonCode.COVERAGE_THRESHOLD_DIFFERENCE,
            )
    baseline_population = {
        case_id: list(baseline.cases[case_id].scored_repeat_indexes)
        for case_id in left_ids
    }
    candidate_population = {
        case_id: list(candidate.cases[case_id].scored_repeat_indexes)
        for case_id in right_ids
    }
    source_coverage_impact = (
        EvidenceImpact.DIAGNOSTIC
        if intersection_selection is not None
        else EvidenceImpact.QUALITY
    )
    _add_evidence(
        evidence,
        path="coverage.scored_sample_population",
        dimension=ComparabilityDimension.COVERAGE,
        impact=source_coverage_impact,
        baseline=baseline_population,
        candidate=candidate_population,
        reason=ComparisonReasonCode.SCORED_CASE_SET_DIFFERENCE,
    )
    population_incomplete = not (
        baseline_population_complete and candidate_population_complete
    )
    evidence.append(
        FieldEvidence(
            field_path="coverage.population_completeness",
            dimension=ComparabilityDimension.COVERAGE,
            impact=source_coverage_impact,
            state=(
                EvidenceState.INCOMPLETE if population_incomplete else EvidenceState.MATCH
            ),
            baseline_value=baseline_population_complete,
            candidate_value=candidate_population_complete,
            reason_code=(
                ComparisonReasonCode.INCOMPLETE_SAMPLE_POPULATION
                if population_incomplete
                else None
            ),
        )
    )
    baseline_statuses = {
        case_id: list(baseline.cases[case_id].sample_statuses)
        for case_id in left_ids
    }
    candidate_statuses = {
        case_id: list(candidate.cases[case_id].sample_statuses)
        for case_id in right_ids
    }
    _add_evidence(
        evidence,
        path="coverage.sample_statuses",
        dimension=ComparabilityDimension.COVERAGE,
        impact=source_coverage_impact,
        baseline=baseline_statuses,
        candidate=candidate_statuses,
        reason=ComparisonReasonCode.SAMPLE_STATUS_DIFFERENCE,
    )
    baseline_ratio = baseline.scored_samples / baseline.expected_samples
    candidate_ratio = candidate.scored_samples / candidate.expected_samples
    baseline_minimum = baseline.manifest.configuration.minimum_scored_coverage
    candidate_minimum = candidate.manifest.configuration.minimum_scored_coverage
    baseline_sufficient = baseline_ratio >= baseline_minimum
    candidate_sufficient = candidate_ratio >= candidate_minimum
    coverage_insufficient = not (baseline_sufficient and candidate_sufficient)
    _add_evidence(
        evidence,
        path="coverage.ratio",
        dimension=ComparabilityDimension.COVERAGE,
        impact=source_coverage_impact,
        baseline=baseline_ratio,
        candidate=candidate_ratio,
        reason=None,
    )
    _add_evidence(
        evidence,
        path="coverage.minimum_required",
        dimension=ComparabilityDimension.COVERAGE,
        impact=source_coverage_impact,
        baseline=baseline_minimum,
        candidate=candidate_minimum,
        reason=ComparisonReasonCode.COVERAGE_THRESHOLD_DIFFERENCE,
    )
    evidence.append(
        FieldEvidence(
            field_path="coverage.sufficient",
            dimension=ComparabilityDimension.COVERAGE,
            impact=source_coverage_impact,
            state=(
                EvidenceState.MATCH
                if baseline_sufficient == candidate_sufficient
                else EvidenceState.DIFFERENCE
            ),
            baseline_value=baseline_sufficient,
            candidate_value=candidate_sufficient,
            reason_code=(
                ComparisonReasonCode.COVERAGE_INSUFFICIENT
                if coverage_insufficient
                else None
            ),
        )
    )

    same_benchmark_eligible = (
        same_content and case_hashes_equal and not baseline_eval_error and not candidate_eval_error
    )
    verified_intersection = (
        intersection_selection.evidence if intersection_selection is not None else None
    )
    if same_benchmark_eligible:
        selected_ids = list(common_ids if complete else matched_ids)
    elif intersection_selection is not None:
        selected_ids = list(intersection_selection.selected_case_ids)
    else:
        selected_ids = []
    left_selected = [baseline.cases[case_id] for case_id in selected_ids]
    right_selected = [candidate.cases[case_id] for case_id in selected_ids]
    full_score = (
        _score_comparison(left_selected, right_selected)
        if same_benchmark_eligible and complete
        else None
    )
    partial_score = (
        _score_comparison(left_selected, right_selected)
        if same_benchmark_eligible and not complete and selected_ids
        else None
    )
    intersection_score = (
        _score_comparison(left_selected, right_selected)
        if intersection_selection is not None and selected_ids
        else None
    )
    refusal_compliance_analysis = _refusal_compliance_analysis(
        left_selected, right_selected
    )
    population_mode = (
        CasePopulationMode.FULL_SUITE
        if full_score
        else CasePopulationMode.MATCHED_CASE_PARTIAL
        if partial_score
        else (
            CasePopulationMode.VERIFIED_INTERSECTION
            if intersection_selection is not None and intersection_selection.complete
            else CasePopulationMode.VERIFIED_INTERSECTION_MATCHED_PARTIAL
        )
        if intersection_score
        else CasePopulationMode.NONE
    )
    if population_mode in {
        CasePopulationMode.VERIFIED_INTERSECTION,
        CasePopulationMode.VERIFIED_INTERSECTION_MATCHED_PARTIAL,
    }:
        evidence.append(
            FieldEvidence(
                field_path="quality.verified_intersection",
                dimension=ComparabilityDimension.BENCHMARK,
                impact=EvidenceImpact.QUALITY,
                state=EvidenceState.DIFFERENCE,
                baseline_value=baseline.snapshot.suite.version,
                candidate_value=candidate.snapshot.suite.version,
                reason_code=ComparisonReasonCode.VERIFIED_INTERSECTION_COMPARISON,
            )
        )
    intersection_coverage_sufficient = bool(
        intersection_selection is not None
        and intersection_selection.evidence.coverage.sufficient
    )
    hard_failure = (
        (not same_benchmark_eligible and intersection_selection is None)
        or (
            coverage_insufficient
            if same_benchmark_eligible
            else not intersection_coverage_sufficient
        )
        or not selected_ids
        or model_identity.relationship == "conflict"
    )
    quality = _classify_quality(
        evidence, hard_failure=hard_failure, has_population=bool(selected_ids)
    )
    performance = _classify_performance(evidence)
    if same_content and case_hashes_equal and (
        baseline_eval_error or candidate_eval_error
    ):
        performance_case_ids = tuple(common_ids)
    elif intersection_selection is not None and selected_ids:
        selected_or_evaluator_unavailable = set(selected_ids) | set(
            intersection_selection.evidence.evaluator_unavailable_case_ids
        )
        performance_case_ids = tuple(
            item.case_id
            for item in intersection_selection.evidence.verified_cases
            if item.case_id in selected_or_evaluator_unavailable
        )
    elif selected_ids:
        performance_case_ids = tuple(selected_ids)
    elif same_content and case_hashes_equal:
        performance_case_ids = tuple(common_ids)
    elif intersection_selection is not None:
        performance_case_ids = tuple(
            item.case_id for item in intersection_selection.evidence.verified_cases
        )
    else:
        performance_case_ids = ()
    paired_repeat_count = min(
        baseline.manifest.configuration.repeats,
        candidate.manifest.configuration.repeats,
    )
    performance_sample_identities = tuple(
        SampleIdentity(case_id=case_id, repeat_index=repeat_index)
        for case_id in performance_case_ids
        for repeat_index in range(paired_repeat_count)
    )
    if same_content and case_hashes_equal:
        performance_execution_case_ids = tuple(common_ids)
    elif intersection_selection is not None:
        performance_execution_case_ids = tuple(
            item.case_id for item in intersection_selection.evidence.verified_cases
        )
    else:
        performance_execution_case_ids = ()
    performance_execution_sample_identities = tuple(
        SampleIdentity(case_id=case_id, repeat_index=repeat_index)
        for case_id in performance_execution_case_ids
        for repeat_index in range(paired_repeat_count)
    )
    metric_performance_reasons = _reason_codes(
        item
        for item in evidence
        if item.impact in {EvidenceImpact.PERFORMANCE, EvidenceImpact.BOTH}
        and item.dimension
        in {
            ComparabilityDimension.MODEL_IDENTITY,
            ComparabilityDimension.INFERENCE_PROFILE,
            ComparabilityDimension.PERFORMANCE_ENVIRONMENT,
        }
    )
    performance_analysis: PerformanceAnalysis = analyze_performance(
        baseline.performance_samples,
        candidate.performance_samples,
        performance_sample_identities,
        execution_cost_sample_identities=performance_execution_sample_identities,
        context=PerformanceComparabilityContext(
            intent=intent,
            baseline_tokenizer=baseline.manifest.model.tokenizer,
            candidate_tokenizer=candidate.manifest.model.tokenizer,
            baseline_provider=baseline.manifest.provider.type,
            candidate_provider=candidate.manifest.provider.type,
            baseline_backend=baseline.manifest.model.backend,
            candidate_backend=candidate.manifest.model.backend,
            baseline_result_schema_version=baseline.manifest.schema_version,
            candidate_result_schema_version=candidate.manifest.schema_version,
            performance_reason_codes=metric_performance_reasons,
        ),
    )
    cases: list[CaseComparison] = []
    if same_benchmark_eligible:
        displayed_case_ids: Sequence[str] = common_ids
    elif intersection_selection is not None:
        displayed_case_ids = tuple(
            item.case_id for item in intersection_selection.evidence.verified_cases
        )
    else:
        displayed_case_ids = ()
    for case_id in displayed_case_ids:
        left_case = baseline.cases[case_id]
        right_case = candidate.cases[case_id]
        if left_case.score is None or right_case.score is None:
            delta = None
            direction = CaseDirection.UNAVAILABLE
        else:
            delta = right_case.score - left_case.score
            direction = (
                CaseDirection.HIGHER
                if delta > 0
                else CaseDirection.LOWER
                if delta < 0
                else CaseDirection.UNCHANGED
            )
        cases.append(
            CaseComparison(
                case_id=case_id,
                category=left_case.case.category,
                tags=left_case.case.tags,
                baseline_status=left_case.status,
                candidate_status=right_case.status,
                baseline_score=left_case.score,
                candidate_score=right_case.score,
                baseline_scored_repeats=left_case.scored_repeats,
                candidate_scored_repeats=right_case.scored_repeats,
                baseline_expected_repeats=left_case.expected_repeats,
                candidate_expected_repeats=right_case.expected_repeats,
                delta=delta,
                direction=direction,
            )
        )
    evaluator_provenance = baseline.evaluator_provenance + candidate.evaluator_provenance
    baseline_semantics = {
        item.case_id: (item.evaluator_name, item.evaluator_version, item.configuration_hash)
        for item in baseline.evaluator_provenance
    }
    candidate_semantics = {
        item.case_id: (item.evaluator_name, item.evaluator_version, item.configuration_hash)
        for item in candidate.evaluator_provenance
    }
    shared_provenance = set(baseline_semantics) & set(candidate_semantics)
    provenance_scope = (
        shared_provenance
        if same_benchmark_eligible
        else shared_provenance & set(selected_ids)
    )
    if any(
        baseline_semantics[case_id] != candidate_semantics[case_id]
        for case_id in provenance_scope
    ):
        evidence.append(
            FieldEvidence(
                field_path="evaluation.provenance",
                dimension=ComparabilityDimension.EVALUATION,
                impact=EvidenceImpact.QUALITY,
                state=EvidenceState.DIFFERENCE,
                baseline_value=[
                    item.model_dump(mode="json") for item in baseline.evaluator_provenance
                ],
                candidate_value=[
                    item.model_dump(mode="json") for item in candidate.evaluator_provenance
                ],
                reason_code=ComparisonReasonCode.EVALUATOR_SPECIFICATION_MISMATCH,
            )
        )
        quality = _classify_quality(evidence, hard_failure=True, has_population=bool(selected_ids))
    coverage = CoverageComparison(
        baseline_expected_samples=baseline.expected_samples,
        candidate_expected_samples=candidate.expected_samples,
        baseline_scored_samples=baseline.scored_samples,
        candidate_scored_samples=candidate.scored_samples,
        baseline_ratio=baseline_ratio,
        candidate_ratio=candidate_ratio,
        matched_case_count=(
            len(matched_ids)
            if same_benchmark_eligible
            else len(selected_ids)
            if intersection_selection is not None
            else 0
        ),
        benchmark_case_count=len(left_ids),
    )
    baseline_benchmark = BenchmarkIdentityEvidence(
        suite_id=baseline.snapshot.suite.id,
        version=baseline.snapshot.suite.version,
        content_hash=baseline.snapshot.benchmark_content_hash,
        snapshot_hash=baseline.manifest.benchmark_snapshot_hash,
    )
    candidate_benchmark = BenchmarkIdentityEvidence(
        suite_id=candidate.snapshot.suite.id,
        version=candidate.snapshot.suite.version,
        content_hash=candidate.snapshot.benchmark_content_hash,
        snapshot_hash=candidate.manifest.benchmark_snapshot_hash,
    )
    evaluator_resolution = (
        baseline.evaluator_resolution + candidate.evaluator_resolution
    )
    canonical_evidence = sorted(
        (item.model_dump(mode="json") for item in evidence),
        key=hash_canonical,
    )
    canonical_resolution = sorted(
        (item.model_dump(mode="json") for item in evaluator_resolution),
        key=hash_canonical,
    )
    canonical_provenance = sorted(
        (item.model_dump(mode="json") for item in evaluator_provenance),
        key=hash_canonical,
    )
    fingerprint = hash_canonical(
        {
            "baseline_evidence_hash": baseline.evidence_hash,
            "candidate_evidence_hash": candidate.evidence_hash,
            "baseline_benchmark": baseline_benchmark.model_dump(mode="json"),
            "candidate_benchmark": candidate_benchmark.model_dump(mode="json"),
            "intent": intent,
            "comparison_policy_version": COMPARISON_POLICY_VERSION,
            "case_population_mode": population_mode,
            "selected_case_ids": list(selected_ids),
            "selected_case_identities": [
                {
                    "case_id": case_id,
                    "canonical_case_hash": baseline_case_hashes[case_id],
                    "weight": baseline.cases[case_id].case.weight,
                }
                for case_id in selected_ids
            ],
            "verified_intersection": (
                verified_intersection.model_dump(mode="json")
                if verified_intersection is not None
                else None
            ),
            "evaluator_resolution": canonical_resolution,
            "evaluator_provenance": canonical_provenance,
            "semantic_evidence": canonical_evidence,
            "performance_analysis": performance_analysis.model_dump(mode="json"),
            "refusal_compliance_analysis": (
                refusal_compliance_analysis.model_dump(mode="json")
                if refusal_compliance_analysis is not None
                else None
            ),
        }
    )
    return ComparisonResult(
        comparison_policy_version=COMPARISON_POLICY_VERSION,
        comparison_fingerprint=fingerprint,
        generated_at=clock(),
        intent=intent,
        baseline=RunComparisonReference(
            run_id=baseline.manifest.run_id,
            run_fingerprint=baseline.manifest.run_fingerprint,
            result_schema_version=baseline.manifest.schema_version,
            evidence_hash=baseline.evidence_hash,
            benchmark=baseline_benchmark,
        ),
        candidate=RunComparisonReference(
            run_id=candidate.manifest.run_id,
            run_fingerprint=candidate.manifest.run_fingerprint,
            result_schema_version=candidate.manifest.schema_version,
            evidence_hash=candidate.evidence_hash,
            benchmark=candidate_benchmark,
        ),
        model_identity=model_identity,
        quality_comparability=quality,
        performance_comparability=performance,
        performance_analysis=performance_analysis,
        refusal_compliance_analysis=refusal_compliance_analysis,
        evidence=tuple(evidence),
        evaluator_resolution=evaluator_resolution,
        evaluator_provenance=evaluator_provenance,
        coverage=coverage,
        case_population_mode=population_mode,
        baseline_source_run_score=_source_run_score(baseline),
        candidate_source_run_score=_source_run_score(candidate),
        verified_intersection=verified_intersection,
        full_suite_score_comparison=full_score,
        matched_case_score_comparison=partial_score,
        verified_intersection_score_comparison=intersection_score,
        categories=(
            _breakdowns(
                left_selected,
                right_selected,
                tags=False,
                population_mode=population_mode,
                baseline_all_cases=baseline.snapshot.suite.cases,
                candidate_all_cases=candidate.snapshot.suite.cases,
            )
            if selected_ids
            else ()
        ),
        tags=(
            _breakdowns(
                left_selected,
                right_selected,
                tags=True,
                population_mode=population_mode,
                baseline_all_cases=baseline.snapshot.suite.cases,
                candidate_all_cases=candidate.snapshot.suite.cases,
            )
            if selected_ids
            else ()
        ),
        cases=tuple(cases),
    )


def write_comparison(
    path: str | Path,
    result: ComparisonResult,
    *,
    source_runs: Sequence[str | Path],
) -> None:
    """Atomically write an explicitly requested derived comparison JSON file."""
    destination = Path(path).resolve()
    for source in source_runs:
        source_path = Path(source).resolve()
        if destination == source_path or destination.is_relative_to(source_path):
            raise ComparisonError("comparison output must not be written inside a source run")
    temporary_name: str | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = comparison_json(result)
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, delete=False
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, destination)
    except OSError as error:
        raise ComparisonError(f"could not write comparison output: {error}") from error
    finally:
        if temporary_name is not None and Path(temporary_name).exists():
            Path(temporary_name).unlink()


def comparison_json(result: ComparisonResult) -> str:
    """Serialize comparison output as stable, prose-free JSON."""
    return (
        json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
