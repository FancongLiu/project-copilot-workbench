from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation import run_hvac_role_benchmark as role_runner
from evaluation.run_hvac_role_benchmark import (
    load_role_benchmark,
    run_role_benchmark,
)
from evaluation.run_offline import EvaluationContractError, _load_imports


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = REPOSITORY_ROOT / "examples" / "synthetic_hvac"
BENCHMARK_PATH = REPOSITORY_ROOT / "evaluation" / "hvac_role_benchmark.json"
EXPECTED_ROLES = {
    "design_engineer",
    "commissioning_engineer",
    "field_service_engineer",
    "project_delivery_engineer",
}
REQUIRED_WORKFLOWS = {
    "project_knowledge",
    "configuration_lookup",
    "meeting_decision",
    "governed_analytics",
    "temporal_diagnostics",
    "clarification",
    "refusal",
    "role_isolation",
}


def test_role_manifest_covers_broad_commercial_hvac_workflows() -> None:
    benchmark = load_role_benchmark(BENCHMARK_PATH, repository_root=REPOSITORY_ROOT)

    assert benchmark.fully_synthetic is True
    assert benchmark.license == "CC0-1.0"
    assert {role.role_id for role in benchmark.roles} == EXPECTED_ROLES
    assert set().union(*(set(role.workflows) for role in benchmark.roles)) >= (
        REQUIRED_WORKFLOWS
    )
    assert len({role.data_area for role in benchmark.roles}) == len(benchmark.roles)
    assert all(role.data_area.is_dir() for role in benchmark.roles)
    assert all(role.gold_path.is_file() for role in benchmark.roles)


def test_role_manifest_rejects_gold_data_outside_its_role_area(
    tmp_path: Path,
) -> None:
    shared_gold = tmp_path / "shared-gold.json"
    shared_gold.write_text("{}", encoding="utf-8")
    roles = []
    for role_id in ("role_alpha", "role_beta"):
        area = tmp_path / role_id
        area.mkdir()
        roles.append(
            {
                "role_id": role_id,
                "display_name": role_id,
                "workflows": ["project_knowledge"],
                "data_area": role_id,
                "gold_path": "shared-gold.json",
            }
        )
    manifest = tmp_path / "benchmark.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "benchmark_id": "isolation-test",
                "license": "CC0-1.0",
                "fully_synthetic": True,
                "roles": roles,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(EvaluationContractError, match="inside its data area"):
        load_role_benchmark(manifest, repository_root=tmp_path)


def test_role_manifest_rejects_nested_data_areas(tmp_path: Path) -> None:
    roles = []
    for role_id, relative in (
        ("role_outer", Path("outer")),
        ("role_inner", Path("outer") / "inner"),
    ):
        area = tmp_path / relative
        area.mkdir(parents=True)
        gold = area / "gold_cases.json"
        gold.write_text("{}", encoding="utf-8")
        roles.append(
            {
                "role_id": role_id,
                "display_name": role_id,
                "workflows": ["project_knowledge"],
                "data_area": relative.as_posix(),
                "gold_path": gold.relative_to(tmp_path).as_posix(),
            }
        )
    manifest = tmp_path / "benchmark.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "benchmark_id": "nested-isolation-test",
                "license": "CC0-1.0",
                "fully_synthetic": True,
                "roles": roles,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(EvaluationContractError, match="overlap"):
        load_role_benchmark(manifest, repository_root=tmp_path)


def test_role_import_requires_docs_source_and_never_falls_back_to_gold(
    tmp_path: Path,
) -> None:
    role_area = tmp_path / "role"
    role_area.mkdir()
    (role_area / "gold_cases.json").write_text("{}", encoding="utf-8")

    with pytest.raises(EvaluationContractError, match="Missing document corpus"):
        _load_imports(CORPUS_ROOT, role_area)


