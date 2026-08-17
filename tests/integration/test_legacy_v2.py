"""Read-only compatibility tests using immutable artifacts written by M2."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from elarabench.cli import main
from elarabench.hashing import hash_canonical
from elarabench.legacy_v2 import (
    LegacyV2BenchmarkSnapshot,
    LegacyV2RunManifest,
    compute_legacy_v2_fingerprint,
)
from elarabench.models import AggregationSummary, EvaluationSpecification, SampleIdentity
from elarabench.scoring import (
    RunIntegrityError,
    open_run_path,
    score_run,
    summarize_run,
    validate_stored_run,
)

FIXTURE = Path("tests/fixtures/historical_v2_run")
IDENTITY = SampleIdentity(case_id="exact-001", repeat_index=0)
CANONICAL_PATHS = (
    "manifest.json",
    "benchmark.json",
    "samples/exact-001/repeat-000/request.json",
    "samples/exact-001/repeat-000/response.json",
    "samples/exact-001/repeat-000/attempts/attempt-000.json",
)


def copied_run(tmp_path: Path) -> Path:
    target = tmp_path / "historical_v2_run"
    shutil.copytree(FIXTURE, target)
    return target


def canonical_hashes(path: Path) -> dict[str, str]:
    return {
        relative: hashlib.sha256((path / relative).read_bytes()).hexdigest()
        for relative in CANONICAL_PATHS
    }


def replace_v2_evaluation(
    run_path: Path,
    specification: EvaluationSpecification,
) -> None:
    """Create coherent historical v2 identity after changing only scoring policy."""
    store = open_run_path(run_path)
    manifest, snapshot = validate_stored_run(store)
    assert isinstance(manifest, LegacyV2RunManifest)
    assert isinstance(snapshot, LegacyV2BenchmarkSnapshot)
    case = snapshot.suite.cases[0].model_copy(update={"evaluation": specification})
    suite = snapshot.suite.model_copy(update={"cases": (case,)})
    content_hash = hash_canonical(
        {"suite": suite.model_dump(mode="json"), "fixtures": []}
    )
    provisional = snapshot.model_copy(
        update={
            "suite": suite,
            "benchmark_content_hash": content_hash,
            "snapshot_hash": "0" * 64,
        }
    )
    snapshot = provisional.model_copy(
        update={
            "snapshot_hash": hash_canonical(
                provisional.model_dump(mode="json", exclude={"snapshot_hash"})
            )
        }
    )
    manifest = manifest.model_copy(
        update={
            "suite_hash": content_hash,
            "benchmark_snapshot_hash": snapshot.snapshot_hash,
        }
    )
    manifest = manifest.model_copy(
        update={"run_fingerprint": compute_legacy_v2_fingerprint(manifest, snapshot)}
    )
    (run_path / "benchmark.json").write_text(
        snapshot.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (run_path / "manifest.json").write_text(
        manifest.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def test_genuine_v2_identity_and_requests_validate_without_v3_defaults(
    tmp_path: Path,
) -> None:
    run_path = copied_run(tmp_path)
    store = open_run_path(run_path)
    manifest, snapshot = validate_stored_run(store)

    assert isinstance(manifest, LegacyV2RunManifest)
    assert isinstance(snapshot, LegacyV2BenchmarkSnapshot)
    assert manifest.schema_version == 2
    assert manifest.run_fingerprint == (
        "8b9dccaef129b8f3bca2fd410e15d19e436500c5a43bbe19c831e347090c6f76"
    )
    assert "thinking" not in store.read_manifest_data()["configuration"]
    assert "thinking" not in store.read_request_data(IDENTITY)
    assert store.read_response(IDENTITY).text == "ELARA"
    evaluation_path = run_path / "samples/exact-001/repeat-000/evaluation.json"
    before = evaluation_path.read_bytes()
    stored_evaluation = json.loads(before)
    assert "source_result_schema_version" not in stored_evaluation
    assert (
        store.read_evaluation(IDENTITY, source_result_schema_version=2)
        .source_result_schema_version
        == 2
    )
    assert evaluation_path.read_bytes() == before


def test_v2_summarize_infers_untouched_evaluation_provenance_without_rescoring(
    tmp_path: Path,
) -> None:
    run_path = copied_run(tmp_path)
    evaluation_path = run_path / "samples/exact-001/repeat-000/evaluation.json"
    evaluation_before = evaluation_path.read_bytes()
    canonical_before = canonical_hashes(run_path)

    summary = summarize_run(run_path)

    assert summary.schema_version == 3
    assert summary.source_result_schema_version == 2
    assert evaluation_path.read_bytes() == evaluation_before
    assert canonical_hashes(run_path) == canonical_before


def test_v2_score_and_summarize_preserve_all_canonical_evidence(tmp_path: Path) -> None:
    run_path = copied_run(tmp_path)
    before = canonical_hashes(run_path)
    historical_summary = json.loads((run_path / "summary.json").read_text(encoding="utf-8"))
    assert historical_summary["schema_version"] == 2
    assert "source_result_schema_version" not in historical_summary
    with pytest.raises(ValidationError):
        AggregationSummary.model_validate(historical_summary)

    scored = score_run(run_path)
    assert scored.score == 1.0
    assert scored.schema_version == 3
    assert scored.source_result_schema_version == 2
    store = open_run_path(run_path)
    assert (
        store.read_evaluation(IDENTITY, source_result_schema_version=2)
        .source_result_schema_version
        == 2
    )
    assert canonical_hashes(run_path) == before

    summarized = summarize_run(run_path)
    assert summarized.score == 1.0
    assert summarized.schema_version == 3
    assert summarized.source_result_schema_version == 2
    assert canonical_hashes(run_path) == before


def test_v2_rescore_propagates_provenance_through_recursive_composite(
    tmp_path: Path,
) -> None:
    run_path = copied_run(tmp_path)
    specification = EvaluationSpecification.model_validate(
        {
            "type": "composite",
            "components": [
                {
                    "specification": {
                        "type": "composite",
                        "components": [
                            {
                                "specification": {
                                    "type": "exact_match",
                                    "config": {"expected": "ELARA"},
                                }
                            }
                        ],
                    }
                }
            ],
        }
    )
    replace_v2_evaluation(run_path, specification)
    before = canonical_hashes(run_path)

    summary = score_run(run_path)
    result = open_run_path(run_path).read_evaluation(
        IDENTITY,
        source_result_schema_version=2,
    )
    outer_components = result.artifacts["components"]
    assert isinstance(outer_components, list)
    inner_result = cast(dict[str, object], outer_components[0])
    inner_artifacts = cast(dict[str, object], inner_result["artifacts"])
    inner_components = cast(list[object], inner_artifacts["components"])
    leaf_result = cast(dict[str, object], inner_components[0])

    assert result.source_result_schema_version == 2
    assert inner_result["source_result_schema_version"] == 2
    assert leaf_result["source_result_schema_version"] == 2
    assert summary.schema_version == 3
    assert summary.source_result_schema_version == 2
    assert canonical_hashes(run_path) == before


def test_v2_resume_is_rejected_before_provider_construction(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_path = copied_run(tmp_path)

    assert main(["run", "--resume", str(run_path)]) == 2
    captured = capsys.readouterr()
    assert "schema v2" in captured.err
    assert "cannot be resumed under schema v3" in captured.err


def test_v2_score_and_summarize_cli_remain_available(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_path = copied_run(tmp_path)

    assert main(["score", str(run_path)]) == 0
    assert "Score: 1.0" in capsys.readouterr().out
    assert main(["summarize", str(run_path)]) == 0
    assert "Scored coverage: 100.00%" in capsys.readouterr().out


def test_v2_request_hash_uses_legacy_serialization(tmp_path: Path) -> None:
    run_path = copied_run(tmp_path)
    request_path = run_path / "samples/exact-001/repeat-000/request.json"
    request_path.write_text(
        request_path.read_text(encoding="utf-8").replace(
            "Return exactly ELARA.", "Return exactly CHANGED."
        ),
        encoding="utf-8",
    )

    with pytest.raises(RunIntegrityError, match="canonical request hash mismatch"):
        score_run(run_path)
