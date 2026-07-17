from __future__ import annotations

import json
from pathlib import Path

from evaluation.run_four_version_complex_benchmark import (
    benchmark_exit_code,
    run_complex_benchmark,
    score_complex_case,
)


def _response(
    answer: str,
    *,
    clarification: bool = False,
    tools: tuple[str, ...] = ("query_hvac_database",),
) -> dict[str, object]:
    return {
        "answer_markdown": answer,
        "grounding_status": "clarification" if clarification else "grounded",
        "refused": False,
        "clarification": clarification,
        "citations": [{"filename": "telemetry.csv"}],
        "activities": [{"tool": tool, "status": "completed"} for tool in tools],
        "tables": [],
        "charts": [],
    }


def test_explicit_history_rejects_clarification_and_raw_path() -> None:
    case = {
        "case_id": "MX09",
        "user_turns": ["分析 2026-01-16 00:00 到 23:59:50 HP-02。"],
        "required_sources": ["telemetry_clean"],
        "expected_tools": ["query_hvac_database"],
        "clarification_policy": (
            "explicit historical range; current-date clarification forbidden"
        ),
        "forbidden_raw_path_patterns": ["docs/", "runtime/"],
    }
    checks = score_complex_case(
        case,
        [
            _response(
                "请确认今天的日期。详见 docs/research/internal.md。",
                clarification=True,
                tools=("ask_for_clarification",),
            )
        ],
    )

    assert checks["clarification_policy_pass"] is False
    assert checks["raw_path_leak_count"] == 1
    assert checks["hard_gate_pass"] is False


def test_grounded_history_answer_passes_core_hard_gates() -> None:
    case = {
        "case_id": "MX09",
        "user_turns": ["分析 2026-01-16 00:00 到 23:59:50 HP-02。"],
        "required_sources": ["telemetry_clean"],
        "expected_tools": ["query_hvac_database"],
        "expected_facts": ["HP-02", "送风温度"],
        "clarification_policy": (
            "explicit historical range; current-date clarification forbidden"
        ),
        "forbidden_raw_path_patterns": ["docs/", "runtime/"],
    }
    checks = score_complex_case(
        case,
        [_response("HP-02 的送风温度已按指定历史窗口完成分析。")],
    )

    assert checks["clarification_policy_pass"] is True
    assert checks["raw_path_leak_count"] == 0
    assert checks["expected_tool_recall"] == 1.0
    assert checks["required_source_recall"] == 1.0
    assert checks["expected_fact_recall"] == 1.0
    assert checks["hard_gate_pass"] is True


def test_missing_expected_fact_and_default_internal_path_fail_hard_gate() -> None:
    case = {
        "case_id": "MX07",
        "user_turns": ["旧配置和当前配置分别何时有效？"],
        "required_sources": ["telemetry_clean"],
        "expected_tools": ["query_hvac_database"],
        "expected_facts": ["当前 10 C 自 2026-01-16 12:00 生效"],
        "clarification_policy": "no clarification",
    }
    checks = score_complex_case(
        case,
        [_response("已核对配置，详情见 datasets/telemetry.csv。")],
    )

    assert checks["expected_fact_recall"] == 0.0
    assert checks["raw_path_leak_count"] == 1
    assert checks["hard_gate_pass"] is False


def test_quality_failures_return_nonzero_even_when_execution_completed() -> None:
    report = {
        "summary": {
            "case_count": 2,
            "completed_count": 2,
            "execution_failure_count": 0,
            "hard_gate_pass_count": 1,
        }
    }

    assert benchmark_exit_code(report) == 1


def test_table_schema_accepts_humanized_columns_with_units() -> None:
    case = {
        "case_id": "MX02",
        "user_turns": ["比较变更前后。"],
        "required_sources": ["telemetry_clean"],
        "expected_tools": ["query_hvac_database"],
        "expected_table_schema": ["比较窗口", "送风温度", "电耗"],
        "clarification_policy": "no clarification",
    }
    response = _response("已完成比较。")
    response["tables"] = [
        {
            "columns": ["比较窗口", "平均送风温度（°C）", "电耗（kWh）"],
            "rows": [["变更前", 12.2, 36.0], ["变更后", 10.3, 40.0]],
        }
    ]

    checks = score_complex_case(case, [response])

    assert checks["table_schema_pass"] is True
    assert checks["presentation_pass"] is True


