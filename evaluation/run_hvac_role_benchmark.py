from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.run_offline import (
    EvaluationContractError,
    _aggregate_metrics,
    _atomic_json_write,
    _latency_summary,
    _retrieval_ranking_summary,
    run_evaluation,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_ROLE_ID = re.compile(r"^[a-z][a-z0-9_]{2,47}$")


@dataclass(frozen=True)
class HvacRole:
    role_id: str
    display_name: str
    workflows: tuple[str, ...]
    data_area: Path
    gold_path: Path


@dataclass(frozen=True)
class HvacRoleBenchmark:
    benchmark_id: str
    license: str
    fully_synthetic: bool
    roles: tuple[HvacRole, ...]


def _resolve_repository_path(repository_root: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationContractError(f"Role field {field!r} must be a path string")
    candidate = (repository_root / value).resolve()
    if not candidate.is_relative_to(repository_root):
        raise EvaluationContractError(f"Role field {field!r} escapes the repository")
    return candidate


def load_role_benchmark(
    path: str | Path, *, repository_root: str | Path = REPOSITORY_ROOT
) -> HvacRoleBenchmark:
    benchmark_path = Path(path).resolve()
    root = Path(repository_root).resolve()
    try:
        payload = json.loads(benchmark_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationContractError(
            f"Cannot read HVAC role benchmark: {benchmark_path}"
        ) from exc
    if payload.get("schema_version") != "1.0":
        raise EvaluationContractError("HVAC role benchmark schema_version must be 1.0")
    if (
        payload.get("license") != "CC0-1.0"
        or payload.get("fully_synthetic") is not True
    ):
        raise EvaluationContractError(
            "HVAC role benchmark must declare CC0-1.0 fully synthetic data"
        )
    benchmark_id = payload.get("benchmark_id")
    raw_roles = payload.get("roles")
    if not isinstance(benchmark_id, str) or not benchmark_id.strip():
        raise EvaluationContractError("HVAC role benchmark_id is required")
    if not isinstance(raw_roles, list) or len(raw_roles) < 2:
        raise EvaluationContractError("HVAC role benchmark requires multiple roles")

    roles: list[HvacRole] = []
    seen: set[str] = set()
    data_areas: set[Path] = set()
    for raw in raw_roles:
        if not isinstance(raw, dict):
            raise EvaluationContractError("Every HVAC role must be an object")
        role_id = raw.get("role_id")
        display_name = raw.get("display_name")
        workflows = raw.get("workflows")
        if not isinstance(role_id, str) or not _ROLE_ID.fullmatch(role_id):
            raise EvaluationContractError(f"Invalid HVAC role_id: {role_id!r}")
        if role_id in seen:
            raise EvaluationContractError(f"Duplicate HVAC role_id: {role_id}")
        if not isinstance(display_name, str) or not display_name.strip():
            raise EvaluationContractError(f"Role {role_id} needs a display_name")
        if (
            not isinstance(workflows, list)
            or not workflows
            or not all(isinstance(item, str) and item.strip() for item in workflows)
        ):
            raise EvaluationContractError(f"Role {role_id} needs workflow labels")
        data_area = _resolve_repository_path(root, raw.get("data_area"), "data_area")
        gold_path = _resolve_repository_path(root, raw.get("gold_path"), "gold_path")
        if any(
            data_area.is_relative_to(existing) or existing.is_relative_to(data_area)
            for existing in data_areas
        ):
            raise EvaluationContractError(
                "HVAC role data areas cannot overlap or contain each other"
            )
        if not data_area.is_dir() or not gold_path.is_file():
            raise EvaluationContractError(
                f"Role {role_id} data area or gold set is missing"
            )
        if not gold_path.is_relative_to(data_area):
            raise EvaluationContractError(
                f"Role {role_id} gold set must be inside its data area"
            )
        seen.add(role_id)
        data_areas.add(data_area)
        roles.append(
            HvacRole(
                role_id=role_id,
                display_name=display_name.strip(),
                workflows=tuple(dict.fromkeys(workflows)),
                data_area=data_area,
                gold_path=gold_path,
            )
        )
    for role in roles:
        provenance_path = role.data_area / "SYNTHETIC_DATA_PROVENANCE.md"
        if (
            not provenance_path.is_file()
            or provenance_path.is_symlink()
            or provenance_path.is_junction()
        ):
            raise EvaluationContractError(
                f"Role {role.role_id} needs a local synthetic provenance file"
            )
        provenance = provenance_path.read_text(encoding="utf-8").casefold()
        for marker in ("fully synthetic", "cc0-1.0", "not an engineering design"):
            if marker not in provenance:
                raise EvaluationContractError(
                    f"Role {role.role_id} provenance is missing marker: {marker}"
                )
    return HvacRoleBenchmark(
        benchmark_id=benchmark_id,
        license=payload["license"],
        fully_synthetic=True,
        roles=tuple(roles),
    )


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    resolved_root = root.resolve()
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or path.is_junction():
            raise EvaluationContractError(
                f"HVAC role data area cannot contain links: {path}"
            )
        if not path.resolve().is_relative_to(resolved_root):
            raise EvaluationContractError(
                f"HVAC role data area path escapes its root: {path}"
            )
        if not path.is_file():
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _role_source_names(role: HvacRole) -> set[str]:
    documents = role.data_area / "docs" / "source"
    return {
        path.name
        for path in documents.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".md", ".txt", ".json"}
    }


def run_role_benchmark(
    *,
    corpus_root: str | Path,
    benchmark_path: str | Path,
    output_path: str | Path,
    runtime_root: str | Path | None = None,
) -> dict[str, Any]:
    started = datetime.now(UTC)
    benchmark = load_role_benchmark(benchmark_path)
    output = Path(output_path).resolve()

    temporary_runtime: tempfile.TemporaryDirectory[str] | None = None
    if runtime_root is None:
        temporary_runtime = tempfile.TemporaryDirectory(
            prefix="project-copilot-hvac-roles-"
        )
        selected_runtime = Path(temporary_runtime.name)
    else:
        selected_runtime = Path(runtime_root).resolve()
    selected_runtime.mkdir(parents=True, exist_ok=True)
    temporary_reports = tempfile.TemporaryDirectory(
        prefix="project-copilot-hvac-role-reports-"
    )

    try:
        role_reports: list[dict[str, Any]] = []
        all_cases: list[dict[str, Any]] = []
        workflows: set[str] = set()
        shared_adapter: str | None = None
        shared_environment: dict[str, Any] | None = None
        shared_corpus: dict[str, Any] | None = None
        for role in benchmark.roles:
            role_output = Path(temporary_reports.name) / f"{role.role_id}.json"
            role_report = run_evaluation(
                corpus_root=corpus_root,
                gold_path=role.gold_path,
                output_path=role_output,
                runtime_root=selected_runtime / role.role_id,
                additional_documents_root=role.data_area,
                evaluation_context={
                    "benchmark_id": benchmark.benchmark_id,
                    "role_id": role.role_id,
                    "role_display_name": role.display_name,
                },
            )
            if shared_adapter is None:
                shared_adapter = role_report["adapter"]
                shared_environment = dict(role_report["environment"])
                shared_corpus = dict(role_report["corpus"])
            elif (
                role_report["adapter"] != shared_adapter
                or role_report["environment"] != shared_environment
                or role_report["corpus"] != shared_corpus
            ):
                raise EvaluationContractError(
                    "Role runs did not use one shared adapter, environment, and corpus"
                )
            role_sources = _role_source_names(role)
            all_cases.extend(role_report["cases"])
            workflows.update(role.workflows)
            role_reports.append(
                {
                    "role_id": role.role_id,
                    "display_name": role.display_name,
                    "workflows": list(role.workflows),
                    "data_area": role.data_area.relative_to(REPOSITORY_ROOT).as_posix(),
                    "data_area_sha256": _tree_digest(role.data_area),
                    "role_source_count": sum(
                        item["filename"] in role_sources
                        for item in role_report["source_inventory"]
                    ),
                    "source_inventory": role_report["source_inventory"],
                    "cases": role_report["cases"],
                    "summary": role_report["summary"],
                }
            )

        assert shared_adapter is not None
        assert shared_environment is not None
        assert shared_corpus is not None
        report = {
            "schema_version": "1.0",
            "benchmark_id": benchmark.benchmark_id,
            "started_at": started.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "offline": True,
            "adapter": shared_adapter,
            "environment": shared_environment,
            "corpus": shared_corpus,
            "license": benchmark.license,
            "fully_synthetic": benchmark.fully_synthetic,
            "isolation": (
                "Each role imports the shared synthetic project package plus its "
                "own role data into a separate WorkspaceManager runtime root."
            ),
            "roles": role_reports,
            "summary": {
                "role_count": len(role_reports),
                "case_count": len(all_cases),
                "completed_count": sum(item["status"] != "error" for item in all_cases),
                "failed_execution_count": sum(
                    item["status"] == "error" for item in all_cases
                ),
                "passed_all_applicable_metrics_count": sum(
                    item["status"] == "passed" for item in all_cases
                ),
                "workflow_coverage": sorted(workflows),
                "metrics": _aggregate_metrics(all_cases),
                "retrieval_ranking": _retrieval_ranking_summary(all_cases),
                "latency": _latency_summary([item["latency_ms"] for item in all_cases]),
            },
        }
        _atomic_json_write(output, report)
        return report
    finally:
        temporary_reports.cleanup()
        if temporary_runtime is not None:
            temporary_runtime.cleanup()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run isolated commercial-HVAC engineer role evaluations."
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=REPOSITORY_ROOT / "examples" / "synthetic_hvac",
    )
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=REPOSITORY_ROOT / "evaluation" / "hvac_role_benchmark.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "evaluation" / "results" / "hvac-role-benchmark.json",
    )
    parser.add_argument("--runtime", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_role_benchmark(
        corpus_root=args.corpus,
        benchmark_path=args.benchmark,
        output_path=args.output,
        runtime_root=args.runtime,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    summary = report["summary"]
    complete_pass = (
        summary["failed_execution_count"] == 0
        and summary["passed_all_applicable_metrics_count"] == summary["case_count"]
    )
    return 0 if complete_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
