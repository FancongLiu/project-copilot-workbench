from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from haystack.components.generators.chat import (
    OpenAIChatGenerator,
    OpenAIResponsesChatGenerator,
)
from haystack.utils import Secret
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from project_copilot.agent import DeterministicChatGenerator, ProjectAgent
from project_copilot.analysis import AnalysisIntentError, ApprovedAnalysisEngine
from project_copilot.analytics import AnalyticsValidationError, AnalyticsWorkspace
from project_copilot.company_api import (
    CompanyAPISettings,
    load_codex_switch_settings,
)
from project_copilot.codex_runtime import CodexRuntime, CodexRuntimeError
from project_copilot.contract import ProjectPackage, load_project_package
from project_copilot.defrost_diagnostics import (
    DefrostAssetContext,
    DefrostDiagnosticsEngine,
    DefrostRulePack,
)
from project_copilot.direction import DirectionAgent, DirectionDemo, DirectionToolbox
from project_copilot.embeddings import OpenAIEmbeddingBackend
from project_copilot.ingestion import ImportedFile, IngestionError, ProjectIndexer
from project_copilot.ingestion import SentenceTransformersReranker
from project_copilot.providers import resolve_knowledge_provider
from project_copilot.semantic_analytics import (
    GovernedAnalyticsResult,
    GovernedAnalyticsTool,
)
from project_copilot.workspaces import WorkspaceError, WorkspaceManager
from project_copilot.tls import build_tls_context


PACKAGE_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = PACKAGE_DIR.parents[1]


class QuestionRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1_000)
    request_id: str | None = Field(default=None, min_length=1, max_length=100)


class DirectionTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2_000)


class DirectionQuestionRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1_000)
    history: list[DirectionTurn] = Field(default_factory=list, max_length=6)
    workflow_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9-]+$",
    )


class WorkspaceCreateRequest(BaseModel):
    project_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,63}$")
    display_name: str = Field(min_length=1, max_length=100)


class UnavailableAnalyticsTool:
    OPERATIONS = GovernedAnalyticsTool.OPERATIONS

    def __init__(
        self,
        summary: str = "No approved telemetry dataset is imported in this workspace.",
    ) -> None:
        self.summary = summary

    def run(self, operation: str) -> GovernedAnalyticsResult:
        if operation not in UnavailableAnalyticsTool.OPERATIONS:
            raise ValueError("Analytics operation is not allowlisted")
        return GovernedAnalyticsResult(
            operation=operation,
            title="Dataset required",
            summary=self.summary,
            sql="",
            rows=[],
            chart_type="none",
        )


def _category_for_path(path: Path) -> str:
    normalized = path.as_posix().casefold()
    if "decision" in normalized:
        return "decision"
    if "meeting" in normalized:
        return "meeting"
    if "sop" in normalized or "safety" in normalized or "procedure" in normalized:
        return "SOP"
    if "config" in normalized or "control" in normalized:
        return "configuration"
    if path.suffix.casefold() == ".csv" or "dataset" in normalized:
        return "dataset"
    return "background"


def _bootstrap_workspace(
    manager: WorkspaceManager, indexer: ProjectIndexer, package: ProjectPackage
) -> None:
    if manager.list_workspaces():
        try:
            manager.active_workspace()
        except WorkspaceError:
            manager.activate(manager.list_workspaces()[0].project_id)
        return
    workspace = manager.create_workspace(
        display_name=package.display_name,
        project_id=package.project_id,
    )
    imported: list[ImportedFile] = []
    for root in (package.documents_root, package.datasets_root):
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise RuntimeError(
                    f"Project source cannot be a symbolic link: {path.name}"
                )
            if not path.is_file() or path.suffix.casefold() not in {
                ".md",
                ".txt",
                ".json",
                ".csv",
            }:
                continue
            imported.append(
                ImportedFile(
                    filename=path.name,
                    content=path.read_bytes(),
                    category=_category_for_path(path),
                )
            )
    if imported:
        indexer.import_files(workspace.project_id, imported)
    manager.activate(workspace.project_id)