def test_typed_snapshot_satisfies_generic_database_tool_contract() -> None:
    case = {
        "case_id": "MX06",
        "user_turns": ["核对一次除霜事件。"],
        "required_sources": ["telemetry_clean"],
        "expected_tools": ["query_hvac_database"],
        "clarification_policy": "no clarification",
    }
    response = _response(
        "已用只读事件检查完成核对。",
        tools=("inspect_hvac_snapshot",),
    )

    checks = score_complex_case(case, [response])

    assert checks["expected_tool_recall"] == 1.0
    assert checks["missing_tools"] == []


def test_runner_preserves_multi_turn_history(tmp_path: Path) -> None:
    benchmark = tmp_path / "benchmark.json"
    benchmark.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "benchmark_id": "multi-turn-test",
                "fully_synthetic": True,
                "shared_backend": True,
                "architectures": [
                    "baseline",
                    "conversation",
                    "evidence",
                    "canvas",
                ],
                "hard_gates": {},
                "cases": [
                    {
                        "case_id": "MX10",
                        "category": "multi_turn_inheritance",
                        "user_turns": [
                            "分析 HP-01 在 2026-01-15 的平均功率。",
                            "换成 HP-02，时间和指标保持不变。",
                            "再只看 08:00 到 18:00。",
                        ],
                        "required_sources": ["telemetry_clean"],
                        "expected_tools": ["query_hvac_database"],
                        "clarification_policy": (
                            "inherit unchanged context; no repeated questions"
                        ),
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    observed: list[tuple[str, list[dict[str, str]]]] = []

    def ask(question: str, history: list[dict[str, str]]) -> dict[str, object]:
        observed.append((question, history))
        return _response(f"已回答：{question}")

    report = run_complex_benchmark(
        benchmark_path=benchmark,
        output_path=tmp_path / "result.json",
        endpoint="http://candidate.invalid/api/direction/query",
        ask=ask,
    )

    assert len(observed) == 3
    assert observed[0][1] == []
    assert observed[1][1] == [
        {"role": "user", "content": "分析 HP-01 在 2026-01-15 的平均功率。"},
        {
            "role": "assistant",
            "content": "已回答：分析 HP-01 在 2026-01-15 的平均功率。",
        },
    ]
    assert observed[2][1][-2:] == [
        {"role": "user", "content": "换成 HP-02，时间和指标保持不变。"},
        {"role": "assistant", "content": "已回答：换成 HP-02，时间和指标保持不变。"},
    ]
    assert report["summary"]["execution_failure_count"] == 0
    assert report["cases"][0]["turns"][2]["request_history_count"] == 4


def test_runner_resume_reuses_completed_case_without_model_call(tmp_path: Path) -> None:
    benchmark = tmp_path / "benchmark.json"
    benchmark.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "benchmark_id": "resume-test",
                "fully_synthetic": True,
                "shared_backend": True,
                "architectures": [
                    "baseline",
                    "conversation",
                    "evidence",
                    "canvas",
                ],
                "hard_gates": {},
                "cases": [
                    {
                        "case_id": "MX01",
                        "category": "data",
                        "user_turns": ["分析历史数据。"],
                        "required_sources": ["telemetry_clean"],
                        "expected_tools": ["query_hvac_database"],
                        "clarification_policy": "no clarification",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "result.json"
    run_complex_benchmark(
        benchmark_path=benchmark,
        output_path=output,
        endpoint="http://candidate.invalid/api/direction/query",
        ask=lambda question, history: _response("已完成历史分析。"),
    )

    report = run_complex_benchmark(
        benchmark_path=benchmark,
        output_path=output,
        endpoint="http://candidate.invalid/api/direction/query",
        ask=lambda question, history: (_ for _ in ()).throw(
            AssertionError("resume must not call the model")
        ),
        resume=True,
    )

    assert report["summary"]["completed_count"] == 1
    assert report["cases"][0]["status"] == "completed"