def test_role_manifest_requires_local_synthetic_provenance(tmp_path: Path) -> None:
    roles = []
    for role_id in ("role_alpha", "role_beta"):
        area = tmp_path / role_id
        (area / "docs" / "source").mkdir(parents=True)
        gold = area / "gold_cases.json"
        gold.write_text("{}", encoding="utf-8")
        roles.append(
            {
                "role_id": role_id,
                "display_name": role_id,
                "workflows": ["project_knowledge"],
                "data_area": role_id,
                "gold_path": gold.relative_to(tmp_path).as_posix(),
            }
        )
    manifest = tmp_path / "benchmark.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "benchmark_id": "provenance-test",
                "license": "CC0-1.0",
                "fully_synthetic": True,
                "roles": roles,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(EvaluationContractError, match="provenance"):
        load_role_benchmark(manifest, repository_root=tmp_path)


def test_role_benchmark_uses_isolated_data_and_runtime_areas(tmp_path: Path) -> None:
    output = tmp_path / "hvac-role-benchmark.json"
    runtime = tmp_path / "runtime"

    report = run_role_benchmark(
        corpus_root=CORPUS_ROOT,
        benchmark_path=BENCHMARK_PATH,
        output_path=output,
        runtime_root=runtime,
    )

    assert json.loads(output.read_text(encoding="utf-8")) == report
    assert {item["role_id"] for item in report["roles"]} == EXPECTED_ROLES
    assert len({item["data_area_sha256"] for item in report["roles"]}) == 4
    assert all(item["source_inventory"] for item in report["roles"])
    assert all(item["role_source_count"] >= 1 for item in report["roles"])
    assert report["adapter"] == "project_copilot.agent.DeterministicChatGenerator"
    assert report["environment"]["python"]
    assert report["environment"]["platform"]
    assert report["corpus"]["sha256"]
    assert report["corpus"]["fully_synthetic"] is True

    for role in report["roles"]:
        isolation_cases = [
            item for item in role["cases"] if item["category"] == "role_isolation"
        ]
        assert len(isolation_cases) == 1
        assert isolation_cases[0]["actual"]["refused"] is True
        assert isolation_cases[0]["actual"]["citations"] == []
        assert isolation_cases[0]["status"] == "passed"

    registries = sorted(runtime.glob("*/workspace-registry.json"))
    assert len(registries) == 4
    assert len({path.parent.name for path in registries}) == 4
    assert all(
        len(json.loads(path.read_text(encoding="utf-8"))["workspaces"]) == 1
        for path in registries
    )


def test_role_benchmark_aggregates_only_measured_case_evidence(tmp_path: Path) -> None:
    report = run_role_benchmark(
        corpus_root=CORPUS_ROOT,
        benchmark_path=BENCHMARK_PATH,
        output_path=tmp_path / "measured.json",
        runtime_root=tmp_path / "runtime",
    )

    case_count = sum(role["summary"]["case_count"] for role in report["roles"])
    passed_count = sum(
        role["summary"]["passed_all_applicable_metrics_count"]
        for role in report["roles"]
    )
    assert report["summary"]["role_count"] == 4
    assert report["summary"]["case_count"] == case_count
    assert report["summary"]["passed_all_applicable_metrics_count"] == passed_count
    assert report["summary"]["failed_execution_count"] == 0
    assert set(report["summary"]["workflow_coverage"]) >= REQUIRED_WORKFLOWS

    for metric, aggregate in report["summary"]["metrics"].items():
        assert aggregate["measured"] >= aggregate["passed"] >= 0, metric
        expected = (
            aggregate["passed"] / aggregate["measured"]
            if aggregate["measured"]
            else None
        )
        assert aggregate["rate"] == expected


def test_role_benchmark_cli_fails_when_a_case_metric_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        role_runner,
        "run_role_benchmark",
        lambda **_: {
            "summary": {
                "failed_execution_count": 0,
                "passed_all_applicable_metrics_count": 1,
                "case_count": 2,
            }
        },
    )

    assert role_runner.main(["--output", str(tmp_path / "result.json")]) == 2
