from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from haystack import Document
from haystack.components.evaluators import (
    DocumentMRREvaluator,
    DocumentNDCGEvaluator,
    DocumentRecallEvaluator,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

os.environ.setdefault("HAYSTACK_TELEMETRY_ENABLED", "False")

from project_copilot.agent import DeterministicChatGenerator, ProjectAgent  # noqa: E402
from project_copilot.analytics import AnalyticsWorkspace  # noqa: E402
from project_copilot.defrost_diagnostics import (  # noqa: E402
    DefrostAssetContext,
    DefrostDiagnosticsEngine,
    DefrostRulePack,
)
from project_copilot.ingestion import ImportedFile, ProjectIndexer  # noqa: E402
from project_copilot.semantic_analytics import GovernedAnalyticsTool  # noqa: E402
from project_copilot.workspaces import WorkspaceManager  # noqa: E402


class EvaluationContractError(ValueError):
    """Raised when frozen gold data or the synthetic corpus is incomplete."""


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    category: str
    question: str
    expected_sources: tuple[str, ...]
    answer_contains_all: tuple[str, ...]
    grounding_terms: tuple[str, ...]
    expected_tools: tuple[str, ...]
    expected_refused: bool
    expected_clarification: bool


def _require_string_list(value: Any, *, field: str, case_id: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise EvaluationContractError(
            f"Case {case_id!r} field {field!r} must be a list of non-empty strings"
        )
    return tuple(value)


def load_gold_cases(path: str | Path) -> list[EvaluationCase]:
    gold_path = Path(path).resolve()
    try:
        payload = json.loads(gold_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationContractError(f"Cannot read gold set: {gold_path}") from exc
    if payload.get("schema_version") != "1.0":
        raise EvaluationContractError("Gold schema_version must be 1.0")
    if (
        payload.get("license") != "CC0-1.0"
        or payload.get("fully_synthetic") is not True
    ):
        raise EvaluationContractError(
            "Gold set must declare CC0-1.0 fully synthetic data"
        )
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise EvaluationContractError("Gold set must contain cases")

    cases: list[EvaluationCase] = []
    seen: set[str] = set()
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict) or not isinstance(
            raw_case.get("expected"), dict
        ):
            raise EvaluationContractError("Each gold case requires an expected object")
        case_id = raw_case.get("id")
        category = raw_case.get("category")
        question = raw_case.get("question")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (case_id, category, question)
        ):
            raise EvaluationContractError(
                "Gold case id, category, and question are required"
            )
        if case_id in seen:
            raise EvaluationContractError(f"Duplicate gold case ID: {case_id}")
        seen.add(case_id)
        expected = raw_case["expected"]
        refused = expected.get("refused")
        clarification = expected.get("clarification")
        if not isinstance(refused, bool) or not isinstance(clarification, bool):
            raise EvaluationContractError(
                f"Case {case_id!r} must declare boolean refusal and clarification expectations"
            )
        cases.append(
            EvaluationCase(
                case_id=case_id,
                category=category,
                question=question,
                expected_sources=_require_string_list(
                    expected.get("sources"), field="sources", case_id=case_id
                ),
                answer_contains_all=_require_string_list(
                    expected.get("answer_contains_all"),
                    field="answer_contains_all",
                    case_id=case_id,
                ),
                grounding_terms=_require_string_list(
                    expected.get("grounding_terms"),
                    field="grounding_terms",
                    case_id=case_id,
                ),
                expected_tools=_require_string_list(
                    expected.get("tools"), field="tools", case_id=case_id
                ),
                expected_refused=refused,
                expected_clarification=clarification,
            )
        )
    return cases


def _category_for_document(path: Path) -> str:
    normalized = path.as_posix().casefold()
    if "decision" in normalized:
        return "decision"
    if "meeting" in normalized:
        return "meeting"
    if "sop" in normalized or "procedure" in normalized or "safety" in normalized:
        return "SOP"
    if "config" in normalized or "control" in normalized:
        return "configuration"
    return "background"


def _load_imports(
    corpus_root: Path, additional_documents_root: Path | None = None
) -> list[ImportedFile]:
    document_roots = [corpus_root / "docs" / "source"]
    if additional_documents_root is not None:
        document_roots.append(additional_documents_root / "docs" / "source")
    missing = [str(path) for path in document_roots if not path.is_dir()]
    if missing:
        raise EvaluationContractError(f"Missing document corpus: {missing}")
    imports: list[ImportedFile] = []
    names: set[str] = set()
    for documents_root in document_roots:
        resolved_documents_root = documents_root.resolve()
        for path in sorted(documents_root.rglob("*")):
            if path.is_symlink() or path.is_junction():
                raise EvaluationContractError(
                    f"Synthetic document corpus cannot contain links: {path}"
                )
            if not path.resolve().is_relative_to(resolved_documents_root):
                raise EvaluationContractError(
                    f"Synthetic document corpus path escapes its root: {path}"
                )
            if not path.is_file() or path.suffix.casefold() not in {
                ".md",
                ".txt",
                ".json",
            }:
                continue
            if path.name in names:
                raise EvaluationContractError(
                    f"Synthetic source basenames must be unique: {path.name}"
                )
            names.add(path.name)
            imports.append(
                ImportedFile(
                    filename=path.name,
                    content=path.read_bytes(),
                    category=_category_for_document(path),
                )
            )
    if not imports:
        raise EvaluationContractError("Synthetic document corpus is empty")
    return imports


def _validate_corpus(corpus_root: Path) -> Path:
    required = (
        corpus_root / "project.yaml",
        corpus_root / "LICENSE",
        corpus_root / "SYNTHETIC_DATA_PROVENANCE.md",
        corpus_root / "datasets" / "raw" / "telemetry.csv",
        corpus_root / "datasets" / "raw" / "defrost_telemetry.csv",
        corpus_root / "docs" / "source" / "configuration" / "defrost-rules.json",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise EvaluationContractError(f"Synthetic corpus is incomplete: {missing}")
    provenance = required[2].read_text(encoding="utf-8").casefold()
    for marker in ("fully synthetic", "cc0-1.0", "not an engineering design"):
        if marker not in provenance:
            raise EvaluationContractError(
                f"Synthetic provenance is missing required marker: {marker}"
            )
    return required[3]


def _corpus_digest(corpus_root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in corpus_root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(corpus_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _normalized(text: str) -> str:
    return " ".join(text.casefold().split())


def _contains_all(text: str, terms: tuple[str, ...]) -> bool:
    normalized = _normalized(text)
    return all(_normalized(term) in normalized for term in terms)


def _score_case(case: EvaluationCase, actual: dict[str, Any]) -> dict[str, bool | None]:
    citation_sources = {item["source"] for item in actual["citations"]}
    citation_text = " ".join(item["excerpt"] for item in actual["citations"])
    return {
        "retrieval": (
            all(source in citation_sources for source in case.expected_sources)
            if case.expected_sources
            else None
        ),
        "citation_grounding": (
            _contains_all(citation_text, case.grounding_terms)
            if case.grounding_terms
            else None
        ),
        "answer_correctness": _contains_all(actual["answer"], case.answer_contains_all),
        "tool_selection": actual["tools"] == list(case.expected_tools),
        "refusal": actual["refused"] == case.expected_refused,
        "clarification": actual["clarification"] == case.expected_clarification,
    }


def _aggregate_metrics(
    cases: list[dict[str, Any]],
) -> dict[str, dict[str, int | float | None]]:
    metric_names = (
        "retrieval",
        "citation_grounding",
        "answer_correctness",
        "tool_selection",
        "refusal",
        "clarification",
    )
    aggregates: dict[str, dict[str, int | float | None]] = {}
    for metric in metric_names:
        values = [
            item["scores"][metric]
            for item in cases
            if item["scores"][metric] is not None
        ]
        passed = sum(value is True for value in values)
        measured = len(values)
        aggregates[metric] = {
            "passed": passed,
            "measured": measured,
            "rate": passed / measured if measured else None,
        }
    return aggregates


def _latency_summary(latencies: list[float]) -> dict[str, float | int | None]:
    if not latencies:
        return {
            "measured": 0,
            "min_ms": None,
            "median_ms": None,
            "p95_ms": None,
            "max_ms": None,
        }
    ordered = sorted(latencies)
    p95_index = max(0, min(len(ordered) - 1, (95 * len(ordered) + 99) // 100 - 1))
    return {
        "measured": len(ordered),
        "min_ms": ordered[0],
        "median_ms": statistics.median(ordered),
        "p95_ms": ordered[p95_index],
        "max_ms": ordered[-1],
    }


def _retrieval_ranking_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    comparable = [item for item in cases if item["expected"]["sources"]]
    ground_truth_documents: list[list[Document]] = []
    retrieved_documents: list[list[Document]] = []
    for item in comparable:
        ground_truth_documents.append(
            [
                Document(content="", meta={"source": source})
                for source in dict.fromkeys(item["expected"]["sources"])
            ]
        )
        ranked_sources = dict.fromkeys(
            citation["source"] for citation in item["actual"]["citations"]
        )
        retrieved_documents.append(
            [Document(content="", meta={"source": source}) for source in ranked_sources]
        )
    if not comparable:
        return {
            "evaluator": "haystack",
            "document_comparison_field": "meta.source",
            "evaluated_cases": 0,
            "recall": None,
            "mrr": None,
            "ndcg": None,
        }
    inputs = {
        "ground_truth_documents": ground_truth_documents,
        "retrieved_documents": retrieved_documents,
    }
    recall = DocumentRecallEvaluator(
        mode="multi_hit", document_comparison_field="meta.source"
    ).run(**inputs)
    mrr = DocumentMRREvaluator(document_comparison_field="meta.source").run(**inputs)
    ndcg = DocumentNDCGEvaluator(document_comparison_field="meta.source").run(**inputs)
    return {
        "evaluator": "haystack",
        "document_comparison_field": "meta.source",
        "evaluated_cases": len(comparable),
        "recall": recall["score"],
        "mrr": mrr["score"],
        "ndcg": ndcg["score"],
    }


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
        newline="\n",
    ) as temporary:
        json.dump(payload, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def run_evaluation(
    *,
    corpus_root: str | Path,
    gold_path: str | Path,
    output_path: str | Path,
    runtime_root: str | Path | None = None,
    additional_documents_root: str | Path | None = None,
    evaluation_context: dict[str, str] | None = None,
) -> dict[str, Any]:
    evaluation_started = datetime.now(UTC)
    corpus = Path(corpus_root).resolve()
    gold = Path(gold_path).resolve()
    output = Path(output_path).resolve()
    additional_documents = (
        Path(additional_documents_root).resolve()
        if additional_documents_root is not None
        else None
    )
    telemetry_path = _validate_corpus(corpus)
    cases = load_gold_cases(gold)

    temporary_runtime: tempfile.TemporaryDirectory[str] | None = None
    if runtime_root is None:
        temporary_runtime = tempfile.TemporaryDirectory(prefix="project-copilot-eval-")
        selected_runtime = Path(temporary_runtime.name)
    else:
        selected_runtime = Path(runtime_root).resolve()
    selected_runtime.mkdir(parents=True, exist_ok=True)

    try:
        project_id = f"eval-{uuid4().hex[:12]}"
        manager = WorkspaceManager(selected_runtime)
        manager.create_workspace(
            display_name="Project Aurora Offline Evaluation",
            project_id=project_id,
        )
        manager.activate(project_id)
        indexer = ProjectIndexer(manager)
        inventory = indexer.import_files(
            project_id, _load_imports(corpus, additional_documents)
        )
        analytics = GovernedAnalyticsTool(
            AnalyticsWorkspace.build(
                csv_path=telemetry_path,
                database_path=selected_runtime / f"{project_id}.duckdb",
            )
        )
        agent = ProjectAgent(
            project_id=project_id,
            indexer=indexer,
            analytics=analytics,
            chat_generator=DeterministicChatGenerator(),
            defrost_diagnostics=DefrostDiagnosticsEngine(
                corpus / "datasets" / "raw" / "defrost_telemetry.csv",
                DefrostRulePack.model_validate_json(
                    (
                        corpus
                        / "docs"
                        / "source"
                        / "configuration"
                        / "defrost-rules.json"
                    ).read_text(encoding="utf-8")
                ),
                DefrostAssetContext.model_validate_json(
                    (
                        corpus
                        / "docs"
                        / "source"
                        / "configuration"
                        / "defrost-asset-context.json"
                    ).read_text(encoding="utf-8")
                ),
            ),
        )

        measured_cases: list[dict[str, Any]] = []
        failed_execution_count = 0
        for case in cases:
            started = perf_counter()
            error: str | None = None
            try:
                answer = agent.ask(case.question)
                actual = {
                    "answer": answer.answer,
                    "refused": answer.refused,
                    "clarification": answer.clarification,
                    "tools": [activity.tool for activity in answer.activities],
                    "tool_trace": [asdict(activity) for activity in answer.activities],
                    "citations": [asdict(citation) for citation in answer.citations],
                }
                scores = _score_case(case, actual)
            except Exception as exc:  # noqa: BLE001 - failures must be recorded, not dropped
                failed_execution_count += 1
                error = f"{type(exc).__name__}: {str(exc)[:500]}"
                actual = {
                    "answer": "",
                    "refused": False,
                    "clarification": False,
                    "tools": [],
                    "tool_trace": [],
                    "citations": [],
                }
                scores = {
                    "retrieval": False if case.expected_sources else None,
                    "citation_grounding": False if case.grounding_terms else None,
                    "answer_correctness": False,
                    "tool_selection": False,
                    "refusal": False,
                    "clarification": False,
                }
            latency_ms = round((perf_counter() - started) * 1000, 3)
            applicable_scores = [
                value for value in scores.values() if value is not None
            ]
            measured_cases.append(
                {
                    "case_id": case.case_id,
                    "category": case.category,
                    "question": case.question,
                    "status": (
                        "error"
                        if error
                        else "passed"
                        if all(applicable_scores)
                        else "failed"
                    ),
                    "latency_ms": latency_ms,
                    "error": error,
                    "expected": {
                        "sources": list(case.expected_sources),
                        "answer_contains_all": list(case.answer_contains_all),
                        "grounding_terms": list(case.grounding_terms),
                        "tools": list(case.expected_tools),
                        "refused": case.expected_refused,
                        "clarification": case.expected_clarification,
                    },
                    "actual": actual,
                    "scores": scores,
                }
            )

        latencies = [item["latency_ms"] for item in measured_cases]
        report = {
            "schema_version": "1.0",
            "run_id": evaluation_started.strftime("%Y%m%dT%H%M%S.%fZ"),
            "started_at": evaluation_started.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "adapter": "project_copilot.agent.DeterministicChatGenerator",
            "offline": True,
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
            },
            "corpus": {
                "dataset": "project-aurora-synthetic-hvac",
                "sha256": _corpus_digest(corpus),
                "license": "CC0-1.0",
                "fully_synthetic": True,
                "telemetry_rows": analytics.workspace.metric_snapshot().row_count,
                "defrost_telemetry_rows": len(
                    (corpus / "datasets" / "raw" / "defrost_telemetry.csv")
                    .read_text(encoding="utf-8")
                    .splitlines()
                )
                - 1,
            },
            "source_inventory": [asdict(item) for item in inventory],
            "cases": measured_cases,
            "summary": {
                "case_count": len(measured_cases),
                "completed_count": len(measured_cases) - failed_execution_count,
                "failed_execution_count": failed_execution_count,
                "passed_all_applicable_metrics_count": sum(
                    item["status"] == "passed" for item in measured_cases
                ),
                "metrics": _aggregate_metrics(measured_cases),
                "retrieval_ranking": _retrieval_ranking_summary(measured_cases),
                "latency": _latency_summary(latencies),
            },
        }
        if evaluation_context:
            report["evaluation_context"] = dict(evaluation_context)
        _atomic_json_write(output, report)
        return report
    finally:
        if temporary_runtime is not None:
            temporary_runtime.cleanup()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the fully offline Project Copilot HVAC gold evaluation."
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=REPOSITORY_ROOT / "examples" / "synthetic_hvac",
    )
    parser.add_argument(
        "--gold",
        type=Path,
        default=REPOSITORY_ROOT / "evaluation" / "gold_cases.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT
        / "evaluation"
        / "results"
        / "deterministic-baseline.json",
    )
    parser.add_argument(
        "--runtime",
        type=Path,
        default=None,
        help="Optional generated-workspace directory; omitted uses a temporary directory.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_evaluation(
        corpus_root=args.corpus,
        gold_path=args.gold,
        output_path=args.output,
        runtime_root=args.runtime,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["failed_execution_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
