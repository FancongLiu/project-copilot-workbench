from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evaluation.adjudicate_agentic_rag_result import adjudicate_result
from evaluation.run_offline import EvaluationContractError


CASE_IDS = [f"K{index:02d}" for index in range(1, 53)]


def _write_benchmark(path: Path) -> str:
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "benchmark_id": "benchmark-52",
                "cases": [{"id": case_id} for case_id in CASE_IDS],
            }
        ),
        encoding="utf-8",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_result(path: Path, benchmark_hash: str) -> str:
    cases = []
    for case_id in CASE_IDS:
        strict_pass = case_id != "K02"
        cases.append(
            {
                "id": case_id,
                "status": "completed",
                "checks": {
                    "behavior_pass": strict_pass,
                    "tool_contract_pass": strict_pass,
                    "evidence_contract_pass": strict_pass,
                },
            }
        )
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "benchmark_id": "benchmark-52",
                "candidate_id": "candidate",
                "provenance": {"benchmark_sha256": benchmark_hash},
                "cases": cases,
            }
        ),
        encoding="utf-8",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_adjudication_records_every_case_and_source_hash(tmp_path: Path) -> None:
    benchmark_path = tmp_path / "benchmark.json"
    benchmark_hash = _write_benchmark(benchmark_path)
    result_path = tmp_path / "result.json"
    source_hash = _write_result(result_path, benchmark_hash)
    review_path = tmp_path / "review.json"
    decisions = {
        case_id: {
            "accepted": True,
            "classification": (
                "reasonable_default" if case_id == "K02" else "correct_grounded_answer"
            ),
            "reason": (
                "The answer states its comparison basis."
                if case_id == "K02"
                else "The answer matches the expected case contract."
            ),
        }
        for case_id in CASE_IDS
    }
    review_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "source_result_sha256": source_hash,
                "rubric_version": "hvac-review-1",
                "reviewer": "independent-hvac-engineer",
                "decisions": decisions,
            }
        ),
        encoding="utf-8",
    )

    report = adjudicate_result(result_path, review_path, benchmark_path)

    assert report["source_result_sha256"] == source_hash
    assert report["benchmark_sha256"] == benchmark_hash
    assert report["accepted_count"] == 52
    assert report["case_count"] == 52
    assert report["decisions"][:2] == [
        {
            "id": "K01",
            "accepted": True,
            "classification": "correct_grounded_answer",
            "reason": "The answer matches the expected case contract.",
            "reviewer": "independent-hvac-engineer",
            "strict_checks": {
                "behavior_pass": True,
                "tool_contract_pass": True,
                "evidence_contract_pass": True,
            },
        },
        {
            "id": "K02",
            "accepted": True,
            "classification": "reasonable_default",
            "reason": "The answer states its comparison basis.",
            "reviewer": "independent-hvac-engineer",
            "strict_checks": {
                "behavior_pass": False,
                "tool_contract_pass": False,
                "evidence_contract_pass": False,
            },
        },
    ]


def test_adjudication_rejects_hash_mismatch_or_unreviewed_failure(
    tmp_path: Path,
) -> None:
    benchmark_path = tmp_path / "benchmark.json"
    benchmark_hash = _write_benchmark(benchmark_path)
    result_path = tmp_path / "result.json"
    source_hash = _write_result(result_path, benchmark_hash)
    review_path = tmp_path / "review.json"
    base_review = {
        "schema_version": "1.0",
        "source_result_sha256": "0" * 64,
        "rubric_version": "hvac-review-1",
        "reviewer": "independent-hvac-engineer",
        "decisions": {
            case_id: {
                "accepted": True,
                "classification": "correct_grounded_answer",
                "reason": "The answer matches the expected case contract.",
            }
            for case_id in CASE_IDS
        },
    }
    review_path.write_text(json.dumps(base_review), encoding="utf-8")

    with pytest.raises(EvaluationContractError, match="SHA-256"):
        adjudicate_result(result_path, review_path, benchmark_path)

    base_review["source_result_sha256"] = source_hash
    del base_review["decisions"]["K52"]
    review_path.write_text(json.dumps(base_review), encoding="utf-8")
    with pytest.raises(EvaluationContractError, match="K52"):
        adjudicate_result(result_path, review_path, benchmark_path)


def test_adjudication_rejects_partial_candidate_result(tmp_path: Path) -> None:
    benchmark_path = tmp_path / "benchmark.json"
    benchmark_hash = _write_benchmark(benchmark_path)
    result_path = tmp_path / "partial-result.json"
    _write_result(result_path, benchmark_hash)
    partial = json.loads(result_path.read_text(encoding="utf-8"))
    partial["cases"] = partial["cases"][:2]
    result_path.write_text(json.dumps(partial), encoding="utf-8")
    review_path = tmp_path / "review.json"
    review_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "source_result_sha256": hashlib.sha256(
                    result_path.read_bytes()
                ).hexdigest(),
                "rubric_version": "hvac-review-1",
                "reviewer": "independent-hvac-engineer",
                "decisions": {
                    case_id: {
                        "accepted": False,
                        "classification": "partial_result",
                        "reason": "The complete benchmark result is required.",
                    }
                    for case_id in CASE_IDS[:2]
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(EvaluationContractError, match="exactly 52"):
        adjudicate_result(result_path, review_path, benchmark_path)
