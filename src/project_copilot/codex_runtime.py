from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PACKAGE_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = PACKAGE_DIR.parents[1]
ASSET_DIR = PACKAGE_DIR / "codex_assets"
MCP_SERVER_NAME = "hvac"
PREFLIGHT_MARKER_NAME = "elevated-sandbox-preflight.json"
PREFLIGHT_SCHEMA_VERSION = 1
PERMISSIONS_PROFILE_VERSION = 1
DENIED_READ_EXIT_CODE = 73
MCP_TOOLS = {
    "schema": ("schema", "数据字段检查"),
    "data_quality": ("data-quality", "数据质量检查"),
    "cop_ranking": ("cop-ranking", "能效排名"),
}


class CodexRuntimeError(RuntimeError):
    """A public-safe Codex failure that never triggers a silent fallback."""


@dataclass(frozen=True)
class CodexRuntimeSettings:
    codex_bin: Path
    runtime_root: Path
    base_url: str
    api_key: str = field(repr=False)
    model: str
    python_executable: Path = field(default_factory=lambda: Path(sys.executable))
    reasoning_effort: str = "high"
    timeout_seconds: int = 240


@dataclass(frozen=True)
class PreparedCodexWorkspace:
    session_root: Path
    workspace_root: Path
    database_path: Path
    codex_home: Path
    config_file: Path
    output_schema: Path
    source_files: dict[str, Path | None]
    events_log: Path
    stderr_log: Path


@dataclass(frozen=True)
class CodexLaunch:
    argv: list[str]
    env: dict[str, str] = field(repr=False)
    prepared: PreparedCodexWorkspace


def _toml_string(value: str | Path) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


