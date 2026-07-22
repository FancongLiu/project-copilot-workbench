from __future__ import annotations

import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = (
    REPOSITORY_ROOT / "evaluation" / "opencode_codex_comparison_cases_20260721.json"
)
RESULTS_ROOT = REPOSITORY_ROOT / "evaluation" / "results"


def test_opencode_codex_comparison_contract_freezes_two_complex_cases() -> None:
    contract = json.loads(CASES_PATH.read_text(encoding="utf-8"))

    assert contract["fully_synthetic"] is True
    assert contract["model_policy"]["same_approved_model"] is True
    assert contract["model_policy"]["reasoning_effort"] == "xhigh"
    assert contract["model_policy"]["prompt_builder"] == "CodexRuntime._prompt"
    assert len(contract["cases"]) == 2
    assert {case["case_id"] for case in contract["cases"]} == {"CC01", "CC02"}
    for case in contract["cases"]:
        assert len(case["question"]) >= 80
        assert case["required_facts"]
        assert case["required_sources"]
        assert case["expected_tools"]
        assert case["safe_behavior"]
    assert set(contract["comparison_dimensions"]) >= {
        "wall_clock_latency_ms",
        "tool_trace_and_count",
        "grounding_status",
        "citation_count_and_filename_coverage",
        "refusal_or_safe_partial_answer",
        "required_fact_correctness",
        "execution_error",
    }


def test_comparison_evidence_records_semantic_correction_and_codex_blocker() -> None:
    cc01_open = json.loads(
        (
            RESULTS_ROOT
            / "opencode-codex-comparison-20260721-cc01-opencode-fixed-xhigh.json"
        ).read_text(encoding="utf-8")
    )
    cc02_open = json.loads(
        (
            RESULTS_ROOT
            / "opencode-codex-comparison-20260721-cc02-opencode-fixed-xhigh-replay.json"
        ).read_text(encoding="utf-8")
    )
    assert cc02_open["semantic_adjudication"]["status"] == (
        "pass_with_contract_correction"
    )
    assert all(cc02_open["semantic_adjudication"]["supported_fact_hits"].values())

    for case_id, open_result in (("cc01", cc01_open), ("cc02", cc02_open)):
        blocked = json.loads(
            (
                RESULTS_ROOT
                / f"opencode-codex-comparison-20260721-{case_id}-codex-blocked.json"
            ).read_text(encoding="utf-8")
        )
        assert blocked["status"] == "blocked_by_isolation"
        assert blocked["model_call_started"] is False
        assert blocked["comparison_eligible"] is False
        assert blocked["model_id_sha256"] == open_result["model_id_sha256"]
        assert blocked["execution_error"]["reason_code"] == (
            "native_windows_isolation_preflight_failed"
        )
