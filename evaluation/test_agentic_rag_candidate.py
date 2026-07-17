from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from evaluation.run_offline import EvaluationContractError
from evaluation.run_agentic_rag_candidate import (
    _candidate_input_paths,
    _corpus_sha256,
    run_candidate_benchmark,
    score_case,
)


def _manifest(tmp_path: Path) -> Path:
    path = tmp_path / "benchmark.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "benchmark_id": "candidate-test",
                "fully_synthetic": True,
                "candidate_neutral": True,
                "license": "CC0-1.0",
                "cases": [
                    {
                        "id": "K01",
                        "category": "knowledge",
                        "question": "当前配置？",
                        "expected": "10 C",
                        "evidence_contract": ["change-register.md"],
                    },
                    {
                        "id": "C01",
                        "category": "combined",
                        "question": "为什么改，效果如何？",
                        "expected": "引用会议并报告效果",
                        "evidence_contract": [
                            "controls-review.md",
                            "telemetry_clean",
                        ],
                        "tool_contract": [
                            "search_project_knowledge",
                            "inspect_configuration_change_effect",
                        ],
                    },
                    {
                        "id": "Q01",
                        "category": "clarification",
                        "question": "哪个更节能？",
                        "expected": "询问口径",
                        "evidence_contract": ["telemetry_clean"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_candidate_runner_scores_behavior_tools_and_evidence(tmp_path: Path) -> None:
    responses = {
        "当前配置？": {
            "answer_markdown": "### 结论\n\n当前是 10 C。",
            "grounding_status": "grounded",
            "refused": False,
            "clarification": False,
            "citations": [
                {
                    "filename": "change-register.md",
                    "location": "decisions/change-register.md",
                    "excerpt": "CR-017 is current.",
                }
            ],
            "activities": [{"tool": "search_project_knowledge", "status": "completed"}],
            "tables": [],
            "charts": [],
        },
        "为什么改，效果如何？": {
            "answer_markdown": "### 结论\n\n会议批准，数据效果已计算。",
            "grounding_status": "grounded",
            "refused": False,
            "clarification": False,
            "citations": [
                {
                    "filename": "controls-review.md",
                    "location": "meetings/controls-review.md",
                    "excerpt": "The team approved CR-017.",
                },
                {
                    "filename": "telemetry.csv",
                    "location": "datasets/telemetry.csv",
                    "excerpt": "Read-only synthetic telemetry.",
                },
            ],
            "activities": [
                {"tool": "search_project_knowledge", "status": "completed"},
                {
                    "tool": "inspect_configuration_change_effect",
                    "status": "completed",
                },
            ],
            "tables": [{"columns": ["窗口"], "rows": [["变更后"]]}],
            "charts": [],
        },
        "哪个更节能？": {
            "answer_markdown": "请补充时间范围和负荷口径。",
            "grounding_status": "clarification",
            "refused": False,
            "clarification": True,
            "citations": [],
            "activities": [{"tool": "ask_for_clarification", "status": "completed"}],
            "tables": [],
            "charts": [],
        },
    }

    report = run_candidate_benchmark(
        benchmark_path=_manifest(tmp_path),
        output_path=tmp_path / "result.json",
        candidate_id="haystack-duckdb",
        endpoint="http://candidate.invalid/api/direction/query",
        ask=lambda question: responses[question],
    )

    assert report["summary"] == {
        "case_count": 3,
        "completed_count": 3,
        "execution_failure_count": 0,
        "behavior_pass_count": 3,
        "behavior_pass_rate": 1.0,
        "tool_contract_pass_count": 3,
        "tool_contract_pass_rate": 1.0,
        "evidence_contract_case_count": 2,
        "evidence_contract_pass_count": 2,
        "evidence_contract_pass_rate": 1.0,
        "evidence_recall_macro": 1.0,
    }
    combined = report["cases"][1]
    assert combined["checks"]["expected_tools"] == [
        "inspect_configuration_change_effect",
        "search_project_knowledge",
    ]
    assert combined["checks"]["missing_evidence"] == []
    assert combined["response"]["tables"]


def test_candidate_runner_accepts_typed_snapshot_inspection_as_data_tool() -> None:
    checks = score_case(
        {
            "id": "D02",
            "category": "data",
            "question": "哪台机组缺数据？",
            "expected": "HP-02",
            "evidence_contract": ["telemetry_raw"],
        },
        {
            "answer_markdown": "HP-02 缺 60 个样本。",
            "grounding_status": "grounded",
            "refused": False,
            "clarification": False,
            "citations": [{"filename": "telemetry.csv"}],
            "activities": [{"tool": "inspect_hvac_snapshot", "status": "completed"}],
        },
    )

    assert checks["tool_contract_pass"] is True
    assert checks["missing_tools"] == []


def test_candidate_runner_accepts_typed_configuration_as_query_tool() -> None:
    checks = score_case(
        {
            "id": "P01",
            "category": "presentation",
            "question": "把配置前后做成表格。",
            "expected": "12 C to 10 C",
            "evidence_contract": ["config_history"],
            "tool_contract": ["inspect_configuration_history"],
        },
        {
            "answer_markdown": "配置由 12 C 变为 10 C。",
            "grounding_status": "grounded",
            "refused": False,
            "clarification": False,
            "citations": [{"filename": "config_history.csv"}],
            "activities": [
                {
                    "tool": "inspect_configuration_history",
                    "status": "completed",
                }
            ],
        },
    )

    assert checks["tool_contract_pass"] is True
    assert checks["missing_tools"] == []


def test_candidate_runner_accepts_typed_configuration_effect_contract() -> None:
    checks = score_case(
        {
            "id": "D11",
            "category": "data",
            "question": "HP-02 改设定前后效果如何？",
            "expected": "送风均值降低 1.9 C，电耗增加 4 kWh",
            "evidence_contract": ["telemetry_clean", "config_history"],
            "tool_contract": ["inspect_configuration_change_effect"],
        },
        {
            "answer_markdown": "送风均值降低 1.9 C，电耗增加 4 kWh。",
            "grounding_status": "grounded",
            "refused": False,
            "clarification": False,
            "citations": [
                {"filename": "telemetry.csv"},
                {"filename": "config_history.csv"},
            ],
            "activities": [
                {
                    "tool": "inspect_configuration_change_effect",
                    "status": "completed",
                }
            ],
        },
    )

    assert checks["tool_contract_pass"] is True
    assert checks["missing_tools"] == []


def test_candidate_runner_does_not_substitute_history_for_configuration_effect() -> (
    None
):
    checks = score_case(
        {
            "id": "D11",
            "category": "data",
            "question": "HP-02 改设定前后效果如何？",
            "expected": "送风均值降低 1.9 C，电耗增加 4 kWh",
            "evidence_contract": ["telemetry_clean", "config_history"],
            "tool_contract": ["inspect_configuration_change_effect"],
        },
        {
            "answer_markdown": "配置由 12 C 变为 10 C。",
            "grounding_status": "grounded",
            "refused": False,
            "clarification": False,
            "citations": [{"filename": "config_history.csv"}],
            "activities": [
                {"tool": "inspect_configuration_history", "status": "completed"}
            ],
        },
    )

    assert checks["tool_contract_pass"] is False
    assert checks["missing_tools"] == ["inspect_configuration_change_effect"]


def test_candidate_runner_rejects_extra_tool_for_explicit_contract() -> None:
    checks = score_case(
        {
            "id": "D11",
            "category": "data",
            "question": "HP-02 改设定前后效果如何？",
            "expected": "送风均值降低 1.9 C，电耗增加 4 kWh",
            "evidence_contract": ["telemetry_clean", "config_history"],
            "tool_contract": ["inspect_configuration_change_effect"],
        },
        {
            "answer_markdown": "送风均值降低 1.9 C，电耗增加 4 kWh。",
            "grounding_status": "grounded",
            "refused": False,
            "clarification": False,
            "citations": [
                {"filename": "telemetry.csv"},
                {"filename": "config_history.csv"},
            ],
            "activities": [
                {
                    "tool": "inspect_configuration_change_effect",
                    "status": "completed",
                },
                {"tool": "query_hvac_database", "status": "completed"},
            ],
        },
    )

    assert checks["tool_contract_pass"] is False
    assert checks["missing_tools"] == []
    assert checks["unexpected_tools"] == ["query_hvac_database"]


def test_candidate_runner_does_not_substitute_unrelated_inspector_for_configuration() -> (
    None
):
    checks = score_case(
        {
            "id": "P01",
            "category": "presentation",
            "question": "把配置前后做成表格。",
            "expected": "12 C to 10 C",
            "evidence_contract": ["config_history", "controls-review.md"],
            "tool_contract": [
                "inspect_configuration_history",
                "search_project_knowledge",
            ],
        },
        {
            "answer_markdown": "配置由 12 C 变为 10 C。",
            "grounding_status": "grounded",
            "refused": False,
            "clarification": False,
            "citations": [
                {"filename": "config_history.csv"},
                {"filename": "controls-review.md"},
            ],
            "activities": [
                {"tool": "inspect_hvac_snapshot", "status": "completed"},
                {"tool": "search_project_knowledge", "status": "completed"},
            ],
        },
    )

    assert checks["tool_contract_pass"] is False
    assert checks["missing_tools"] == ["inspect_configuration_history"]


def test_candidate_runner_accepts_metric_extreme_as_data_tool() -> None:
    checks = score_case(
        {
            "id": "C04",
            "category": "combined",
            "question": "用 P_SUC 查低吸气压力时段。",
            "expected": "HP-04",
            "evidence_contract": ["point-dictionary.csv", "telemetry_clean"],
            "tool_contract": [
                "query_hvac_database",
                "inspect_metric_extreme",
            ],
        },
        {
            "answer_markdown": "P_SUC 映射到吸气压力，最低窗口属于 HP-04。",
            "grounding_status": "grounded",
            "refused": False,
            "clarification": False,
            "citations": [
                {"filename": "point-dictionary.csv"},
                {"filename": "telemetry.csv"},
            ],
            "activities": [
                {"tool": "query_hvac_database", "status": "completed"},
                {"tool": "inspect_metric_extreme", "status": "completed"},
            ],
        },
    )

    assert checks["tool_contract_pass"] is True
    assert checks["expected_tools"] == [
        "inspect_metric_extreme",
        "query_hvac_database",
    ]
    assert checks["missing_tools"] == []


def test_candidate_provenance_covers_policy_agent_and_all_knowledge_files(
    tmp_path: Path,
) -> None:
    input_names = {path.name for path in _candidate_input_paths()}
    assert {"agent.py", "direction.py", "sql_guard.py", "web.py"} <= input_names
    assert {"pyproject.toml", "requirements.runtime.lock"} <= input_names

    corpus = tmp_path / "corpus"
    (corpus / "datasets").mkdir(parents=True)
    (corpus / "docs" / "source").mkdir(parents=True)
    (corpus / "manifest.json").write_text('{"timezone":"UTC"}', encoding="utf-8")
    (corpus / "datasets" / "hvac_bakeoff.duckdb").write_bytes(b"database-v1")
    knowledge = corpus / "docs" / "source" / "knowledge.md"
    knowledge.write_text("knowledge-v1", encoding="utf-8")

    first = _corpus_sha256(corpus)
    knowledge.write_text("knowledge-v2", encoding="utf-8")
    second = _corpus_sha256(corpus)
    (corpus / "datasets" / "hvac_bakeoff.duckdb").write_bytes(b"database-v2")
    third = _corpus_sha256(corpus)

    assert first != second != third


def test_candidate_runner_checkpoints_and_resumes_completed_cases(
    tmp_path: Path,
) -> None:
    output = tmp_path / "result.json"
    calls: list[str] = []

    def first_run(question: str) -> dict[str, object]:
        calls.append(question)
        if question == "为什么改，效果如何？":
            raise RuntimeError("temporary upstream failure")
        return {
            "answer_markdown": "grounded",
            "grounding_status": "grounded",
            "refused": False,
            "clarification": question == "哪个更节能？",
            "citations": [
                {
                    "filename": "change-register.md",
                    "location": "decisions/change-register.md",
                    "excerpt": "current",
                }
            ],
            "activities": [{"tool": "search_project_knowledge", "status": "completed"}],
            "tables": [],
            "charts": [],
        }

    first = run_candidate_benchmark(
        benchmark_path=_manifest(tmp_path),
        output_path=output,
        candidate_id="haystack-duckdb",
        endpoint="http://candidate.invalid/api/direction/query",
        ask=first_run,
    )

    assert output.is_file()
    assert first["summary"]["execution_failure_count"] == 1
    assert [item["status"] for item in first["cases"]] == [
        "completed",
        "error",
        "completed",
    ]

    resumed_calls: list[str] = []

    def resumed(question: str) -> dict[str, object]:
        resumed_calls.append(question)
        return {
            "answer_markdown": "grounded after retry",
            "grounding_status": "grounded",
            "refused": False,
            "clarification": False,
            "citations": [
                {
                    "filename": "controls-review.md",
                    "location": "meetings/controls-review.md",
                    "excerpt": "approved",
                },
                {
                    "filename": "telemetry.csv",
                    "location": "datasets/telemetry.csv",
                    "excerpt": "computed",
                },
            ],
            "activities": [
                {"tool": "search_project_knowledge", "status": "completed"},
                {"tool": "query_hvac_database", "status": "completed"},
            ],
            "tables": [],
            "charts": [],
        }

    second = run_candidate_benchmark(
        benchmark_path=_manifest(tmp_path),
        output_path=output,
        candidate_id="haystack-duckdb",
        endpoint="http://candidate.invalid/api/direction/query",
        ask=resumed,
        resume=True,
    )

    assert resumed_calls == ["为什么改，效果如何？"]
    assert second["summary"]["execution_failure_count"] == 0
    assert all(item["status"] == "completed" for item in second["cases"])


def test_candidate_runner_aborts_after_two_model_provider_failures(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def denied(question: str) -> dict[str, object]:
        calls.append(question)
        return {
            "answer_markdown": "暂时无法安全完成",
            "grounding_status": "failed",
            "refused": True,
            "clarification": False,
            "citations": [],
            "activities": [
                {
                    "tool": "agent",
                    "status": "failed",
                    "summary": (
                        "Model workflow failed closed: PipelineRuntimeError -> "
                        "PermissionDeniedError"
                    ),
                }
            ],
            "tables": [],
            "charts": [],
        }

    report = run_candidate_benchmark(
        benchmark_path=_manifest(tmp_path),
        output_path=tmp_path / "provider-failure.json",
        candidate_id="haystack-duckdb",
        endpoint="http://candidate.invalid/api/direction/query",
        ask=denied,
    )

    assert calls == ["当前配置？", "为什么改，效果如何？"]
    assert [case["status"] for case in report["cases"]] == ["error", "error"]
    assert report["summary"]["execution_failure_count"] == 2
    assert report["abort_reason"] == "consecutive_model_provider_failures"
    assert "aborted_at" in report
    assert "completed_at" not in report


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("provider timed out"),
        httpx.HTTPStatusError(
            "rate limited",
            request=httpx.Request("POST", "https://provider.invalid"),
            response=httpx.Response(429),
        ),
        httpx.HTTPStatusError(
            "provider unavailable",
            request=httpx.Request("POST", "https://provider.invalid"),
            response=httpx.Response(503),
        ),
    ],
)
def test_candidate_runner_aborts_after_two_transport_provider_failures(
    tmp_path: Path,
    failure: Exception,
) -> None:
    calls: list[str] = []

    def unavailable(question: str) -> dict[str, object]:
        calls.append(question)
        raise failure

    report = run_candidate_benchmark(
        benchmark_path=_manifest(tmp_path),
        output_path=tmp_path / "transport-provider-failure.json",
        candidate_id="haystack-duckdb",
        endpoint="http://candidate.invalid/api/direction/query",
        ask=unavailable,
    )

    assert len(calls) == 2
    assert report["abort_reason"] == "consecutive_model_provider_failures"
    assert all(case["error"]["model_provider_failure"] for case in report["cases"])


def test_candidate_runner_resume_rejects_model_or_revision_change(
    tmp_path: Path,
) -> None:
    output = tmp_path / "result.json"
    responses = {
        "当前配置？": {
            "answer_markdown": "10 C",
            "grounding_status": "grounded",
            "refused": False,
            "clarification": False,
            "citations": [{"filename": "change-register.md"}],
            "activities": [{"tool": "search_project_knowledge", "status": "completed"}],
        },
        "为什么改，效果如何？": {
            "answer_markdown": "已核对。",
            "grounding_status": "grounded",
            "refused": False,
            "clarification": False,
            "citations": [
                {"filename": "controls-review.md"},
                {"filename": "telemetry.csv"},
            ],
            "activities": [
                {"tool": "search_project_knowledge", "status": "completed"},
                {"tool": "query_hvac_database", "status": "completed"},
            ],
        },
        "哪个更节能？": {
            "answer_markdown": "请补充口径。",
            "grounding_status": "clarification",
            "refused": False,
            "clarification": True,
            "citations": [],
            "activities": [{"tool": "ask_for_clarification", "status": "completed"}],
        },
    }
    run_candidate_benchmark(
        benchmark_path=_manifest(tmp_path),
        output_path=output,
        candidate_id="candidate",
        endpoint="http://candidate.invalid/query",
        ask=lambda question: responses[question],
        model_label="model-a",
        candidate_revision="revision-a",
    )

    with pytest.raises(EvaluationContractError, match="model_label"):
        run_candidate_benchmark(
            benchmark_path=_manifest(tmp_path),
            output_path=output,
            candidate_id="candidate",
            endpoint="http://candidate.invalid/query",
            ask=lambda question: responses[question],
            model_label="model-b",
            candidate_revision="revision-a",
            resume=True,
        )

    with pytest.raises(EvaluationContractError, match="candidate_revision"):
        run_candidate_benchmark(
            benchmark_path=_manifest(tmp_path),
            output_path=output,
            candidate_id="candidate",
            endpoint="http://candidate.invalid/query",
            ask=lambda question: responses[question],
            model_label="model-a",
            candidate_revision="revision-b",
            resume=True,
        )


def test_candidate_runner_rescores_retained_responses_on_resume(tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    response = {
        "answer_markdown": "10 C",
        "grounding_status": "grounded",
        "refused": False,
        "clarification": False,
        "citations": [{"filename": "change-register.md"}],
        "activities": [{"tool": "search_project_knowledge", "status": "completed"}],
    }
    run_candidate_benchmark(
        benchmark_path=_manifest(tmp_path),
        output_path=output,
        candidate_id="candidate",
        endpoint="http://candidate.invalid/query",
        ask=lambda _: response,
        selected_case_ids={"K01"},
        model_label="model-a",
        candidate_revision="revision-a",
    )
    tampered = json.loads(output.read_text(encoding="utf-8"))
    tampered["cases"][0]["checks"]["behavior_pass"] = False
    output.write_text(json.dumps(tampered), encoding="utf-8")

    resumed = run_candidate_benchmark(
        benchmark_path=_manifest(tmp_path),
        output_path=output,
        candidate_id="candidate",
        endpoint="http://candidate.invalid/query",
        ask=lambda _: (_ for _ in ()).throw(AssertionError("must not call")),
        selected_case_ids={"K01"},
        model_label="model-a",
        candidate_revision="revision-a",
        resume=True,
    )

    assert resumed["cases"][0]["checks"]["behavior_pass"] is True


def test_candidate_runner_rejects_subset_resume_that_would_truncate_result(
    tmp_path: Path,
) -> None:
    output = tmp_path / "result.json"
    response = {
        "answer_markdown": "grounded",
        "grounding_status": "grounded",
        "refused": False,
        "clarification": False,
        "citations": [{"filename": "change-register.md"}],
        "activities": [{"tool": "search_project_knowledge", "status": "completed"}],
    }
    run_candidate_benchmark(
        benchmark_path=_manifest(tmp_path),
        output_path=output,
        candidate_id="candidate",
        endpoint="http://candidate.invalid/query",
        ask=lambda _: response,
        model_label="model-a",
        candidate_revision="revision-a",
    )

    with pytest.raises(EvaluationContractError, match="selected case set"):
        run_candidate_benchmark(
            benchmark_path=_manifest(tmp_path),
            output_path=output,
            candidate_id="candidate",
            endpoint="http://candidate.invalid/query",
            ask=lambda _: response,
            selected_case_ids={"K01"},
            model_label="model-a",
            candidate_revision="revision-a",
            resume=True,
        )

    assert len(json.loads(output.read_text(encoding="utf-8"))["cases"]) == 3
