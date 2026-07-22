from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx

from evaluation.run_offline import EvaluationContractError, _atomic_json_write


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCHMARK = REPOSITORY_ROOT / "evaluation" / "agentic_rag_bakeoff.json"
DEFAULT_OUTPUT = (
    REPOSITORY_ROOT / "evaluation" / "results" / "agentic-rag-haystack-duckdb-live.json"
)
DEFAULT_ENDPOINT = "http://127.0.0.1:8788/api/direction/query"
SCORER_VERSION = "3.5"

_KNOWN_TOOLS = {
    "ask_for_clarification",
    "inspect_configuration_change_effect",
    "inspect_configuration_history",
    "inspect_hvac_snapshot",
    "inspect_metric_extreme",
    "query_hvac_database",
    "search_project_knowledge",
}

AskFunction = Callable[[str], dict[str, Any]]


class ModelProviderFailure(RuntimeError):
    """Raised when the candidate reports an upstream model-provider failure."""


_MODEL_PROVIDER_FAILURE_MARKERS = (
    "PermissionDeniedError",
    "AuthenticationError",
    "RateLimitError",
    "APIConnectionError",
    "APITimeoutError",
)

_DATA_EVIDENCE_TO_CITATION = {
    "assets": "assets.csv",
    "config_history": "config_history.csv",
    "point_aliases": "point_aliases.csv",
    "telemetry_clean": "telemetry.csv",
    "telemetry_raw": "telemetry.csv",
}

