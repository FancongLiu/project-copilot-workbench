from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.run_offline import EvaluationContractError, _atomic_json_write


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCHMARK = REPOSITORY_ROOT / "evaluation" / "agentic_rag_bakeoff.json"
REQUIRED_CASE_COUNT = 52


def _read_json(path: str | Path, label: str) -> tuple[Path, dict[str, Any]]:
    resolved = Path(path).resolve()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationContractError(f"Cannot read {label}: {resolved}") from exc
    if not isinstance(payload, dict):
        raise EvaluationContractError(f"{label} must be a JSON object")
    return resolved, payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def adjudicate_result(
    result_path: str | Path,
    review_path: str | Path,
    benchmark_path: str | Path = DEFAULT_BENCHMARK,
) -> dict[str, Any]:
    resolved_result, result = _read_json(result_path, "candidate result")
    resolved_benchmark, benchmark = _read_json(benchmark_path, "benchmark")
    _, review = _read_json(review_path, "adjudication review")
    benchmark_cases = benchmark.get("cases")
    if (
        not isinstance(benchmark_cases, list)
        or len(benchmark_cases) != REQUIRED_CASE_COUNT
    ):
        raise EvaluationContractError(
            f"Adjudication benchmark must contain exactly {REQUIRED_CASE_COUNT} cases"
        )
    benchmark_ids = [str(case.get("id")) for case in benchmark_cases]
    if len(set(benchmark_ids)) != REQUIRED_CASE_COUNT or any(
        not case_id or case_id == "None" for case_id in benchmark_ids
    ):
        raise EvaluationContractError("Adjudication benchmark case IDs are invalid")
    benchmark_hash = _sha256(resolved_benchmark)
    if result.get("benchmark_id") != benchmark.get("benchmark_id"):
        raise EvaluationContractError("Candidate result benchmark_id does not match")
    provenance = result.get("provenance")
    if (
        not isinstance(provenance, dict)
        or provenance.get("benchmark_sha256") != benchmark_hash
    ):
        raise EvaluationContractError(
            "Candidate result provenance does not match the adjudication benchmark"
        )
    if review.get("schema_version") != "1.0":
        raise EvaluationContractError("Adjudication schema_version must be 1.0")
    source_hash = _sha256(resolved_result)
    if review.get("source_result_sha256") != source_hash:
        raise EvaluationContractError(
            "Adjudication source-result SHA-256 does not match"
        )
    reviewer = str(review.get("reviewer", "")).strip()
    rubric_version = str(review.get("rubric_version", "")).strip()
    review_decisions = review.get("decisions")
    if not reviewer or not rubric_version or not isinstance(review_decisions, dict):
        raise EvaluationContractError(
            "Adjudication reviewer, rubric_version and decisions are required"
        )

    cases = result.get("cases")
    if not isinstance(cases, list) or len(cases) != REQUIRED_CASE_COUNT:
        raise EvaluationContractError(
            f"Candidate result must contain exactly {REQUIRED_CASE_COUNT} cases"
        )
    result_ids = [
        str(case.get("id"))
        for case in cases
        if isinstance(case, dict) and case.get("id")
    ]
    if len(result_ids) != REQUIRED_CASE_COUNT or set(result_ids) != set(benchmark_ids):
        raise EvaluationContractError(
            "Candidate result must contain every benchmark case exactly once"
        )
    case_ids = set(result_ids)
    unknown_decisions = sorted(set(review_decisions) - case_ids)
    if unknown_decisions:
        raise EvaluationContractError(
            f"Adjudication decisions reference unknown cases: {unknown_decisions}"
        )
    missing_decisions = sorted(case_ids - set(review_decisions))
    if missing_decisions:
        raise EvaluationContractError(
            f"Adjudication decisions are missing cases: {missing_decisions}"
        )

    decisions: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict) or not case.get("id"):
            raise EvaluationContractError("Candidate result contains an invalid case")
        case_id = str(case["id"])
        checks = case.get("checks")
        if not isinstance(checks, dict):
            raise EvaluationContractError(f"Candidate checks are missing for {case_id}")
        strict_checks = {
            "behavior_pass": checks.get("behavior_pass"),
            "tool_contract_pass": checks.get("tool_contract_pass"),
            "evidence_contract_pass": checks.get("evidence_contract_pass"),
        }
        reviewed = review_decisions[case_id]
        if not isinstance(reviewed, dict):
            raise EvaluationContractError(
                f"Adjudication decision must be an object for {case_id}"
            )
        accepted = reviewed.get("accepted")
        classification = str(reviewed.get("classification", "")).strip()
        reason = str(reviewed.get("reason", "")).strip()
        if not isinstance(accepted, bool) or not classification or not reason:
            raise EvaluationContractError(
                f"Adjudication decision is incomplete for {case_id}"
            )
        decision = {
            "id": case_id,
            "accepted": accepted,
            "classification": classification,
            "reason": reason,
            "reviewer": reviewer,
            "strict_checks": strict_checks,
        }
        decisions.append(decision)

    accepted_count = sum(bool(item["accepted"]) for item in decisions)
    return {
        "schema_version": "1.0",
        "source_result": resolved_result.name,
        "source_result_sha256": source_hash,
        "benchmark_sha256": benchmark_hash,
        "benchmark_id": result.get("benchmark_id"),
        "candidate_id": result.get("candidate_id"),
        "provenance": result.get("provenance"),
        "reviewed_at": datetime.now(UTC).isoformat(),
        "reviewer": reviewer,
        "rubric_version": rubric_version,
        "rubric": {
            "per_case_review": (
                "Every response, including strict passes, requires an explicit "
                "accepted/rejected verdict against its expected answer, evidence "
                "contract and HVAC engineering usefulness."
            ),
        },
        "decisions": decisions,
        "accepted_count": accepted_count,
        "case_count": len(decisions),
        "accepted_rate": round(accepted_count / len(decisions), 6),
        "rejected_count": len(decisions) - accepted_count,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a hash-bound per-case Agentic RAG adjudication report."
    )
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = adjudicate_result(args.result, args.review, args.benchmark)
    _atomic_json_write(args.output.resolve(), report)
    print(
        json.dumps(
            {
                "accepted_count": report["accepted_count"],
                "case_count": report["case_count"],
                "accepted_rate": report["accepted_rate"],
                "source_result_sha256": report["source_result_sha256"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
