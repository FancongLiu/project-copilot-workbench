from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation.run_agentic_rag_candidate import (  # noqa: E402
    _expected_citation,
    _model_provider_failure,
)
from evaluation.run_offline import (  # noqa: E402
    EvaluationContractError,
    _atomic_json_write,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCHMARK = (
    REPOSITORY_ROOT / "evaluation" / "four_version_complex_questions.json"
)
DEFAULT_OUTPUT = (
    REPOSITORY_ROOT / "evaluation" / "results" / "four-version-shared-backend-live.json"
)
DEFAULT_ENDPOINT = "http://127.0.0.1:8788/api/direction/query"
SCORER_VERSION = "1.0"
DEFAULT_FORBIDDEN_RAW_PATH_PATTERNS = (
    "background/",
    "datasets/",
    "docs/",
    "project.local/",
    "runtime/",
    "src/",
    "C:\\",
    "D:\\",
    "E:\\",
)
TYPED_DATABASE_TOOLS = {
    "inspect_configuration_change_effect",
    "inspect_configuration_history",
    "inspect_hvac_snapshot",
    "inspect_metric_extreme",
}

History = list[dict[str, str]]
AskFunction = Callable[[str, History], dict[str, Any]]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def load_complex_benchmark(path: str | Path) -> dict[str, Any]:
    benchmark_path = Path(path).resolve()
    try:
        payload = json.loads(benchmark_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationContractError(
            f"Cannot read four-version benchmark: {benchmark_path}"
        ) from exc
    if payload.get("schema_version") != "1.0":
        raise EvaluationContractError("Benchmark schema_version must be 1.0")
    if payload.get("fully_synthetic") is not True:
        raise EvaluationContractError("Benchmark must be fully synthetic")
    if payload.get("shared_backend") is not True:
        raise EvaluationContractError("Four versions must declare one shared backend")
    if payload.get("architectures") != [
        "baseline",
        "conversation",
        "evidence",
        "canvas",
    ]:
        raise EvaluationContractError("Benchmark architecture order is not frozen")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvaluationContractError("Benchmark cases are required")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise EvaluationContractError("Each benchmark case must be an object")
        case_id = case.get("case_id")
        turns = case.get("user_turns")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise EvaluationContractError("Case IDs must be unique strings")
        seen.add(case_id)
        if (
            not isinstance(turns, list)
            or not turns
            or not all(isinstance(turn, str) and turn.strip() for turn in turns)
        ):
            raise EvaluationContractError(f"User turns are required for {case_id}")
        for field in (
            "required_sources",
            "expected_tools",
            "forbidden_tools",
            "expected_facts",
            "forbidden_raw_path_patterns",
        ):
            value = case.get(field, [])
            if not isinstance(value, list) or not all(
                isinstance(item, str) and item for item in value
            ):
                raise EvaluationContractError(
                    f"{field} must be a string list for {case_id}"
                )
    return payload


def _normalized(value: str) -> str:
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", value.casefold())


def _fact_present(answer: str, expected: str) -> bool:
    normalized_answer = _normalized(answer)
    tokens = [token for token in re.split(r"\s+", expected.strip()) if token]
    return bool(tokens) and all(
        _normalized(token) in normalized_answer for token in tokens
    )


def _completed_tools(responses: list[dict[str, Any]]) -> set[str]:
    return {
        str(activity.get("tool"))
        for response in responses
        for activity in response.get("activities", [])
        if isinstance(activity, dict) and activity.get("status") == "completed"
    }


def _citation_names(responses: list[dict[str, Any]]) -> set[str]:
    return {
        str(citation.get("filename"))
        for response in responses
        for citation in response.get("citations", [])
        if isinstance(citation, dict) and citation.get("filename")
    }


def _clarification_policy_pass(
    policy: str,
    responses: list[dict[str, Any]],
) -> bool:
    clarification_count = sum(
        bool(response.get("clarification", False)) for response in responses
    )
    normalized_policy = policy.casefold()
    if "one minimal clarification" in normalized_policy:
        return clarification_count == 1
    if "no clarification" in normalized_policy or "forbidden" in normalized_policy:
        return clarification_count == 0
    if "inherit unchanged context" in normalized_policy:
        return clarification_count == 0
    return True


def score_complex_case(
    case: dict[str, Any],
    responses: list[dict[str, Any]],
) -> dict[str, Any]:
    answer = "\n\n".join(
        str(response.get("answer_markdown", "")) for response in responses
    )
    completed_tools = _completed_tools(responses)
    expected_tools = {str(tool) for tool in case.get("expected_tools", [])}
    forbidden_tools = {str(tool) for tool in case.get("forbidden_tools", [])}
    expected_tool_hits = expected_tools & completed_tools
    if (
        "query_hvac_database" in expected_tools
        and TYPED_DATABASE_TOOLS & completed_tools
    ):
        expected_tool_hits.add("query_hvac_database")
    forbidden_tool_hits = forbidden_tools & completed_tools

    citation_names = _citation_names(responses)
    expected_citations = {
        citation
        for source in case.get("required_sources", [])
        if (citation := _expected_citation(str(source))) is not None
    }
    citation_hits = expected_citations & citation_names

    expected_facts = [str(fact) for fact in case.get("expected_facts", [])]
    fact_hits = [fact for fact in expected_facts if _fact_present(answer, fact)]
    forbidden_patterns = sorted(
        {
            *DEFAULT_FORBIDDEN_RAW_PATH_PATTERNS,
            *(str(pattern) for pattern in case.get("forbidden_raw_path_patterns", [])),
        }
    )
    raw_path_hits = [
        pattern
        for pattern in forbidden_patterns
        if pattern.casefold() in answer.casefold()
    ]
    policy_pass = _clarification_policy_pass(
        str(case.get("clarification_policy", "")), responses
    )
    provider_failures = [
        failure
        for response in responses
        if (failure := _model_provider_failure(response)) is not None
    ]
    expected_tool_recall = (
        round(len(expected_tool_hits) / len(expected_tools), 6)
        if expected_tools
        else 1.0
    )
    required_source_recall = (
        round(len(citation_hits) / len(expected_citations), 6)
        if expected_citations
        else 1.0
    )
    expected_fact_recall = (
        round(len(fact_hits) / len(expected_facts), 6) if expected_facts else None
    )
    requires_table = bool(case.get("expected_table_schema"))
    requires_chart = bool(case.get("expected_chart_spec"))
    has_table = any(response.get("tables") for response in responses)
    has_chart = any(response.get("charts") for response in responses)
    expected_table_columns = {
        _normalized(str(column)) for column in case.get("expected_table_schema", [])
    }
    table_column_sets = [
        {_normalized(str(column)) for column in table.get("columns", [])}
        for response in responses
        for table in response.get("tables", [])
        if isinstance(table, dict)
    ]
    table_schema_pass = not expected_table_columns or any(
        all(
            any(expected in actual for actual in columns)
            for expected in expected_table_columns
        )
        for columns in table_column_sets
    )
    presentation_pass = (not requires_table or (has_table and table_schema_pass)) and (
        not requires_chart or has_chart
    )
    expected_fact_pass = expected_fact_recall in {None, 1.0}
    manual_review_fields = [
        field
        for field in (
            "required_subquestions",
            "required_uncertainty",
            "expected_time_window",
            "expected_chart_spec",
        )
        if case.get(field)
    ]
    expected_minimal_clarification = (
        "one minimal clarification"
        in str(case.get("clarification_policy", "")).casefold()
    )
    response_behavior_pass = bool(responses) and all(
        str(response.get("answer_markdown", "")).strip()
        and not bool(response.get("refused", False))
        and (
            bool(response.get("clarification", False))
            if expected_minimal_clarification
            else str(response.get("grounding_status", "")) == "grounded"
        )
        for response in responses
    )
    hard_gate_pass = all(
        (
            response_behavior_pass,
            policy_pass,
            not raw_path_hits,
            not forbidden_tool_hits,
            not provider_failures,
            expected_tool_recall == 1.0,
            required_source_recall == 1.0,
            expected_fact_pass,
            presentation_pass,
        )
    )
    return {
        "response_behavior_pass": response_behavior_pass,
        "clarification_policy_pass": policy_pass,
        "completed_tools": sorted(completed_tools),
        "expected_tools": sorted(expected_tools),
        "missing_tools": sorted(expected_tools - expected_tool_hits),
        "expected_tool_recall": expected_tool_recall,
        "forbidden_tool_hits": sorted(forbidden_tool_hits),
        "citation_names": sorted(citation_names),
        "expected_citations": sorted(expected_citations),
        "missing_citations": sorted(expected_citations - citation_names),
        "required_source_recall": required_source_recall,
        "expected_fact_hits": fact_hits,
        "missing_expected_facts": [
            fact for fact in expected_facts if fact not in fact_hits
        ],
        "expected_fact_recall": expected_fact_recall,
        "raw_path_hits": raw_path_hits,
        "raw_path_leak_count": len(raw_path_hits),
        "requires_table": requires_table,
        "has_table": has_table,
        "expected_table_columns": sorted(expected_table_columns),
        "table_schema_pass": table_schema_pass,
        "requires_chart": requires_chart,
        "has_chart": has_chart,
        "presentation_pass": presentation_pass,
        "model_provider_failures": provider_failures,
        "manual_review_fields": manual_review_fields,
        "quality_review_status": (
            "manual_review_required" if manual_review_fields else "automatic_only"
        ),
        "hard_gate_pass": hard_gate_pass,
    }


def _http_asker(endpoint: str, timeout_seconds: float) -> tuple[AskFunction, Any]:
    client = httpx.Client(timeout=timeout_seconds, trust_env=False)

    def ask(question: str, history: History) -> dict[str, Any]:
        response = client.post(
            endpoint,
            headers={"X-Project-Copilot": "1"},
            json={"question": question, "history": history},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Candidate response must be a JSON object")
        return payload

    return ask, client


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [result for result in results if result["status"] == "completed"]
    hard_gate_pass_count = sum(
        bool(result["checks"]["hard_gate_pass"]) for result in completed
    )
    raw_path_leak_count = sum(
        int(result["checks"]["raw_path_leak_count"]) for result in completed
    )
    unnecessary_clarification_count = sum(
        not bool(result["checks"]["clarification_policy_pass"]) for result in completed
    )
    manual_review_case_count = sum(
        bool(result["checks"]["manual_review_fields"]) for result in completed
    )

    def rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 6) if denominator else 0.0

    return {
        "case_count": len(results),
        "completed_count": len(completed),
        "execution_failure_count": len(results) - len(completed),
        "hard_gate_pass_count": hard_gate_pass_count,
        "hard_gate_pass_rate": rate(hard_gate_pass_count, len(completed)),
        "raw_path_leak_count": raw_path_leak_count,
        "clarification_policy_failure_count": unnecessary_clarification_count,
        "manual_review_case_count": manual_review_case_count,
    }


def run_complex_benchmark(
    *,
    benchmark_path: str | Path,
    output_path: str | Path,
    endpoint: str,
    ask: AskFunction | None = None,
    timeout_seconds: float = 180.0,
    selected_case_ids: set[str] | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    resolved_benchmark = Path(benchmark_path).resolve()
    benchmark = load_complex_benchmark(resolved_benchmark)
    output = Path(output_path).resolve()
    cases = [
        case
        for case in benchmark["cases"]
        if selected_case_ids is None or case["case_id"] in selected_case_ids
    ]
    if selected_case_ids is not None and len(cases) != len(selected_case_ids):
        missing = selected_case_ids - {str(case["case_id"]) for case in cases}
        raise EvaluationContractError(f"Unknown selected case IDs: {sorted(missing)}")

    benchmark_sha256 = _sha256_file(resolved_benchmark)
    started_at = datetime.now(UTC).isoformat()
    previous_cases: dict[str, dict[str, Any]] = {}
    if resume and output.is_file():
        previous = json.loads(output.read_text(encoding="utf-8"))
        if previous.get("benchmark_id") != benchmark["benchmark_id"]:
            raise EvaluationContractError("Resume benchmark_id does not match")
        if previous.get("endpoint") != endpoint:
            raise EvaluationContractError("Resume endpoint does not match")
        previous_provenance = previous.get("provenance", {})
        if previous_provenance.get("benchmark_sha256") != benchmark_sha256:
            raise EvaluationContractError("Resume benchmark content does not match")
        previous_case_ids = {
            str(result.get("case_id"))
            for result in previous.get("cases", [])
            if isinstance(result, dict) and result.get("case_id")
        }
        selected_ids = {str(case["case_id"]) for case in cases}
        if previous_case_ids - selected_ids:
            raise EvaluationContractError(
                "Resume result contains cases outside the selected case set"
            )
        started_at = str(previous.get("started_at", started_at))
        previous_cases = {
            str(result["case_id"]): result
            for result in previous.get("cases", [])
            if isinstance(result, dict) and result.get("status") == "completed"
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
            "endpoint": endpoint,
            "shared_backend": True,
            "architectures": benchmark["architectures"],
            "provenance": {
                "benchmark_sha256": benchmark_sha256,
                "scorer_version": SCORER_VERSION,
            },
            "started_at": started_at,
            "updated_at": datetime.now(UTC).isoformat(),
            "cases": results,
            "summary": _summary(results),
        }
        _atomic_json_write(output, report)
        return report

    try:
        for index, case in enumerate(cases, start=1):
            case_id = str(case["case_id"])
            if case_id in previous_cases:
                retained = dict(previous_cases[case_id])
                responses = [
                    turn.get("response", {})
                    for turn in retained.get("turns", [])
                    if isinstance(turn, dict) and isinstance(turn.get("response"), dict)
                ]
                retained["checks"] = score_complex_case(case, responses)
                results.append(retained)
                report = checkpoint()
                print(f"[{index}/{len(cases)}] {case_id} resumed")
                continue
            history: History = []
            responses: list[dict[str, Any]] = []
            turns: list[dict[str, Any]] = []
            started = perf_counter()
            try:
                for question in case["user_turns"]:
                    request_history_count = len(history)
                    turn_started = perf_counter()
                    response = ask(str(question), list(history))
                    responses.append(response)
                    answer = str(response.get("answer_markdown", ""))
                    turns.append(
                        {
                            "question": question,
                            "request_history_count": request_history_count,
                            "latency_ms": round(
                                (perf_counter() - turn_started) * 1_000, 3
                            ),
                            "response": response,
                        }
                    )
                    history.extend(
                        [
                            {"role": "user", "content": str(question)},
                            {"role": "assistant", "content": answer},
                        ]
                    )
                result = {
                    "case_id": case_id,
                    "category": case["category"],
                    "status": "completed",
                    "latency_ms": round((perf_counter() - started) * 1_000, 3),
                    "turns": turns,
                    "checks": score_complex_case(case, responses),
                }
            except Exception as exc:  # noqa: BLE001 - preserve every failed case
                result = {
                    "case_id": case_id,
                    "category": case["category"],
                    "status": "error",
                    "latency_ms": round((perf_counter() - started) * 1_000, 3),
                    "turns": turns,
                    "error": {"type": type(exc).__name__, "message": str(exc)[:500]},
                }
            results.append(result)
            report = checkpoint()
            print(
                f"[{index}/{len(cases)}] {case_id} {result['status']} "
                f"{result['latency_ms']:.0f} ms"
            )
        if (
            len(results) == len(cases)
            and not report["summary"]["execution_failure_count"]
        ):
            report["completed_at"] = datetime.now(UTC).isoformat()
            _atomic_json_write(output, report)
        return report
    finally:
        if closer is not None:
            closer.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the shared-backend complex benchmark for all four UI architectures."
    )
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--resume", action="store_true")
    return parser


def benchmark_exit_code(report: dict[str, Any]) -> int:
    summary = report["summary"]
    if summary["execution_failure_count"]:
        return 1
    return 0 if summary["hard_gate_pass_count"] == summary["case_count"] else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_complex_benchmark(
        benchmark_path=args.benchmark,
        output_path=args.output,
        endpoint=args.endpoint,
        timeout_seconds=args.timeout_seconds,
        selected_case_ids=set(args.case_id) if args.case_id else None,
        resume=args.resume,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return benchmark_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