def _build_chat_generator():  # type: ignore[no-untyped-def]
    mode = os.getenv("PROJECT_COPILOT_MODEL_MODE", "deterministic").casefold()
    if mode == "deterministic":
        return DeterministicChatGenerator(), "deterministic-test-double"
    if mode == "codex-switch":
        settings = load_codex_switch_settings()
    elif mode == "company":
        allowed_hosts = tuple(
            host.strip()
            for host in os.environ.get("PROJECT_COPILOT_ALLOWED_HOSTS", "").split(",")
            if host.strip()
        )
        settings = CompanyAPISettings(
            base_url=os.environ.get("PROJECT_COPILOT_OPENAI_BASE_URL", ""),
            api_key=os.environ.get("PROJECT_COPILOT_OPENAI_API_KEY", ""),
            model=os.environ.get("PROJECT_COPILOT_OPENAI_MODEL", ""),
            allowed_hosts=allowed_hosts,
            wire_api=os.environ.get(
                "PROJECT_COPILOT_OPENAI_WIRE_API", "chat_completions"
            ),
        )
    else:
        raise RuntimeError(f"Unsupported model mode: {mode}")
    http_client_kwargs: dict[str, object] = {
        "trust_env": False,
        "verify": build_tls_context(os.environ.get("PROJECT_COPILOT_CA_BUNDLE")),
    }
    if settings.wire_api == "responses":
        return (
            OpenAIResponsesChatGenerator(
                api_key=Secret.from_token(settings.api_key),
                model=settings.model,
                api_base_url=settings.base_url,
                timeout=150,
                max_retries=0,
                generation_kwargs={
                    "store": False,
                    "reasoning": {
                        "effort": os.environ.get(
                            "PROJECT_COPILOT_REASONING_EFFORT", "high"
                        )
                    },
                },
                tools_strict=True,
                http_client_kwargs=http_client_kwargs,
            ),
            "codex-switch-responses"
            if mode == "codex-switch"
            else "company-openai-responses",
        )
    return (
        OpenAIChatGenerator(
            api_key=Secret.from_token(settings.api_key),
            model=settings.model,
            api_base_url=settings.base_url.rstrip("/"),
            timeout=15,
            max_retries=0,
            generation_kwargs={"temperature": 0},
            tools_strict=True,
            http_client_kwargs=http_client_kwargs,
        ),
        "company-openai-compatible",
    )