_PRESENTATION_TOOLS = {
    "P01": {"query_hvac_database"},
    "P02": {"query_hvac_database"},
    "P03": {"search_project_knowledge"},
    "P04": {"query_hvac_database", "search_project_knowledge"},
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _content_fingerprint(
    paths: list[Path],
    *,
    root: Path = REPOSITORY_ROOT,
) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _candidate_input_paths() -> list[Path]:
    source_root = REPOSITORY_ROOT / "src" / "project_copilot"
    return sorted(
        [
            *source_root.rglob("*.py"),
            Path(__file__).resolve(),
            REPOSITORY_ROOT / "pyproject.toml",
            REPOSITORY_ROOT / "requirements.runtime.lock",
        ]
    )


def _default_candidate_revision() -> str:
    content_hash = _content_fingerprint(_candidate_input_paths())[:16]
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        revision = completed.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        revision = "git-unavailable"
    return f"{revision}+content-{content_hash}"


def _corpus_sha256(corpus_root: Path | None = None) -> str:
    root = (
        corpus_root or REPOSITORY_ROOT / "examples" / "agentic_hvac_bakeoff"
    ).resolve()
    inputs = [
        root / "manifest.json",
        root / "datasets" / "hvac_bakeoff.duckdb",
        *(path for path in (root / "docs" / "source").rglob("*") if path.is_file()),
    ]
    return _content_fingerprint(inputs, root=root)


def load_benchmark(path: str | Path) -> dict[str, Any]:
    benchmark_path = Path(path).resolve()
    try:
        payload = json.loads(benchmark_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationContractError(
            f"Cannot read Agentic RAG benchmark: {benchmark_path}"
        ) from exc
    if payload.get("schema_version") != "1.0":
        raise EvaluationContractError(
            "Agentic RAG benchmark schema_version must be 1.0"
        )
    if payload.get("fully_synthetic") is not True:
        raise EvaluationContractError("Agentic RAG benchmark must be fully synthetic")
    if payload.get("candidate_neutral") is not True:
        raise EvaluationContractError("Agentic RAG benchmark must be candidate neutral")
    if payload.get("license") != "CC0-1.0":
        raise EvaluationContractError("Agentic RAG benchmark license must be CC0-1.0")
    benchmark_id = payload.get("benchmark_id")
    cases = payload.get("cases")
    if not isinstance(benchmark_id, str) or not benchmark_id.strip():
        raise EvaluationContractError("Agentic RAG benchmark_id is required")
    if not isinstance(cases, list) or not cases:
        raise EvaluationContractError("Agentic RAG benchmark cases are required")
    case_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise EvaluationContractError("Every benchmark case must be an object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in case_ids:
            raise EvaluationContractError("Benchmark case IDs must be unique strings")
        case_ids.add(case_id)
        if case.get("category") not in {
            "knowledge",
            "data",
            "combined",
            "clarification",
            "safety",
            "presentation",
        }:
            raise EvaluationContractError(f"Unsupported category for {case_id}")
        if not isinstance(case.get("question"), str) or not case["question"].strip():
            raise EvaluationContractError(f"Question is required for {case_id}")
        if not isinstance(case.get("evidence_contract"), list):
            raise EvaluationContractError(
                f"Evidence contract must be a list for {case_id}"
            )
        tool_contract = case.get("tool_contract")
        if tool_contract is not None and (
            not isinstance(tool_contract, list)
            or not tool_contract
            or not all(isinstance(tool, str) and tool for tool in tool_contract)
            or not set(tool_contract) <= _KNOWN_TOOLS
        ):
            raise EvaluationContractError(
                f"Tool contract must be a non-empty string list for {case_id}"
            )
    return payload


def _expected_tools(case: dict[str, Any]) -> set[str]:
    explicit_contract = case.get("tool_contract")
    if isinstance(explicit_contract, list):
        return {str(tool) for tool in explicit_contract}
    case_id = str(case["id"])
    category = str(case["category"])
    if category == "knowledge":
        return {"search_project_knowledge"}
    if category == "data":
        return {"query_hvac_database"}
    if category == "combined":
        return {"query_hvac_database", "search_project_knowledge"}
    if category == "clarification":
        return {"ask_for_clarification"}
    if category == "presentation":
        return set(_PRESENTATION_TOOLS[case_id])
    return set()


def _expected_citation(evidence: str) -> str | None:
    if evidence in _DATA_EVIDENCE_TO_CITATION:
        return _DATA_EVIDENCE_TO_CITATION[evidence]
    if evidence == "project boundary":
        return None
    return Path(evidence).name


def _model_provider_failure(response: dict[str, Any]) -> str | None:
    for activity in response.get("activities", []):
        if not isinstance(activity, dict):
            continue
        if activity.get("tool") != "agent" or activity.get("status") != "failed":
            continue
        summary = str(activity.get("summary", ""))
        if any(marker in summary for marker in _MODEL_PROVIDER_FAILURE_MARKERS):
            return summary[:500]
    return None


def _is_model_provider_exception(exc: BaseException) -> bool:
    if isinstance(exc, ModelProviderFailure | httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or status_code >= 500
    return False


def score_case(case: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    category = str(case["category"])
    grounding_status = str(response.get("grounding_status", ""))
    refused = bool(response.get("refused", False))
    clarification = bool(response.get("clarification", False))
    answer = str(response.get("answer_markdown", "")).strip()
    if category == "safety":
        behavior_pass = refused and grounding_status in {"failed", "refused"}
    elif category == "clarification":
        behavior_pass = clarification and grounding_status == "clarification"
    else:
        behavior_pass = bool(answer) and not refused and grounding_status == "grounded"

    completed_tools = {
        str(activity.get("tool"))
        for activity in response.get("activities", [])
        if isinstance(activity, dict) and activity.get("status") == "completed"
    }
    expected_tools = _expected_tools(case)
    missing_tools = expected_tools - completed_tools
    explicit_tool_contract = isinstance(case.get("tool_contract"), list)
    unexpected_tools = (
        completed_tools - expected_tools if explicit_tool_contract else set()
    )
    if (
        not isinstance(case.get("tool_contract"), list)
        and "query_hvac_database" in missing_tools
        and {
            "inspect_hvac_snapshot",
            "inspect_metric_extreme",
        }
        & completed_tools
    ):
        missing_tools.remove("query_hvac_database")
    if category == "safety":
        tool_contract_pass = behavior_pass
    else:
        tool_contract_pass = not missing_tools and not unexpected_tools

    citation_names = {
        str(citation.get("filename"))
        for citation in response.get("citations", [])
        if isinstance(citation, dict) and citation.get("filename")
    }
    expected_citations = [
        citation
        for citation in (
            _expected_citation(str(evidence)) for evidence in case["evidence_contract"]
        )
        if citation is not None
    ]
    evidence_applicable = category not in {"clarification", "safety"}
    evidence_hits = [
        citation for citation in expected_citations if citation in citation_names
    ]
    missing_evidence = [
        citation for citation in expected_citations if citation not in citation_names
    ]
    evidence_recall = (
        round(len(evidence_hits) / len(expected_citations), 6)
        if evidence_applicable and expected_citations
        else None
    )
    evidence_contract_pass = (
        not missing_evidence if evidence_applicable and expected_citations else None
    )
    return {
        "behavior_pass": behavior_pass,
        "expected_tools": sorted(expected_tools),
        "completed_tools": sorted(completed_tools),
        "missing_tools": sorted(missing_tools),
        "unexpected_tools": sorted(unexpected_tools),
        "tool_contract_pass": tool_contract_pass,
        "evidence_applicable": evidence_applicable,
        "expected_citations": expected_citations,
        "citation_names": sorted(citation_names),
        "evidence_hits": evidence_hits,
        "missing_evidence": missing_evidence,
        "evidence_recall": evidence_recall,
        "evidence_contract_pass": evidence_contract_pass,
    }


def _summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [case for case in cases if case["status"] == "completed"]
    evidence_cases = [
        case
        for case in completed
        if case["checks"]["evidence_contract_pass"] is not None
    ]
    behavior_pass_count = sum(
        bool(case["checks"]["behavior_pass"]) for case in completed
    )
    tool_pass_count = sum(
        bool(case["checks"]["tool_contract_pass"]) for case in completed
    )
    evidence_pass_count = sum(
        bool(case["checks"]["evidence_contract_pass"]) for case in evidence_cases
    )
    evidence_recalls = [
        float(case["checks"]["evidence_recall"])
        for case in evidence_cases
        if case["checks"]["evidence_recall"] is not None
    ]

    def rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 6) if denominator else 0.0

    return {
        "case_count": len(cases),
        "completed_count": len(completed),
        "execution_failure_count": len(cases) - len(completed),
        "behavior_pass_count": behavior_pass_count,
        "behavior_pass_rate": rate(behavior_pass_count, len(completed)),
        "tool_contract_pass_count": tool_pass_count,
        "tool_contract_pass_rate": rate(tool_pass_count, len(completed)),
        "evidence_contract_case_count": len(evidence_cases),
        "evidence_contract_pass_count": evidence_pass_count,
        "evidence_contract_pass_rate": rate(evidence_pass_count, len(evidence_cases)),
        "evidence_recall_macro": (
            round(sum(evidence_recalls) / len(evidence_recalls), 6)
            if evidence_recalls
            else 0.0
        ),
    }


def _http_asker(endpoint: str, timeout_seconds: float) -> tuple[AskFunction, Any]:
    client = httpx.Client(timeout=timeout_seconds, trust_env=False)

    def ask(question: str) -> dict[str, Any]:
        response = client.post(
            endpoint,
            headers={"X-Project-Copilot": "1"},
            json={"question": question, "history": []},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Candidate response must be a JSON object")
        return payload

    return ask, client


def run_candidate_benchmark(
    *,
    benchmark_path: str | Path,
    output_path: str | Path,
    candidate_id: str,
    endpoint: str,
    ask: AskFunction | None = None,
    resume: bool = False,
    timeout_seconds: float = 135.0,
    selected_case_ids: set[str] | None = None,
    model_label: str | None = None,
    candidate_revision: str | None = None,
) -> dict[str, Any]:
    resolved_benchmark = Path(benchmark_path).resolve()
    benchmark = load_benchmark(resolved_benchmark)
    output = Path(output_path).resolve()
    resolved_candidate_revision = candidate_revision or _default_candidate_revision()
    provenance = {
        "model_label": model_label,
        "candidate_revision": resolved_candidate_revision,
        "benchmark_sha256": _sha256_file(resolved_benchmark),
        "corpus_sha256": _corpus_sha256(),
        "scorer_version": SCORER_VERSION,
    }
    cases = [
        case
        for case in benchmark["cases"]
        if selected_case_ids is None or case["id"] in selected_case_ids
    ]
    if selected_case_ids is not None and len(cases) != len(selected_case_ids):
        missing = selected_case_ids - {str(case["id"]) for case in cases}
        raise EvaluationContractError(f"Unknown selected case IDs: {sorted(missing)}")

    started_at = datetime.now(UTC).isoformat()
    previous_cases: dict[str, dict[str, Any]] = {}
    if resume and output.is_file():
        previous = json.loads(output.read_text(encoding="utf-8"))
        if previous.get("benchmark_id") != benchmark["benchmark_id"]:
            raise EvaluationContractError("Resume benchmark_id does not match")
        if previous.get("candidate_id") != candidate_id:
            raise EvaluationContractError("Resume candidate_id does not match")
        if previous.get("endpoint") != endpoint:
            raise EvaluationContractError("Resume endpoint does not match")
        previous_provenance = previous.get("provenance")
        if not isinstance(previous_provenance, dict):
            raise EvaluationContractError("Resume provenance is missing")
        for field, current_value in provenance.items():
            if previous_provenance.get(field) != current_value:
                raise EvaluationContractError(
                    f"Resume {field} does not match current run"
                )
        started_at = str(previous.get("started_at", started_at))
        previous_case_ids = {
            str(case["id"])
            for case in previous.get("cases", [])
            if isinstance(case, dict) and case.get("id") is not None
        }
        if selected_case_ids is not None and previous_case_ids != selected_case_ids:
            raise EvaluationContractError(
                "Resume selected case set does not match the existing result; "
                "use a new output path for a subset run"
            )
        previous_cases = {
            str(case["id"]): case
            for case in previous.get("cases", [])
            if isinstance(case, dict) and case.get("status") == "completed"
        }

    closer: Any = None
    if ask is None:
        ask, closer = _http_asker(endpoint, timeout_seconds)
    assert ask is not None

    results: list[dict[str, Any]] = []

    def checkpoint() -> dict[str, Any]:
        report = {
            "schema_version": "1.0",
            "benchmark_id": benchmark["benchmark_id"],
            "candidate_id": candidate_id,
            "endpoint": endpoint,
            "model_label": model_label,
            "provenance": provenance,
            "started_at": started_at,
            "updated_at": datetime.now(UTC).isoformat(),
            "fully_synthetic": True,
            "license": "CC0-1.0",
            "cases": results,
            "summary": _summary(results),
        }
        _atomic_json_write(output, report)
        return report

    try:
        consecutive_model_provider_failures = 0
        for case in cases:
            case_id = str(case["id"])
            if case_id in previous_cases:
                retained = dict(previous_cases[case_id])
                response = retained.get("response")
                if not isinstance(response, dict):
                    raise EvaluationContractError(
                        f"Resume response is invalid for {case_id}"
                    )
                retained.update(
                    {
                        "category": case["category"],
                        "question": case["question"],
                        "expected": case.get("expected"),
                        "evidence_contract": case["evidence_contract"],
                        "checks": score_case(case, response),
                    }
                )
                results.append(retained)
                checkpoint()
                continue
            started = perf_counter()
            model_provider_failure = False
            try:
                response = ask(str(case["question"]))
                provider_error = _model_provider_failure(response)
                if provider_error is not None:
                    raise ModelProviderFailure(provider_error)
                latency_ms = round((perf_counter() - started) * 1_000, 3)
                result = {
                    "id": case_id,
                    "category": case["category"],
                    "question": case["question"],
                    "expected": case.get("expected"),
                    "evidence_contract": case["evidence_contract"],
                    "status": "completed",
                    "latency_ms": latency_ms,
                    "response": response,
                    "checks": score_case(case, response),
                }
            except Exception as exc:  # noqa: BLE001 - benchmark must retain failures
                model_provider_failure = _is_model_provider_exception(exc)
                latency_ms = round((perf_counter() - started) * 1_000, 3)
                result = {
                    "id": case_id,
                    "category": case["category"],
                    "question": case["question"],
                    "expected": case.get("expected"),
                    "evidence_contract": case["evidence_contract"],
                    "status": "error",
                    "latency_ms": latency_ms,
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc)[:500],
                        "model_provider_failure": model_provider_failure,
                    },
                }
            if model_provider_failure:
                consecutive_model_provider_failures += 1
            else:
                consecutive_model_provider_failures = 0
            results.append(result)
            report = checkpoint()
            print(
                f"[{len(results)}/{len(cases)}] {case_id} {result['status']} "
                f"{latency_ms:.0f} ms"
            )
            if consecutive_model_provider_failures >= 2:
                report = checkpoint()
                report["aborted_at"] = datetime.now(UTC).isoformat()
                report["abort_reason"] = "consecutive_model_provider_failures"
                _atomic_json_write(output, report)
                return report
        report = checkpoint()
        if len(results) == len(cases) and all(
            result["status"] == "completed" for result in results
        ):
            report["completed_at"] = datetime.now(UTC).isoformat()
        _atomic_json_write(output, report)
        return report
    finally:
        if closer is not None:
            closer.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a resumable live Agentic RAG candidate benchmark."
    )
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--candidate-id", default="haystack-duckdb")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--model-label")
    parser.add_argument("--candidate-revision")
    parser.add_argument("--timeout-seconds", type=float, default=135.0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--case-id", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_candidate_benchmark(
        benchmark_path=args.benchmark,
        output_path=args.output,
        candidate_id=args.candidate_id,
        endpoint=args.endpoint,
        resume=args.resume,
        timeout_seconds=args.timeout_seconds,
        selected_case_ids=set(args.case_id) if args.case_id else None,
        model_label=args.model_label,
        candidate_revision=args.candidate_revision,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 1 if report["summary"]["execution_failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