class CodexWorkspaceBuilder:
    def __init__(
        self,
        corpus_root: str | Path,
        settings: CodexRuntimeSettings,
    ) -> None:
        self.corpus_root = Path(corpus_root).resolve()
        self.settings = settings

    def prepare(self) -> PreparedCodexWorkspace:
        runtime_root = self.settings.runtime_root.resolve()
        if _path_is_within(runtime_root, REPOSITORY_ROOT):
            raise CodexRuntimeError("Codex runtime storage must be outside the repository")
        source_docs = self.corpus_root / "docs" / "source"
        source_database = self.corpus_root / "datasets" / "hvac_bakeoff.duckdb"
        if not source_docs.is_dir() or not source_database.is_file():
            raise CodexRuntimeError("Fixed synthetic Codex evidence is unavailable")

        session_root = runtime_root / "runs" / uuid.uuid4().hex
        workspace = session_root / "workspace"
        evidence_root = session_root / "private-evidence"
        codex_home = session_root / "codex-home"
        workspace.mkdir(parents=True, exist_ok=False)
        evidence_root.mkdir()
        codex_home.mkdir()

        copied_docs = workspace / "docs" / "source"
        shutil.copytree(source_docs, copied_docs)
        database_path = evidence_root / "hvac_bakeoff.duckdb"
        shutil.copy2(source_database, database_path)
        (workspace / "AGENTS.md").write_text(
            (ASSET_DIR / "AGENTS.template.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        source_files: dict[str, Path | None] = {}
        for path in sorted(copied_docs.rglob("*")):
            if not path.is_file():
                continue
            if path.name in source_files:
                raise CodexRuntimeError(
                    f"Duplicate human source filename in fixed corpus: {path.name}"
                )
            source_files[path.name] = path
        source_files["telemetry.csv"] = None

        output_schema = codex_home / "answer.schema.json"
        output_schema.write_text(
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["answer_markdown", "citations"],
                    "properties": {
                        "answer_markdown": {"type": "string", "minLength": 1},
                        "citations": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 8,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["filename", "excerpt"],
                                "properties": {
                                    "filename": {"type": "string", "minLength": 1},
                                    "excerpt": {"type": "string", "maxLength": 500},
                                },
                            },
                        },
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        config_file = codex_home / "config.toml"
        config_file.write_text(
            self._config_text(workspace, database_path, codex_home),
            encoding="utf-8",
        )
        return PreparedCodexWorkspace(
            session_root=session_root,
            workspace_root=workspace,
            database_path=database_path,
            codex_home=codex_home,
            config_file=config_file,
            output_schema=output_schema,
            source_files=source_files,
            events_log=session_root / "events.jsonl",
            stderr_log=session_root / "stderr.log",
        )

    def _config_text(
        self,
        workspace: Path,
        database_path: Path,
        codex_home: Path,
    ) -> str:
        base_url = self.settings.base_url.rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise CodexRuntimeError("Codex provider must use an HTTPS URL")
        python_path = PACKAGE_DIR.parent
        return "\n".join(
            [
                f"model = {_toml_string(self.settings.model)}",
                'model_provider = "company"',
                f"model_reasoning_effort = {_toml_string(self.settings.reasoning_effort)}",
                'approval_policy = "never"',
                'web_search = "disabled"',
                'default_permissions = "project-copilot"',
                "",
                "[model_providers.company]",
                'name = "Company"',
                f"base_url = {_toml_string(base_url)}",
                'wire_api = "responses"',
                'env_key = "CODEX_API_KEY"',
                "supports_websockets = false",
                "",
                "[shell_environment_policy]",
                'inherit = "core"',
                "ignore_default_excludes = false",
                'exclude = ["CODEX_API_KEY", "*KEY*", "*TOKEN*", "*SECRET*", "PROJECT_COPILOT_MCP_DATABASE"]',
                "",
                "[permissions.project-copilot.filesystem]",
                '":minimal" = "read"',
                '":tmpdir" = "write"',
                f"{_toml_string(workspace)} = \"read\"",
                f"{_toml_string(database_path.parent)} = \"deny\"",
                f"{_toml_string(codex_home)} = \"deny\"",
                "",
                "[permissions.project-copilot.network]",
                "enabled = false",
                "",
                f"[mcp_servers.{MCP_SERVER_NAME}]",
                f"command = {_toml_string(self.settings.python_executable)}",
                'args = ["-m", "project_copilot.codex_mcp_server"]',
                "required = true",
                "startup_timeout_sec = 15",
                "tool_timeout_sec = 45",
                'enabled_tools = ["schema", "data_quality", "cop_ranking"]',
                'default_tools_approval_mode = "auto"',
                "",
                f"[mcp_servers.{MCP_SERVER_NAME}.env]",
                f"PROJECT_COPILOT_MCP_DATABASE = {_toml_string(database_path)}",
                f"PYTHONPATH = {_toml_string(python_path)}",
                "",
                "[windows]",
                'sandbox = "elevated"',
                "sandbox_private_desktop = true",
                "",
            ]
        )


def _controlled_environment() -> dict[str, str]:
    allowed = {
        "APPDATA",
        "COMSPEC",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "PATH",
        "PATHEXT",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def build_codex_launch(
    settings: CodexRuntimeSettings,
    prepared: PreparedCodexWorkspace,
) -> CodexLaunch:
    argv = [
        str(settings.codex_bin),
        "exec",
        "--json",
        "--strict-config",
        "--ephemeral",
        "--cd",
        str(prepared.workspace_root),
        "--skip-git-repo-check",
        "--output-schema",
        str(prepared.output_schema),
        "--color",
        "never",
        "-",
    ]
    env = _controlled_environment()
    env["CODEX_API_KEY"] = settings.api_key
    env["CODEX_HOME"] = str(prepared.codex_home)
    return CodexLaunch(argv=argv, env=env, prepared=prepared)


def preflight_marker_path(settings: CodexRuntimeSettings) -> Path:
    return settings.runtime_root.resolve() / PREFLIGHT_MARKER_NAME


def _powershell_literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _sandbox_probe_command(
    settings: CodexRuntimeSettings,
    prepared: PreparedCodexWorkspace,
    script: str,
) -> list[str]:
    return [
        str(settings.codex_bin),
        "sandbox",
        "-P",
        "project-copilot",
        "-C",
        str(prepared.workspace_root),
        "--",
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        script,
    ]


def verify_elevated_sandbox_preflight(
    settings: CodexRuntimeSettings,
    corpus_root: str | Path,
    *,
    runner: Any = subprocess.run,
) -> Path:
    marker = preflight_marker_path(settings)
    marker.unlink(missing_ok=True)
    prepared = CodexWorkspaceBuilder(corpus_root, settings).prepare()
    allowed_path = prepared.workspace_root / "AGENTS.md"
    allowed_script = (
        "$ErrorActionPreference = 'Stop'; "
        f"Get-Content -LiteralPath {_powershell_literal(allowed_path)} "
        "-TotalCount 1 | Out-Null"
    )
    denied_path = prepared.database_path
    denied_script = (
        "$ErrorActionPreference = 'Stop'; "
        "try { "
        f"$stream = [System.IO.File]::OpenRead({_powershell_literal(denied_path)}); "
        "$null = $stream.ReadByte(); $stream.Dispose(); exit 0 "
        "} catch { "
        "$errorType = $_.Exception.GetType().FullName; "
        "$innerType = if ($_.Exception.InnerException) { "
        "$_.Exception.InnerException.GetType().FullName } else { '' }; "
        "if ($errorType -match 'UnauthorizedAccess|SecurityException' -or "
        "$innerType -match 'UnauthorizedAccess|SecurityException') { "
        f"exit {DENIED_READ_EXIT_CODE} }}; exit 74 }}"
    )
    env = _controlled_environment()
    env["CODEX_HOME"] = str(prepared.codex_home)
    run_kwargs = {
        "cwd": prepared.workspace_root,
        "env": env,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 30,
        "check": False,
    }
    try:
        allowed = runner(
            _sandbox_probe_command(settings, prepared, allowed_script),
            **run_kwargs,
        )
        if allowed.returncode != 0:
            raise CodexRuntimeError(
                "Codex elevated sandbox preflight could not read allowed workspace evidence"
            )
        denied = runner(
            _sandbox_probe_command(settings, prepared, denied_script),
            **run_kwargs,
        )
    except subprocess.TimeoutExpired as exc:
        raise CodexRuntimeError("Codex elevated sandbox preflight timed out") from exc
    if denied.returncode == 0:
        raise CodexRuntimeError(
            "Codex elevated sandbox preflight failed to block private database"
        )
    if denied.returncode != DENIED_READ_EXIT_CODE:
        raise CodexRuntimeError(
            "Codex elevated sandbox preflight could not verify private database denial"
        )

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "schema_version": PREFLIGHT_SCHEMA_VERSION,
                "permissions_profile_version": PERMISSIONS_PROFILE_VERSION,
                "status": "passed",
                "codex_bin": str(settings.codex_bin.resolve()),
                "verified_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return marker


def validate_elevated_sandbox_preflight(settings: CodexRuntimeSettings) -> None:
    marker = preflight_marker_path(settings)
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise CodexRuntimeError(
            "Codex elevated sandbox preflight has not passed"
        ) from exc
    if (
        payload.get("schema_version") != PREFLIGHT_SCHEMA_VERSION
        or payload.get("permissions_profile_version")
        != PERMISSIONS_PROFILE_VERSION
        or payload.get("status") != "passed"
    ):
        raise CodexRuntimeError("Codex elevated sandbox preflight is outdated")
    if payload.get("codex_bin") != str(settings.codex_bin.resolve()):
        raise CodexRuntimeError(
            "Codex elevated sandbox preflight used a different Codex executable"
        )


class CodexTurnParser:
    def __init__(self, prepared: PreparedCodexWorkspace) -> None:
        self.prepared = prepared

    def parse(self, jsonl: str) -> dict[str, Any]:
        answer_text = ""
        completed = False
        activities: list[dict[str, str]] = []
        data_evidence_tools: set[str] = set()
        try:
            events = [
                json.loads(line)
                for line in jsonl.splitlines()
                if line.strip()
            ]
        except (json.JSONDecodeError, TypeError) as exc:
            raise CodexRuntimeError(
                "Codex Agent did not produce a verifiable answer"
            ) from exc
        for event in events:
            event_type = event.get("type")
            if event_type == "turn.failed":
                raise CodexRuntimeError(
                    "Codex Agent did not produce a verifiable answer"
                )
            if event_type == "turn.completed":
                completed = True
                continue
            if event_type != "item.completed":
                continue
            item = event.get("item", {})
            if item.get("type") == "agent_message":
                answer_text = str(item.get("text", "")).strip()
                continue
            if item.get("type") != "mcp_tool_call":
                continue
            server = str(item.get("server", ""))
            tool = str(item.get("tool", ""))
            if server != MCP_SERVER_NAME or tool not in MCP_TOOLS:
                continue
            if item.get("status") != "completed" or item.get("error"):
                raise CodexRuntimeError("Governed data operation failed")
            result = item.get("result")
            structured_content = (
                result.get("structured_content")
                if isinstance(result, dict)
                else None
            )
            rows = (
                structured_content.get("result")
                if isinstance(structured_content, dict)
                else None
            )
            if not isinstance(rows, list) or not rows:
                raise CodexRuntimeError(
                    "Governed data operation did not return evidence"
                )
            tool_id, summary = MCP_TOOLS[tool]
            if tool in {"data_quality", "cop_ranking"}:
                data_evidence_tools.add(tool_id)
            if not any(activity["tool"] == tool_id for activity in activities):
                activities.append(
                    {"tool": tool_id, "status": "completed", "summary": summary}
                )
        if not completed or not answer_text:
            raise CodexRuntimeError("Codex Agent did not produce a verifiable answer")
        try:
            structured = json.loads(answer_text)
            answer = str(structured["answer_markdown"]).strip()
            requested_citations = structured["citations"]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise CodexRuntimeError(
                "Codex Agent did not produce a verifiable answer"
            ) from exc
        if not answer or not isinstance(requested_citations, list):
            raise CodexRuntimeError("Codex Agent did not produce a verifiable answer")
        citations = self._verified_citations(
            requested_citations,
            data_evidence_tools,
        )
        if not citations:
            raise CodexRuntimeError("Codex Agent answer lacked verified project evidence")
        has_documents = any(item["filename"] != "telemetry.csv" for item in citations)
        mode = "combined" if activities and has_documents else "data" if activities else "knowledge"
        return {
            "mode": mode,
            "demo_mode": False,
            "model_backed": True,
            "answer_markdown": answer,
            "tables": [],
            "charts": [],
            "citations": citations,
            "activities": activities,
            "clarification": False,
            "refused": False,
            "grounding_status": "grounded",
        }

    def _verified_citations(
        self,
        requested: list[object],
        data_evidence_tools: set[str],
    ) -> list[dict[str, object]]:
        citations: list[dict[str, object]] = []
        seen: set[str] = set()
        for raw in requested:
            if not isinstance(raw, dict):
                raise CodexRuntimeError("Codex citation payload is invalid")
            filename = str(raw.get("filename", "")).strip()
            excerpt = str(raw.get("excerpt", "")).strip()
            if not filename or filename in seen or filename not in self.prepared.source_files:
                raise CodexRuntimeError("Codex citation filename is not verified")
            source = self.prepared.source_files[filename]
            if source is None:
                if filename != "telemetry.csv" or not data_evidence_tools:
                    raise CodexRuntimeError("Codex data citation is not verified")
                excerpt = "只读合成遥测快照；计算由受控 MCP 工具完成。"
                location = "datasets/telemetry.csv"
                role = "dataset"
            else:
                content = source.read_text(encoding="utf-8", errors="strict")
                if not excerpt or len(excerpt) > 500 or excerpt not in content:
                    raise CodexRuntimeError("Codex citation excerpt is not verified")
                location = source.relative_to(self.prepared.workspace_root).as_posix()
                role = source.parent.name
            citations.append(
                {
                    "filename": filename,
                    "excerpt": excerpt,
                    "location": location,
                    "source_status": "只读证据",
                    "source_role": role,
                    "support_share_pct": 0,
                }
            )
            seen.add(filename)
        return citations


class CodexProcessRunner:
    def run(self, launch: CodexLaunch, prompt: str, timeout_seconds: int) -> str:
        launch.prepared.events_log.parent.mkdir(parents=True, exist_ok=True)
        try:
            with launch.prepared.events_log.open(
                "w", encoding="utf-8"
            ) as stdout, launch.prepared.stderr_log.open(
                "w", encoding="utf-8"
            ) as stderr:
                completed = subprocess.run(
                    launch.argv,
                    input=prompt,
                    env=launch.env,
                    stdout=stdout,
                    stderr=stderr,
                    text=True,
                    encoding="utf-8",
                    timeout=timeout_seconds,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise CodexRuntimeError(
                "Codex Agent exceeded the configured time budget"
            ) from exc
        if completed.returncode != 0:
            raise CodexRuntimeError(
                f"Codex Agent exited with code {completed.returncode}; see private runtime log"
            )
        return launch.prepared.events_log.read_text(encoding="utf-8")


class CodexRuntime:
    WORKFLOWS = {
        "project-overview": (
            "Summarize current project goals, decisions, open actions, and evidence. "
            "Use targeted document inspection; call data tools only when needed."
        ),
        "configuration-review": (
            "Reconcile current, superseded, meeting, and decision evidence. "
            "State effective dates and conflicts before drawing conclusions."
        ),
        "data-quality-efficiency": (
            "Call the governed data_quality and cop_ranking MCP tools, then read only "
            "the few project documents needed to interpret operating-condition limits."
        ),
    }
    workspace_name = "Agentic HVAC Bakeoff"

    def __init__(
        self,
        settings: CodexRuntimeSettings,
        corpus_root: str | Path,
        *,
        runner: Any | None = None,
    ) -> None:
        self.settings = settings
        self.corpus_root = Path(corpus_root).resolve()
        self.builder = CodexWorkspaceBuilder(self.corpus_root, settings)
        self.runner = runner or CodexProcessRunner()
        self.model = settings.model
        self.provider_is_remote = (
            urlparse(settings.base_url).hostname or ""
        ).casefold() not in {"", "localhost", "127.0.0.1", "::1"}
        self.source_count = (
            sum(1 for path in (self.corpus_root / "docs" / "source").rglob("*") if path.is_file())
            + 1
        )

    @classmethod
    def from_environment(
        cls,
        *,
        corpus_root: str | Path,
        application_runtime: str | Path,
    ) -> CodexRuntime:
        from project_copilot.company_api import load_codex_switch_settings

        codex_bin = os.environ.get("PROJECT_COPILOT_CODEX_BIN", "").strip()
        if not codex_bin:
            raise CodexRuntimeError(
                "Codex mode requires PROJECT_COPILOT_CODEX_BIN"
            )
        provider = load_codex_switch_settings()
        runtime_root = Path(
            os.environ.get(
                "PROJECT_COPILOT_CODEX_RUNTIME_ROOT",
                str(Path(application_runtime) / "codex-agent"),
            )
        )
        settings = CodexRuntimeSettings(
            codex_bin=Path(codex_bin),
            runtime_root=runtime_root,
            base_url=provider.base_url,
            api_key=provider.api_key,
            model=provider.model,
            python_executable=Path(sys.executable),
            reasoning_effort=os.environ.get(
                "PROJECT_COPILOT_CODEX_REASONING_EFFORT", "high"
            ),
            timeout_seconds=int(
                os.environ.get("PROJECT_COPILOT_CODEX_TIMEOUT_SECONDS", "240")
            ),
        )
        validate_elevated_sandbox_preflight(settings)
        return cls(settings, corpus_root)

    async def answer_async(
        self,
        question: str,
        *,
        history: list[dict[str, str]] | None = None,
        workflow_id: str | None = None,
    ) -> dict[str, Any]:
        prepared = self.builder.prepare()
        prompt = self._prompt(question, history or [], workflow_id=workflow_id)
        launch = build_codex_launch(self.settings, prepared)
        try:
            jsonl = await asyncio.to_thread(
                self.runner.run,
                launch,
                prompt,
                self.settings.timeout_seconds,
            )
            return CodexTurnParser(prepared).parse(jsonl)
        except CodexRuntimeError:
            raise
        except Exception as exc:
            raise CodexRuntimeError(
                "Codex Agent did not produce a verifiable answer"
            ) from exc

    @classmethod
    def _prompt(
        cls,
        question: str,
        history: list[dict[str, str]],
        *,
        workflow_id: str | None,
    ) -> str:
        workflow = ""
        if workflow_id is not None:
            try:
                workflow = cls.WORKFLOWS[workflow_id]
            except KeyError as exc:
                raise CodexRuntimeError("Unknown named workflow") from exc
        compact_history = "\n".join(
            f"{turn.get('role', 'unknown')}: {str(turn.get('content', ''))[:2000]}"
            for turn in history[-6:]
        )
        return "\n\n".join(
            part
            for part in [
                "You are the private Project Copilot analysis agent. Follow AGENTS.md.",
                f"Named workflow: {workflow_id}\n{workflow}" if workflow else "",
                f"Recent conversation:\n{compact_history}" if compact_history else "",
                f"Current engineer question:\n{question}",
                (
                    "Return the required JSON object. answer_markdown must lead with the direct "
                    "conclusion and use a compact Markdown table only when useful. Every citation "
                    "must use an exact human filename and an exact excerpt copied from that source. "
                    "For telemetry.csv use an empty excerpt; it is accepted only after a governed MCP call."
                ),
            ]
            if part
        )
