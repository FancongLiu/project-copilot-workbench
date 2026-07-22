from __future__ import annotations

import json
from pathlib import Path


BENCHMARK = Path(__file__).with_name("four_version_complex_questions.json")


def test_four_version_complex_question_contract_is_frozen_and_complete() -> None:
    payload = json.loads(BENCHMARK.read_text(encoding="utf-8"))

    assert payload["fully_synthetic"] is True
    assert payload["shared_backend"] is True
    assert payload["architectures"] == [
        "baseline",
        "conversation",
        "evidence",
        "canvas",
    ]
    assert payload["hard_gates"]["raw_path_leak_count"] == 0
    assert payload["hard_gates"]["subquestion_recall_min"] >= 0.9

    cases = payload["cases"]
    assert len(cases) == 14
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert all(case["user_turns"] for case in cases)
    assert all(case["required_subquestions"] for case in cases)
    assert any(len(case["required_subquestions"]) >= 8 for case in cases)
    assert any(len(case["user_turns"]) >= 3 for case in cases)
    assert any(
        case.get("clarification_policy", "").startswith("explicit historical range")
        for case in cases
    )
    assert any(case.get("expected_table_schema") for case in cases)
    assert any(case.get("expected_chart_spec") for case in cases)
