from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from haystack.components.generators.chat import OpenAIChatGenerator
from haystack.utils import Secret
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from project_copilot.agent import DeterministicChatGenerator, ProjectAgent
from project_copilot.analysis import AnalysisIntentError, ApprovedAnalysisEngine
from project_copilot.analytics import AnalyticsValidationError, AnalyticsWorkspace
from project_copilot.company_api import CompanyAPISettings
from project_copilot.contract import ProjectPackage, load_project_package
from project_copilot.defrost_diagnostics import (
    DefrostAssetContext,
    DefrostDiagnosticsEngine,
    DefrostRulePack,
)
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
    if mode != "company":
        raise RuntimeError(f"Unsupported model mode: {mode}")
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
    )
    http_client_kwargs: dict[str, object] = {
        "trust_env": False,
        "verify": build_tls_context(os.environ.get("PROJECT_COPILOT_CA_BUNDLE")),
    }
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

    legacy_knowledge, knowledge_provider_name = resolve_knowledge_provider(package)
    knowledge_provider_display = {
        "haystack-local": "Haystack BM25",
        "anythingllm-query": "AnythingLLM query",
    }[knowledge_provider_name]
    manager = WorkspaceManager(selected_runtime)
    embedding_backend = _build_embedding_backend()
    indexer = ProjectIndexer(
        manager,
        embedding_backend=embedding_backend,
        reranker=_build_reranker(),
    )
    _bootstrap_workspace(manager, indexer, package)
    if embedding_backend is not None:
        for workspace in manager.list_workspaces():
            indexer.reindex(workspace.project_id)
    chat_generator, model_mode = _build_chat_generator()
    egress_detail = {
        "chat": (
            "approved-remote"
            if model_mode == "company-openai-compatible"
            and _is_remote_endpoint(
                os.environ.get("PROJECT_COPILOT_OPENAI_BASE_URL", "")
            )
            else "loopback"
            if model_mode == "company-openai-compatible"
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

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):  # type: ignore[no-untyped-def]
        active = manager.active_workspace()
        model_mode_display = (
            "Demonstration test mode - not for field decisions"
            if model_mode == "deterministic-test-double"
            else model_mode
        )
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "package": package,
                "active_workspace": active,
                "workspaces": [
                    workspace_payload(item.project_id)
                    for item in manager.list_workspaces()
                ],
                "sources": indexer.list_sources(active.project_id),
                "metrics": analytics_summary_payload(active.project_id),
                "knowledge_provider_display": knowledge_provider_display,
                "model_mode": model_mode_display,
                "egress_display": egress_display,
                "downstream_approval_acknowledged": downstream_approval_acknowledged,
            },
        )

    @app.get("/api/health")
    def health() -> dict[str, object]:
        active = manager.active_workspace()
        return {
            "status": "ok",
            "project_id": active.project_id,
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