def _build_embedding_backend():  # type: ignore[no-untyped-def]
    model = os.environ.get("PROJECT_COPILOT_EMBEDDING_MODEL", "").strip()
    if not model:
        return None
    if (
        os.environ.get("PROJECT_COPILOT_ACK_EMBEDDINGS_APPROVED", "").casefold()
        != "true"
    ):
        raise RuntimeError(
            "Company embedding egress requires PROJECT_COPILOT_ACK_EMBEDDINGS_APPROVED=true"
        )
    allowed_hosts = tuple(
        host.strip()
        for host in os.environ.get("PROJECT_COPILOT_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    )
    settings = CompanyAPISettings(
        base_url=os.environ.get(
            "PROJECT_COPILOT_EMBEDDING_BASE_URL",
            os.environ.get("PROJECT_COPILOT_OPENAI_BASE_URL", ""),
        ),
        api_key=os.environ.get(
            "PROJECT_COPILOT_EMBEDDING_API_KEY",
            os.environ.get("PROJECT_COPILOT_OPENAI_API_KEY", ""),
        ),
        model=model,
        allowed_hosts=allowed_hosts,
    )
    return OpenAIEmbeddingBackend(
        settings,
        ca_bundle=os.environ.get("PROJECT_COPILOT_CA_BUNDLE", "").strip() or None,
    )


def _build_reranker():  # type: ignore[no-untyped-def]
    model_path = os.environ.get("PROJECT_COPILOT_RERANKER_MODEL_PATH", "").strip()
    if not model_path:
        return None
    if os.environ.get("PROJECT_COPILOT_ACK_RERANKER_APPROVED", "").casefold() != "true":
        raise RuntimeError(
            "Local reranking requires PROJECT_COPILOT_ACK_RERANKER_APPROVED=true"
        )
    return SentenceTransformersReranker(model_path)


def _is_remote_endpoint(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").casefold()
    return hostname not in {"", "localhost", "127.0.0.1", "::1"}


def create_app(
    *,
    project_root: str | Path | None = None,
    runtime_root: str | Path | None = None,
) -> FastAPI:
    bundled_demo = PACKAGE_DIR / "demo_project"
    source_demo = REPOSITORY_ROOT / "examples" / "synthetic_hvac"
    default_project = bundled_demo if bundled_demo.is_dir() else source_demo
    package = load_project_package(Path(project_root or default_project))
    selected_runtime = Path(
        runtime_root or Path(tempfile.gettempdir()) / "project-copilot-workbench-v2"
    ).resolve()
    selected_runtime.mkdir(parents=True, exist_ok=True)
    direction_runtime_mode = os.environ.get(
        "PROJECT_COPILOT_AGENT_RUNTIME", "haystack"
    ).casefold()
    if direction_runtime_mode not in {"codex", "haystack"}:
        raise RuntimeError(f"Unsupported Agent runtime: {direction_runtime_mode}")

    legacy_knowledge, knowledge_provider_name = resolve_knowledge_provider(package)
    manager = WorkspaceManager(selected_runtime)
    embedding_backend = (
        None if direction_runtime_mode == "codex" else _build_embedding_backend()
    )
    indexer = ProjectIndexer(
        manager,
        embedding_backend=embedding_backend,
        reranker=None if direction_runtime_mode == "codex" else _build_reranker(),
    )
    _bootstrap_workspace(manager, indexer, package)
    if embedding_backend is not None:
        for workspace in manager.list_workspaces():
            indexer.reindex(workspace.project_id)
    chat_generator, model_mode = _build_chat_generator()
    chat_base_url = str(getattr(chat_generator, "api_base_url", "") or "")
    model_backed = model_mode != "deterministic-test-double"
    egress_detail = {
        "chat": (
            "approved-remote"
            if model_backed and _is_remote_endpoint(chat_base_url)
            else "loopback"
            if model_backed
            else "disabled"
        ),
        "embedding": (
            "approved-remote"
            if embedding_backend is not None
            and _is_remote_endpoint(
                os.environ.get(
                    "PROJECT_COPILOT_EMBEDDING_BASE_URL",
                    os.environ.get("PROJECT_COPILOT_OPENAI_BASE_URL", ""),
                )
            )
            else "loopback"
            if embedding_backend is not None
            else "disabled"
        ),
        "knowledge": (
            "approved-remote"
            if knowledge_provider_name == "anythingllm-query"
            and _is_remote_endpoint(os.environ.get("ANYTHINGLLM_BASE_URL", ""))
            else "loopback"
            if knowledge_provider_name == "anythingllm-query"
            else "local"
        ),
    }
    egress_channel_labels = {
        "chat": "company-chat",
        "embedding": "company-embedding",
        "knowledge": "anythingllm-knowledge",
    }
    egress_channels = [
        egress_channel_labels[name]
        for name, state in egress_detail.items()
        if state == "approved-remote"
    ]
    downstream_approval_acknowledged = bool(egress_channels)
    egress_mode = "approved-provider" if egress_channels else "loopback-only"
    egress_display = "Approved company endpoint" if egress_channels else "Loopback only"

    default_csv = next(
        (
            path
            for path in sorted(package.datasets_root.glob("*.csv"))
            if path.is_file() and path.name == "telemetry.csv"
        ),
        None,
    )
    if default_csv is None:
        default_csv = next(
            (
                path
                for path in sorted(package.datasets_root.glob("*.csv"))
                if path.is_file()
            ),
            None,
        )
    if default_csv is None:
        raise RuntimeError("The bootstrap Project Package requires a CSV dataset")
    default_csv_digest = hashlib.sha256(default_csv.read_bytes()).hexdigest()
    default_analytics = AnalyticsWorkspace.build(
        csv_path=default_csv,
        database_path=(
            selected_runtime
            / "analytics"
            / f"{package.project_id}-bootstrap-{default_csv_digest}.duckdb"
        ),
    )
    legacy_analysis = ApprovedAnalysisEngine(default_analytics)

    app = FastAPI(
        title="Project Copilot Workbench",
        version="0.2.0",
        docs_url=None,
        redoc_url=None,
    )
    app.state.package = package
    app.state.workspace_manager = manager
    app.state.indexer = indexer
    app.state.knowledge = legacy_knowledge
    app.state.knowledge_provider_name = knowledge_provider_name
    app.state.analytics = default_analytics
    app.state.analysis = legacy_analysis
    app.state.model_mode = model_mode
    source_direction_corpus = REPOSITORY_ROOT / "examples" / "agentic_hvac_bakeoff"
    bundled_direction_corpus = PACKAGE_DIR / "direction_demo"
    direction_corpus = (
        source_direction_corpus
        if source_direction_corpus.is_dir()
        else bundled_direction_corpus
    )

    def search_active_workspace(query: str) -> list[dict[str, object]]:
        active = manager.active_workspace()
        normalized_query = query.casefold().replace("\\", "/")
        source_records = indexer.list_sources(active.project_id)

        def mentions_source_location(location: str) -> bool:
            normalized_location = location.casefold().replace("\\", "/")
            return (
                re.search(
                    rf"(?<![A-Za-z0-9_./-]){re.escape(normalized_location)}(?![A-Za-z0-9_./-])",
                    normalized_query,
                )
                is not None
            )

        path_matches = [
            source
            for source in source_records
            if source.source_location
            and mentions_source_location(source.source_location)
        ]
        basename_matches = [
            source
            for source in source_records
            if (source.original_filename or source.filename).casefold()
            in normalized_query
        ]
        exact_sources = path_matches or basename_matches
        if not path_matches and len(basename_matches) > 1:
            basename = (
                basename_matches[0].original_filename or basename_matches[0].filename
            )
            locations = sorted(
                source.source_location or source.original_filename or source.filename
                for source in basename_matches
            )
            return [
                {
                    "_clarification_message": (
                        f"找到多个名为 {basename} 的文件，请在问题中写明相对路径："
                        + "、".join(locations)
                    )
                }
            ]
        if exact_sources:
            exact_citations: list[dict[str, object]] = []
            for source in exact_sources:
                filtered = indexer.search(
                    active.project_id,
                    query,
                    source_ids={source.source_id},
                    top_k=3,
                )
                if filtered.refused or not filtered.citations:
                    location = (
                        source.source_location
                        or source.original_filename
                        or source.filename
                    )
                    return [
                        {
                            "_clarification_message": (
                                f"指定文件 {location} 已入库，但没有检索到足以回答该问题的内容。"
                            )
                        }
                    ]
                for citation in filtered.citations:
                    exact_citations.append(
                        {
                            "filename": citation.source,
                            "excerpt": citation.excerpt[:4_000],
                            "location": citation.source_location
                            or source.source_location
                            or citation.source,
                            "source_status": "\u5df2\u5165\u5e93",
                            "source_role": citation.category,
                            "support_weight": max(float(citation.score), 0.1),
                            "_exact_filename": True,
                        }
                    )
            return exact_citations

        citations: list[dict[str, object]] = []
        result = indexer.search(active.project_id, query, top_k=5)
        for citation in result.citations:
            location_parts = [citation.source_location or citation.source]
            if citation.page is not None:
                location_parts.append(f"page {citation.page}")
            elif citation.section:
                location_parts.append(citation.section)
            citations.append(
                {
                    "filename": citation.source,
                    "excerpt": citation.excerpt[:500],
                    "location": " · ".join(location_parts),
                    "source_status": "\u5df2\u5165\u5e93",
                    "source_role": citation.category,
                    "support_weight": max(float(citation.score), 0.1),
                }
            )
        return citations

    if direction_runtime_mode == "codex":
        app.state.direction_demo = CodexRuntime.from_environment(
            corpus_root=direction_corpus,
            application_runtime=selected_runtime,
        )
        model_backed = True
        app.state.model_mode = "codex-agent-runtime"
        codex_remote = bool(
            getattr(app.state.direction_demo, "provider_is_remote", True)
        )
        egress_detail = {
            "chat": "approved-remote" if codex_remote else "loopback",
            "embedding": "disabled",
            "knowledge": "local",
        }
        egress_channels = ["company-chat"] if codex_remote else []
        downstream_approval_acknowledged = codex_remote
        egress_mode = "approved-provider" if codex_remote else "loopback-only"
        egress_display = (
            "Approved company endpoint" if codex_remote else "Loopback only"
        )
    elif direction_runtime_mode == "haystack":
        app.state.direction_demo = (
            DirectionAgent(
                DirectionToolbox(
                    direction_corpus,
                    workspace_search=search_active_workspace,
                ),
                chat_generator,
            )
            if model_backed
            else DirectionDemo(direction_corpus)
        )
    app.state.direction_runtime_mode = direction_runtime_mode
    app.state.direction_model_backed = model_backed
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "::1", "[::1]", "testserver"],
    )
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

    def workspace_payload(project_id: str) -> dict[str, object]:
        workspace = next(
            (
                item
                for item in manager.list_workspaces()
                if item.project_id == project_id
            ),
            None,
        )
        if workspace is None:
            raise HTTPException(status_code=404, detail="Unknown workspace")
        active = manager.active_workspace().project_id == project_id
        return {
            "project_id": workspace.project_id,
            "display_name": workspace.display_name,
            "active": active,
            "source_count": len(indexer.list_sources(project_id)),
        }

    def analytics_workspace_for(
        project_id: str,
    ) -> tuple[AnalyticsWorkspace | None, dict[str, object]]:
        workspace_payload(project_id)
        workspace = next(
            item for item in manager.list_workspaces() if item.project_id == project_id
        )
        dataset = next(
            (
                source
                for source in indexer.list_sources(project_id)
                if source.category == "dataset"
                and source.filename.casefold() == "telemetry.csv"
            ),
            None,
        )
        if dataset is None:
            return None, {
                "state": "missing",
                "dataset_filename": None,
                "source_id": None,
                "error": None,
            }
        if dataset.status == "error":
            return None, {
                "state": "invalid",
                "dataset_filename": dataset.filename,
                "source_id": dataset.source_id,
                "error": (dataset.error or "Telemetry validation failed")[:500],
            }
        database_path = workspace.root / "analytics" / f"{dataset.sha256}.duckdb"
        if not database_path.is_file():
            return None, {
                "state": "unavailable",
                "dataset_filename": dataset.filename,
                "source_id": dataset.source_id,
                "error": "The immutable analytics snapshot is missing; rebuild the project index.",
            }
        analytics_workspace = AnalyticsWorkspace(database_path)
        try:
            analytics_workspace.metric_snapshot()
        except AnalyticsValidationError as exc:
            return None, {
                "state": "invalid",
                "dataset_filename": dataset.filename,
                "source_id": dataset.source_id,
                "error": str(exc)[:500],
            }
        return analytics_workspace, {
            "state": "ready",
            "dataset_filename": dataset.filename,
            "source_id": dataset.source_id,
            "error": None,
        }

    def analytics_for(project_id: str):  # type: ignore[no-untyped-def]
        analytics_workspace, state = analytics_workspace_for(project_id)
        if analytics_workspace is None:
            if state["state"] == "invalid":
                return UnavailableAnalyticsTool(
                    f"The imported telemetry dataset failed validation: {state['error']}"
                )
            if state["state"] == "unavailable":
                return UnavailableAnalyticsTool(str(state["error"]))
            return UnavailableAnalyticsTool()
        return GovernedAnalyticsTool(analytics_workspace)

    def analytics_summary_payload(project_id: str) -> dict[str, object]:
        analytics_workspace, state = analytics_workspace_for(project_id)
        if analytics_workspace is None:
            return {
                "project_id": project_id,
                "available": False,
                **state,
                "row_count": 0,
                "average_power_kw": None,
                "average_delta_t_c": None,
                "average_cop": None,
            }
        return {
            "project_id": project_id,
            "available": True,
            **state,
            **asdict(analytics_workspace.metric_snapshot()),
        }

    def defrost_for(project_id: str):  # type: ignore[no-untyped-def]
        sources = indexer.list_sources(project_id)
        dataset = next(
            (
                source
                for source in sources
                if source.category == "dataset"
                and source.filename.casefold() == "defrost_telemetry.csv"
                and source.status == "indexed"
            ),
            None,
        )
        rules = next(
            (
                source
                for source in sources
                if source.filename.casefold() == "defrost-rules.json"
                and source.status == "indexed"
            ),
            None,
        )
        asset_context = next(
            (
                source
                for source in sources
                if source.filename.casefold() == "defrost-asset-context.json"
                and source.status == "indexed"
            ),
            None,
        )
        if dataset is None or rules is None or asset_context is None:
            return None
        try:
            rule_pack = DefrostRulePack.model_validate_json(
                indexer.source_path(project_id, rules.source_id).read_text(
                    encoding="utf-8"
                )
            )
            context = DefrostAssetContext.model_validate_json(
                indexer.source_path(project_id, asset_context.source_id).read_text(
                    encoding="utf-8"
                )
            )
            return DefrostDiagnosticsEngine(
                indexer.source_path(project_id, dataset.source_id),
                rule_pack,
                context,
            )
        except (OSError, ValueError):
            return None

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.url.path.startswith("/api/")
            and request.headers.get("X-Project-Copilot") != "1"
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": "Missing same-origin request header"},
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    architectures = {
        "baseline": {
            "name": "基线：简洁证据 Chat",
            "description": "当前版本，连续问答与按需原始文件名证据。",
        },
        "conversation": {
            "name": "V1：连续对话台",
            "description": "生成中继续追加问题，按队列保持连续工作。",
        },
        "evidence": {
            "name": "V2：答案与证据工作台",
            "description": "完整答案居中，原文与检索路径在按需证据面板核查。",
        },
        "canvas": {
            "name": "V3：工程成果画布",
            "description": "聊天保持简短，长报告、表格和图表进入稳定成果区。",
        },
    }

    def direction_response(
        request: Request,
        architecture: str,
        *,
        show_version_nav: bool,
    ):  # type: ignore[no-untyped-def]
        if architecture not in architectures:
            raise HTTPException(status_code=404, detail="Unknown architecture version")
        active = manager.active_workspace()
        architecture_info = architectures[architecture]
        codex_mode = app.state.direction_runtime_mode == "codex"
        workspace_name = (
            app.state.direction_demo.workspace_name
            if codex_mode
            else active.display_name
        )
        source_count = (
            app.state.direction_demo.source_count
            if codex_mode
            else len(indexer.list_sources(active.project_id))
        )
        return templates.TemplateResponse(
            request=request,
            name="direction.html",
            context={
                "model_backed": model_backed,
                "model_name": (
                    app.state.direction_demo.model
                    if codex_mode
                    else getattr(chat_generator, "model", "离线测试")
                ),
                "egress_display": egress_display,
                "workspace_name": workspace_name,
                "source_count": source_count,
                "scope_label": (
                    "固定合成测试资料" if codex_mode else "本地私有索引"
                ),
                "uploads_enabled": not codex_mode,
                "architecture": architecture,
                "architecture_name": architecture_info["name"],
                "architecture_description": architecture_info["description"],
                "show_version_nav": show_version_nav,
            },
        )

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):  # type: ignore[no-untyped-def]
        return direction_response(request, "baseline", show_version_nav=False)

    @app.get("/workbench")
    def workbench(request: Request):  # type: ignore[no-untyped-def]
        del request
        return RedirectResponse(url="/", status_code=307)

    @app.get("/direction", response_class=HTMLResponse)
    def direction(request: Request):  # type: ignore[no-untyped-def]
        return direction_response(request, "baseline", show_version_nav=False)

    @app.get("/versions", response_class=HTMLResponse)
    def version_overview(request: Request):  # type: ignore[no-untyped-def]
        return templates.TemplateResponse(
            request=request,
            name="versions.html",
            context={
                "architectures": [
                    {"id": architecture_id, **architecture_info}
                    for architecture_id, architecture_info in architectures.items()
                ]
            },
        )

    @app.get("/versions/{architecture}", response_class=HTMLResponse)
    def version_page(
        request: Request,
        architecture: str,
    ):  # type: ignore[no-untyped-def]
        return direction_response(request, architecture, show_version_nav=True)

    @app.post("/api/direction/query")
    async def query_direction(payload: DirectionQuestionRequest) -> dict[str, object]:
        history = [turn.model_dump() for turn in payload.history]
        async_answer = getattr(app.state.direction_demo, "answer_async", None)
        if async_answer is not None:
            try:
                if app.state.direction_runtime_mode == "codex":
                    return await async_answer(
                        payload.question,
                        history=history,
                        workflow_id=payload.workflow_id,
                    )
                return await async_answer(payload.question, history=history)
            except CodexRuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        return app.state.direction_demo.answer(payload.question, history=history)

    @app.post("/api/direction/sources", status_code=201)
    async def upload_direction_sources(
        files: list[UploadFile] = File(...),
    ) -> dict[str, object]:
        if app.state.direction_runtime_mode == "codex":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Codex evaluation mode uses fixed synthetic evidence; "
                    "uploads are disabled until active-workspace isolation is implemented."
                ),
            )
        active = manager.active_workspace()
        try:
            if (
                len(files) == 1
                and Path(files[0].filename or "").suffix.casefold() == ".zip"
            ):
                payload = await files[0].read(ProjectIndexer.MAX_ARCHIVE_BYTES + 1)
                imported = indexer.import_archive(
                    active.project_id,
                    files[0].filename or "project-package.zip",
                    payload,
                )
            else:
                if any(
                    Path(upload.filename or "").suffix.casefold() == ".zip"
                    for upload in files
                ):
                    raise IngestionError("Upload one Project Package ZIP by itself")
                pending: list[ImportedFile] = []
                for upload in files:
                    filename = upload.filename or "unnamed"
                    payload = await upload.read(ProjectIndexer.MAX_FILE_BYTES + 1)
                    pending.append(
                        ImportedFile(
                            filename=filename,
                            content=payload,
                            category=_category_for_path(Path(filename)),
                        )
                    )
                imported = indexer.import_files(active.project_id, pending)
        except IngestionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "files": [
                {
                    "filename": item.original_filename or item.filename,
                    "category": item.category,
                    "status": item.status,
                }
                for item in imported
            ]
        }

    @app.get("/api/direction/graph")
    def direction_graph() -> dict[str, object]:
        active = manager.active_workspace()
        sources = indexer.list_sources(active.project_id)
        nodes: list[dict[str, str]] = [
            {"id": "project", "label": active.display_name, "kind": "project"}
        ]
        edges: list[dict[str, str]] = []
        folder_ids: dict[str, str] = {}
        for source in sorted(
            sources,
            key=lambda item: (item.source_location or item.filename).casefold(),
        ):
            display_filename = source.original_filename or source.filename
            location = (source.source_location or display_filename).replace("\\", "/")
            path_parts = [part for part in location.split("/") if part]
            directories = path_parts[:-1]
            parent_id = "project"
            accumulated: list[str] = []
            for directory in directories:
                accumulated.append(directory)
                directory_path = "/".join(accumulated)
                folder_id = folder_ids.get(directory_path)
                if folder_id is None:
                    folder_id = (
                        "folder-"
                        + hashlib.sha256(directory_path.encode("utf-8")).hexdigest()[
                            :12
                        ]
                    )
                    folder_ids[directory_path] = folder_id
                    nodes.append(
                        {
                            "id": folder_id,
                            "label": directory,
                            "kind": "folder",
                            "location": directory_path,
                        }
                    )
                    edges.append(
                        {
                            "id": f"{parent_id}-{folder_id}",
                            "source": parent_id,
                            "target": folder_id,
                            "kind": "contains",
                        }
                    )
                parent_id = folder_id
            file_id = (
                "file-"
                + hashlib.sha256(source.source_id.encode("utf-8")).hexdigest()[:12]
            )
            nodes.append(
                {
                    "id": file_id,
                    "label": display_filename,
                    "kind": "file",
                    "category": source.category,
                    "location": location,
                }
            )
            edges.append(
                {
                    "id": f"{parent_id}-{file_id}",
                    "source": parent_id,
                    "target": file_id,
                    "kind": "contains",
                }
            )
        return {
            "nodes": nodes,
            "edges": edges,
            "summarized": False,
            "layout": "directory-flow",
        }

    @app.get("/api/health")
    def health() -> dict[str, object]:
        active = manager.active_workspace()
        return {
            "status": "ok",
            "project_id": active.project_id,
            "agent_runtime": app.state.direction_runtime_mode,
            "knowledge_provider": knowledge_provider_name,
            "network_allowed": bool(egress_channels),
            "nl2sql_allowed": package.security.allow_nl2sql,
            "egress_mode": egress_mode,
            "egress_channels": egress_channels,
            "egress_detail": egress_detail,
            "downstream_approval_acknowledged": downstream_approval_acknowledged,
        }

    @app.get("/api/workspaces")
    def list_workspaces() -> list[dict[str, object]]:
        return [
            workspace_payload(item.project_id) for item in manager.list_workspaces()
        ]

    @app.post("/api/workspaces", status_code=201)
    def create_workspace(payload: WorkspaceCreateRequest) -> dict[str, object]:
        try:
            manager.create_workspace(
                display_name=payload.display_name, project_id=payload.project_id
            )
        except WorkspaceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return workspace_payload(payload.project_id)

    @app.post("/api/workspaces/{project_id}/activate")
    def activate_workspace(project_id: str) -> dict[str, object]:
        try:
            manager.activate(project_id)
        except WorkspaceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return workspace_payload(project_id)

    @app.get("/api/workspaces/{project_id}/sources")
    def list_sources(project_id: str) -> list[dict[str, object]]:
        workspace_payload(project_id)
        return [asdict(item) for item in indexer.list_sources(project_id)]

    @app.post("/api/workspaces/{project_id}/sources", status_code=201)
    async def upload_sources(
        project_id: str,
        category: str = Form(...),
        files: list[UploadFile] = File(...),
    ) -> list[dict[str, object]]:
        workspace_payload(project_id)
        try:
            if (
                len(files) == 1
                and Path(files[0].filename or "").suffix.casefold() == ".zip"
            ):
                payload = await files[0].read(ProjectIndexer.MAX_ARCHIVE_BYTES + 1)
                imported = indexer.import_archive(
                    project_id, files[0].filename or "project-package.zip", payload
                )
            else:
                pending: list[ImportedFile] = []
                for upload in files:
                    payload = await upload.read(ProjectIndexer.MAX_FILE_BYTES + 1)
                    pending.append(
                        ImportedFile(
                            filename=upload.filename or "unnamed",
                            content=payload,
                            category=category,
                        )
                    )
                imported = indexer.import_files(project_id, pending)
        except IngestionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return [asdict(item) for item in imported]

    @app.post("/api/workspaces/{project_id}/reindex")
    def reindex(project_id: str) -> dict[str, int]:
        workspace_payload(project_id)
        return {"indexed_chunks": indexer.reindex(project_id)}

    @app.delete("/api/workspaces/{project_id}/sources/{source_id}", status_code=204)
    def delete_source(project_id: str, source_id: str) -> Response:
        workspace_payload(project_id)
        try:
            indexer.delete_source(project_id, source_id)
        except IngestionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(status_code=204)

    @app.post("/api/workspaces/{project_id}/copilot/query")
    async def query_copilot(
        project_id: str, payload: QuestionRequest
    ) -> dict[str, object]:
        workspace = workspace_payload(project_id)
        agent = ProjectAgent(
            project_id=project_id,
            indexer=indexer,
            analytics=analytics_for(project_id),
            chat_generator=chat_generator,
            defrost_diagnostics=defrost_for(project_id),
        )
        return {
            "project_id": project_id,
            "display_name": workspace["display_name"],
            "request_id": payload.request_id,
            **asdict(await agent.ask_async(payload.question)),
        }

    @app.post("/api/knowledge/query")
    def query_knowledge(payload: QuestionRequest) -> dict[str, object]:
        return asdict(legacy_knowledge.query(payload.question))

    @app.get("/api/workspaces/{project_id}/analytics/summary")
    def analytics_summary(project_id: str) -> dict[str, object]:
        return analytics_summary_payload(project_id)

    @app.post("/api/analytics/analyze")
    def analyze(payload: QuestionRequest) -> dict[str, object]:
        try:
            return asdict(legacy_analysis.analyze(payload.question))
        except AnalysisIntentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app
