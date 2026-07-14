from __future__ import annotations

import tempfile
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from project_copilot.analysis import AnalysisIntentError, ApprovedAnalysisEngine
from project_copilot.analytics import AnalyticsWorkspace
from project_copilot.contract import load_project_package
from project_copilot.providers import resolve_knowledge_provider


PACKAGE_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = PACKAGE_DIR.parents[1]


class QuestionRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500)


def create_app(
    *,
    project_root: str | Path | None = None,
    runtime_root: str | Path | None = None,
) -> FastAPI:
    bundled_demo = PACKAGE_DIR / "demo_project"
    source_demo = REPOSITORY_ROOT / "examples" / "synthetic_hvac"
    default_project = bundled_demo if bundled_demo.is_dir() else source_demo
    selected_project = Path(project_root or default_project)
    selected_runtime = Path(
        runtime_root or Path(tempfile.gettempdir()) / "project-copilot-workbench"
    )
    selected_runtime.mkdir(parents=True, exist_ok=True)

    package = load_project_package(selected_project)
    knowledge, knowledge_provider_name = resolve_knowledge_provider(package)
    knowledge_provider_display = {
        "haystack-local": "Haystack BM25",
        "anythingllm-query": "AnythingLLM query",
    }[knowledge_provider_name]
    downstream_approval_acknowledged = knowledge_provider_name == "anythingllm-query"
    egress_mode = (
        "approved-provider" if downstream_approval_acknowledged else "loopback-only"
    )
    egress_display = (
        "Approved provider only"
        if downstream_approval_acknowledged
        else "Loopback only"
    )
    csv_files: list[Path] = []
    for candidate in sorted(package.datasets_root.glob("*.csv")):
        if candidate.is_symlink():
            raise RuntimeError("Dataset files cannot be symbolic links")
        try:
            resolved_candidate = candidate.resolve(strict=True)
        except OSError as exc:
            raise RuntimeError(
                f"Dataset file is unavailable: {candidate.name}"
            ) from exc
        if not resolved_candidate.is_relative_to(package.datasets_root):
            raise RuntimeError("Dataset files must stay inside the Project Package")
        if resolved_candidate.is_file():
            csv_files.append(resolved_candidate)
    if not csv_files:
        raise RuntimeError("The project package does not contain a CSV dataset")
    analytics = AnalyticsWorkspace.build(
        csv_path=csv_files[0],
        database_path=selected_runtime / f"{package.project_id}.duckdb",
    )
    analysis = ApprovedAnalysisEngine(analytics)
    metrics = analytics.metric_snapshot()

    app = FastAPI(
        title="Project Copilot Workbench",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
    )
    app.state.package = package
    app.state.knowledge = knowledge
    app.state.knowledge_provider_name = knowledge_provider_name
    app.state.analytics = analytics
    app.state.analysis = analysis
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "::1", "[::1]", "testserver"],
    )
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        if (
            request.method == "POST"
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
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "package": package,
                "metrics": metrics,
                "knowledge_provider_display": knowledge_provider_display,
                "egress_display": egress_display,
                "downstream_approval_acknowledged": downstream_approval_acknowledged,
            },
        )

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "project_id": package.project_id,
            "knowledge_provider": knowledge_provider_name,
            "network_allowed": package.security.allow_network,
            "nl2sql_allowed": package.security.allow_nl2sql,
            "egress_mode": egress_mode,
            "downstream_approval_acknowledged": downstream_approval_acknowledged,
        }

    @app.post("/api/knowledge/query")
    def query_knowledge(payload: QuestionRequest) -> dict[str, object]:
        return asdict(knowledge.query(payload.question))

    @app.get("/api/analytics/summary")
    def analytics_summary() -> dict[str, object]:
        return asdict(analytics.metric_snapshot())

    @app.post("/api/analytics/analyze")
    def analyze(payload: QuestionRequest) -> dict[str, object]:
        try:
            return asdict(analysis.analyze(payload.question))
        except AnalysisIntentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app
