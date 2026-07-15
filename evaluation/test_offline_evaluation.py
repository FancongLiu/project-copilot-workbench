from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from evaluation.run_offline import load_gold_cases, run_evaluation


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = REPOSITORY_ROOT / "examples" / "synthetic_hvac"
GOLD_PATH = REPOSITORY_ROOT / "evaluation" / "gold_cases.json"
REQUIRED_CATEGORIES = {
    "exact_lookup",
    "cross_document_synthesis",
    "temporal",
    "configuration_conflict",
    "knowledge_and_data",
    "clarification",
    "refusal",
    "hostile_input",
    "tool_selection",
}


def test_gold_set_covers_required_acceptance_categories() -> None:
    cases = load_gold_cases(GOLD_PATH)

    assert len(cases) >= 15
    assert {case.category for case in cases} >= REQUIRED_CATEGORIES
    assert len({case.case_id for case in cases}) == len(cases)
    assert all(case.question.strip() for case in cases)
    assert all(case.expected_tools is not None for case in cases)


def test_offline_run_records_auditable_per_case_evidence(tmp_path: Path) -> None:
    output_path = tmp_path / "measured.json"

    report = run_evaluation(
        corpus_root=CORPUS_ROOT,
        gold_path=GOLD_PATH,
        output_path=output_path,
        runtime_root=tmp_path / "runtime",
    )

    persisted = json.loads(output_path.read_text(encoding="utf-8"))
    assert persisted == report
    assert report["summary"]["case_count"] == len(report["cases"])
    assert report["summary"]["completed_count"] == len(report["cases"])
    assert report["summary"]["failed_execution_count"] == 0
    assert report["summary"]["passed_all_applicable_metrics_count"] == len(
        report["cases"]
    )
    assert report["source_inventory"]
    assert report["corpus"]["license"] == "CC0-1.0"
    assert report["corpus"]["fully_synthetic"] is True
    assert datetime.fromisoformat(report["completed_at"]) >= datetime.fromisoformat(
        report["started_at"]
    )

    for result in report["cases"]:
        assert result["latency_ms"] >= 0
        assert isinstance(result["actual"]["answer"], str)
        assert isinstance(result["actual"]["tools"], list)
        assert isinstance(result["actual"]["citations"], list)
        assert set(result["scores"]) == {
            "retrieval",
            "citation_grounding",
            "answer_correctness",
            "tool_selection",
            "refusal",
            "clarification",
        }


def test_offline_run_measures_safety_and_knowledge_data_paths(tmp_path: Path) -> None:
    report = run_evaluation(
        corpus_root=CORPUS_ROOT,
        gold_path=GOLD_PATH,
        output_path=tmp_path / "measured.json",
        runtime_root=tmp_path / "runtime",
    )
    by_id = {item["case_id"]: item for item in report["cases"]}

    combined = by_id["knowledge-data-peak-decision"]
    assert combined["actual"]["tools"] == [
        "meeting_decision_lookup",
        "governed_analytics",
    ]
    assert combined["actual"]["citations"]
    assert combined["scores"]["tool_selection"] is True

    for case_id in (
        "refusal-no-evidence",
        "hostile-shell-control",
        "hostile-web-bypass",
    ):
        assert by_id[case_id]["actual"]["refused"] is True
        assert by_id[case_id]["scores"]["refusal"] is True


def test_summary_rates_are_derived_from_explicit_counts(tmp_path: Path) -> None:
    report = run_evaluation(
        corpus_root=CORPUS_ROOT,
        gold_path=GOLD_PATH,
        output_path=tmp_path / "measured.json",
        runtime_root=tmp_path / "runtime",
    )

    for metric, aggregate in report["summary"]["metrics"].items():
        assert aggregate["measured"] >= aggregate["passed"] >= 0, metric
        expected_rate = (
            aggregate["passed"] / aggregate["measured"]
            if aggregate["measured"]
            else None
        )
        assert aggregate["rate"] == expected_rate


def test_summary_uses_haystack_ranking_evaluators(tmp_path: Path) -> None:
    report = run_evaluation(
        corpus_root=CORPUS_ROOT,
        gold_path=GOLD_PATH,
        output_path=tmp_path / "measured.json",
        runtime_root=tmp_path / "runtime",
    )

    ranking = report["summary"]["retrieval_ranking"]
    assert ranking["evaluator"] == "haystack"
    assert ranking["evaluated_cases"] == sum(
        bool(case.expected_sources) for case in load_gold_cases(GOLD_PATH)
    )
    assert set(ranking) == {
        "evaluator",
        "document_comparison_field",
        "evaluated_cases",
        "recall",
        "mrr",
        "ndcg",
    }
    assert all(0.0 <= ranking[key] <= 1.0 for key in ("recall", "mrr", "ndcg"))
