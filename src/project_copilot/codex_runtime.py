from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import math
import os
import re
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
PREFLIGHT_SCHEMA_VERSION = 2
PERMISSIONS_PROFILE_VERSION = 1
DENIED_READ_EXIT_CODE = 73
MCP_TOOLS = {
    "search_project_knowledge": (
        "search-project-knowledge",
        "已检索项目资料",
    ),
    "query_hvac_database": ("query_hvac_database", "已完成受控数据查询"),
    "inspect_hvac_snapshot": ("inspect_hvac_snapshot", "已完成运行快照检查"),
    "inspect_configuration_history": (
        "inspect_configuration_history",
        "已完成配置历史核对",
    ),
    "inspect_configuration_change_effect": (
        "inspect_configuration_change_effect",
        "已完成配置变更效果核对",
    ),
    "inspect_metric_extreme": ("inspect_metric_extreme", "已完成指标极值检查"),
    "schema": ("schema", "数据字段检查"),
    "data_quality": ("data-quality", "数据质量检查"),
    "cop_ranking": ("cop-ranking", "能效排名"),
}
VIRTUAL_DATA_LOCATIONS = {
    "telemetry.csv": "datasets/telemetry.csv",
    "config_history.csv": "datasets/config_history.csv",
    "assets.csv": "datasets/assets.csv",
    "point_aliases.csv": "configuration/point-dictionary.csv",
}
_NUMBER_TOKEN = re.compile(r"(?<![\w.-])-?\d+(?:,\d{3})*(?:\.\d+)?")


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
    enforce_windows_acl: bool = False


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


def _codex_sandbox_group_sid() -> str:
    if os.name != "nt":
        raise CodexRuntimeError(
            "Private runtime ACL enforcement requires native Windows"
        )
    try:
        import win32security

        sid, _, _ = win32security.LookupAccountName(None, "CodexSandboxUsers")
        return str(win32security.ConvertSidToStringSid(sid))
    except Exception as exc:
        raise CodexRuntimeError("Codex elevated sandbox group is unavailable") from exc


def protect_private_runtime_paths(
    paths: list[Path],
    *,
    runner: Any = subprocess.run,
    sid_lookup: Any = _codex_sandbox_group_sid,
) -> None:
    sid = str(sid_lookup()).strip()
    if not sid:
        raise CodexRuntimeError("Codex elevated sandbox group is unavailable")
    for path in paths:
        resolved = path.resolve()
        if not resolved.exists():
            raise CodexRuntimeError("Private runtime ACL target is unavailable")
        completed = runner(
            [
                "icacls.exe",
                str(resolved),
                "/deny",
                f"*{sid}:(OI)(CI)(R)",
                "/T",
                "/Q",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            raise CodexRuntimeError("Could not enforce private runtime ACL")


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
            raise CodexRuntimeError(
                "Codex runtime storage must be outside the repository"
            )
        source_docs = self.corpus_root / "docs" / "source"
        source_database = self.corpus_root / "datasets" / "hvac_bakeoff.duckdb"
        source_manifest = self.corpus_root / "manifest.json"
        if (
            not source_docs.is_dir()
            or not source_database.is_file()
            or not source_manifest.is_file()
        ):
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
        toolbox_database = evidence_root / "datasets" / "hvac_bakeoff.duckdb"
        toolbox_database.parent.mkdir()
        try:
            os.link(database_path, toolbox_database)
        except OSError:
            shutil.copy2(database_path, toolbox_database)
        shutil.copy2(source_manifest, evidence_root / "manifest.json")
        shutil.copytree(source_docs, evidence_root / "docs" / "source")
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
        source_files["manifest.json"] = evidence_root / "manifest.json"
        for virtual_filename in VIRTUAL_DATA_LOCATIONS:
            source_files[virtual_filename] = None

        output_schema = codex_home / "answer.schema.json"
        output_schema.write_text(
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "answer_markdown",
                        "tables",
                        "charts",
                        "citations",
                    ],
                    "properties": {
                        "answer_markdown": {"type": "string", "minLength": 1},
                        "tables": {
                            "type": "array",
                            "maxItems": 4,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["title", "columns", "rows"],
                                "properties": {
                                    "title": {"type": "string", "maxLength": 100},
                                    "columns": {
                                        "type": "array",
                                        "minItems": 1,
                                        "maxItems": 12,
                                        "items": {
                                            "type": "string",
                                            "maxLength": 80,
                                        },
                                    },
                                    "rows": {
                                        "type": "array",
                                        "maxItems": 100,
                                        "items": {
                                            "type": "array",
                                            "maxItems": 12,
                                            "items": {
                                                "type": [
                                                    "string",
                                                    "number",
                                                    "boolean",
                                                    "null",
                                                ]
                                            },
                                        },
                                    },
                                },
                            },
                        },
                        "charts": {
                            "type": "array",
                            "maxItems": 4,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "kind",
                                    "title",
                                    "unit",
                                    "points",
                                ],
                                "properties": {
                                    "kind": {"enum": ["line", "bar"]},
                                    "title": {"type": "string", "maxLength": 100},
                                    "unit": {"type": "string", "maxLength": 30},
                                    "points": {
                                        "type": "array",
                                        "minItems": 1,
                                        "maxItems": 200,
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["label", "value"],
                                            "properties": {
                                                "label": {
                                                    "type": "string",
                                                    "maxLength": 100,
                                                },
                                                "value": {"type": "number"},
                                                "series": {
                                                    "type": "string",
                                                    "maxLength": 80,
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                        },
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
        if self.settings.enforce_windows_acl:
            protect_private_runtime_paths([evidence_root])
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
                (
                    'exclude = ["CODEX_API_KEY", "*KEY*", "*TOKEN*", "*SECRET*", '
                    '"PROJECT_COPILOT_MCP_DATABASE", "PROJECT_COPILOT_MCP_CORPUS"]'
                ),
                "",
                "[permissions.project-copilot.filesystem]",
                '":minimal" = "read"',
                '":tmpdir" = "write"',
                f'{_toml_string(workspace)} = "read"',
                f'{_toml_string(database_path.parent)} = "deny"',
                f'{_toml_string(codex_home)} = "deny"',
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
                (
                    'enabled_tools = ["schema", "data_quality", "cop_ranking", '
                    '"search_project_knowledge", "query_hvac_database", '
                    '"inspect_hvac_snapshot", '
                    '"inspect_configuration_history", '
                    '"inspect_configuration_change_effect", '
                    '"inspect_metric_extreme"]'
                ),
                'default_tools_approval_mode = "auto"',
                "",
                f"[mcp_servers.{MCP_SERVER_NAME}.env]",
                f"PROJECT_COPILOT_MCP_DATABASE = {_toml_string(database_path)}",
                (f"PROJECT_COPILOT_MCP_CORPUS = {_toml_string(database_path.parent)}"),
                'POLARS_SKIP_CPU_CHECK = "1"',
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


def _denied_read_probe_script(path: Path) -> str:
    return (
        "$ErrorActionPreference = 'Stop'; "
        "try { "
        f"$stream = [System.IO.File]::OpenRead({_powershell_literal(path)}); "
        "$null = $stream.ReadByte(); $stream.Dispose(); exit 0 "
        "} catch { "
        "$errorType = $_.Exception.GetType().FullName; "
        "$innerType = if ($_.Exception.InnerException) { "
        "$_.Exception.InnerException.GetType().FullName } else { '' }; "
        "if ($errorType -match 'UnauthorizedAccess|SecurityException' -or "
        "$innerType -match 'UnauthorizedAccess|SecurityException') { "
        f"exit {DENIED_READ_EXIT_CODE} }}; exit 74 }}"
    )


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
    denied_script = _denied_read_probe_script(denied_path)
    application_source = PACKAGE_DIR / "web.py"
    application_source_script = _denied_read_probe_script(application_source)
    env = _controlled_environment()
    env["CODEX_HOME"] = str(prepared.codex_home)
    run_kwargs = {
        "cwd": prepared.workspace_root,
        "env": env,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 120,
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
        source_denied = runner(
            _sandbox_probe_command(
                settings,
                prepared,
                application_source_script,
            ),
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
    if source_denied.returncode == 0:
        raise CodexRuntimeError(
            "Codex elevated sandbox preflight failed to isolate application source"
        )
    if source_denied.returncode != DENIED_READ_EXIT_CODE:
        raise CodexRuntimeError(
            "Codex elevated sandbox preflight could not verify application source denial"
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
        or payload.get("permissions_profile_version") != PERMISSIONS_PROFILE_VERSION
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

    def parse(self, jsonl: str, *, question: str = "") -> dict[str, Any]:
        answer_text = ""
        completed = False
        activities: list[dict[str, str]] = []
        tool_tables: list[dict[str, Any]] = []
        tool_charts: list[dict[str, Any]] = []
        tool_payloads: list[object] = []
        tool_citations: dict[str, dict[str, object]] = {}
        try:
            events = [json.loads(line) for line in jsonl.splitlines() if line.strip()]
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
            item_type = item.get("type")
            if item_type == "file_change":
                raise CodexRuntimeError("Codex attempted a forbidden file change")
            if item_type == "web_search":
                raise CodexRuntimeError("Codex attempted a forbidden web search")
            if item_type == "command_execution":
                raise CodexRuntimeError("Codex attempted a forbidden shell command")
            if item_type == "agent_message":
                answer_text = str(item.get("text", "")).strip()
                continue
            if item_type != "mcp_tool_call":
                continue
            server = str(item.get("server", ""))
            tool = str(item.get("tool", ""))
            if server != MCP_SERVER_NAME or tool not in MCP_TOOLS:
                raise CodexRuntimeError("Codex attempted an unapproved MCP tool")
            if item.get("status") != "completed" or item.get("error"):
                raise CodexRuntimeError("Governed data operation failed")
            result = item.get("result")
            structured_content = (
                result.get("structured_content") if isinstance(result, dict) else None
            )
            payload = None
            if isinstance(structured_content, dict):
                payload = (
                    structured_content.get("result")
                    if "result" in structured_content
                    else structured_content
                )
            if isinstance(payload, list):
                has_evidence = bool(payload)
                if has_evidence:
                    tool_payloads.append(payload)
                if tool in {"data_quality", "cop_ranking"}:
                    tool_citations["telemetry.csv"] = self._virtual_tool_citation(
                        "telemetry.csv",
                        "Read-only synthetic telemetry snapshot; calculations were "
                        "performed by a governed MCP tool.",
                    )
            elif isinstance(payload, dict):
                has_evidence = bool(payload)
                if has_evidence:
                    tool_payloads.append(payload)
                tool_tables.extend(self._validated_tables(payload.get("tables", [])))
                tool_charts.extend(self._validated_charts(payload.get("charts", [])))
                raw_citations = payload.get("citations", [])
                if not isinstance(raw_citations, list):
                    raise CodexRuntimeError("Governed data citations are invalid")
                for raw_citation in raw_citations:
                    canonical = self._canonical_tool_citation(raw_citation)
                    canonical_filename = str(canonical["filename"])
                    if (
                        self.prepared.source_files[canonical_filename] is not None
                        and tool != "search_project_knowledge"
                    ):
                        raise CodexRuntimeError(
                            "Codex document citation is not verified by the "
                            "knowledge tool"
                        )
                    tool_citations.setdefault(canonical_filename, canonical)
            else:
                has_evidence = False
            if not has_evidence:
                raise CodexRuntimeError(
                    "Governed data operation did not return evidence"
                )
            tool_id, summary = MCP_TOOLS[tool]
            if not any(activity["tool"] == tool_id for activity in activities):
                activities.append(
                    {"tool": tool_id, "status": "completed", "summary": summary}
                )
        if not completed or not answer_text:
            raise CodexRuntimeError("Codex Agent did not produce a verifiable answer")
        try:
            structured = json.loads(answer_text)
            answer = str(structured["answer_markdown"]).strip()
            model_tables = self._validated_tables(structured.get("tables", []))
            model_charts = self._validated_charts(structured.get("charts", []))
            tables = tool_tables or model_tables
            charts = tool_charts or model_charts
            requested_citations = structured["citations"]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise CodexRuntimeError(
                "Codex Agent did not produce a verifiable answer"
            ) from exc
        if not answer or not isinstance(requested_citations, list):
            raise CodexRuntimeError("Codex Agent did not produce a verifiable answer")
        citations = self._verified_citations(requested_citations, tool_citations)
        if not citations:
            raise CodexRuntimeError(
                "Codex Agent answer lacked verified project evidence"
            )
        self._verify_numeric_grounding(
            answer=answer,
            tables=model_tables if not tool_tables else [],
            charts=model_charts if not tool_charts else [],
            citations=citations,
            tool_payloads=tool_payloads,
            question=question,
        )
        has_documents = any(
            str(item["filename"]) not in VIRTUAL_DATA_LOCATIONS for item in citations
        )
        mode = (
            "combined"
            if activities and has_documents
            else "data"
            if activities
            else "knowledge"
        )
        return {
            "mode": mode,
            "demo_mode": False,
            "model_backed": True,
            "answer_markdown": answer,
            "tables": tables,
            "charts": charts,
            "citations": citations,
            "activities": activities,
            "clarification": False,
            "refused": False,
            "grounding_status": "grounded",
        }

    def _canonical_tool_citation(self, raw: object) -> dict[str, object]:
        if not isinstance(raw, dict):
            raise CodexRuntimeError("Governed data citations are invalid")
        filename = str(raw.get("filename", "")).strip()
        excerpt = str(raw.get("excerpt", "")).strip()
        if not filename:
            raise CodexRuntimeError("Governed data citations are invalid")
        source = self.prepared.source_files.get(filename)
        if source is None:
            if filename not in VIRTUAL_DATA_LOCATIONS:
                raise CodexRuntimeError("Governed data citation filename is invalid")
            return self._virtual_tool_citation(filename, excerpt)
        content = source.read_text(encoding="utf-8", errors="strict")
        excerpt = excerpt[:500]
        if not excerpt or excerpt not in content:
            raise CodexRuntimeError("Governed document citation is invalid")
        return {
            "filename": filename,
            "excerpt": excerpt,
            "location": self._document_location(source),
            "source_status": str(raw.get("source_status", "只读证据"))[:80],
            "source_role": str(raw.get("source_role", source.parent.name))[:80],
            "support_share_pct": 0,
        }

    @staticmethod
    def _virtual_tool_citation(filename: str, excerpt: str) -> dict[str, object]:
        return {
            "filename": filename,
            "excerpt": excerpt[:500]
            or "Read-only governed project data returned by the MCP service.",
            "location": VIRTUAL_DATA_LOCATIONS[filename],
            "source_status": "只读证据",
            "source_role": "dataset",
            "support_share_pct": 0,
        }

    def _document_location(self, source: Path) -> str:
        documents_root = self.prepared.workspace_root / "docs" / "source"
        try:
            return source.relative_to(documents_root).as_posix()
        except ValueError:
            if source.name == "manifest.json":
                return "manifest.json"
            raise CodexRuntimeError("Codex citation path is outside approved evidence")

    @staticmethod
    def _numbers(value: object) -> list[float]:
        if isinstance(value, bool) or value is None:
            return []
        if isinstance(value, (int, float)):
            number = float(value)
            return [number] if math.isfinite(number) else []
        if isinstance(value, str):
            return [
                float(match.group(0).replace(",", ""))
                for match in _NUMBER_TOKEN.finditer(value)
            ]
        if isinstance(value, dict):
            return [
                number
                for item in value.values()
                for number in CodexTurnParser._numbers(item)
            ]
        if isinstance(value, (list, tuple)):
            return [
                number for item in value for number in CodexTurnParser._numbers(item)
            ]
        return []

    @staticmethod
    def _is_explicit_numeric_rejection(text: str, match: re.Match[str]) -> bool:
        boundaries = ("\n", ".", "!", "?", ";", "。", "！", "？", "；")
        start = max(text.rfind(boundary, 0, match.start()) for boundary in boundaries)
        ends = [
            position
            for boundary in boundaries
            if (position := text.find(boundary, match.end())) >= 0
        ]
        end = min(ends) if ends else len(text)
        clause = text[start + 1 : end].casefold()
        clause = re.sub(r"^[\s#>*+\-\d.)、]+", "", clause).strip()
        if any(
            reversal in clause
            for reversal in (
                " but ",
                " however",
                " actually",
                " is correct",
                " is supported",
                " is the factual",
                "但是",
                "不过",
                "其实",
                "事实上",
                "已确认",
                "是正确",
            )
        ):
            return False
        english = re.match(
            r"^(?:(?:i|we)\s+)?(?:(?:cannot|can't|can not|do not|don't|"
            r"will not|won't)\s+(?:verify|confirm|support|accept|use|treat|"
            r"set|change)\b|(?:refuse|decline)\b|reject(?:ed|ing)?\b)",
            clause,
        )
        chinese = re.match(
            r"^(?:我(?:们)?\s*)?(?:拒绝|无法确认|不能确认|不采纳|不接受|"
            r"不会把|不能把|不会将|不能将)",
            clause,
        )
        return bool(english or chinese)

    @staticmethod
    def _claim_numbers(text: str) -> list[float]:
        claims: list[float] = []
        for match in _NUMBER_TOKEN.finditer(text):
            if CodexTurnParser._is_explicit_numeric_rejection(text, match):
                continue
            token = match.group(0).replace(",", "")
            value = float(token)
            suffix = text[match.end() : match.end() + 8].casefold()
            has_unit = bool(
                re.match(
                    r"\s*(?:%|°|℃|c\b|k\b|kw\b|kwh\b|hz\b|kpa\b|s\b|min\b|h\b)",
                    suffix,
                )
            )
            if "." in token or abs(value) >= 10 or has_unit:
                claims.append(value)
        return claims

    @staticmethod
    def _supported_values(values: list[float]) -> list[float]:
        finite = list(dict.fromkeys(value for value in values if math.isfinite(value)))
        bounded = finite[:200]
        supported = list(bounded)
        for value in bounded:
            supported.extend(
                (value / 60.0, value / 3600.0, value * 10.0, value * 100.0)
            )
        for index, left in enumerate(bounded):
            for right in bounded[index + 1 :]:
                supported.extend((abs(left - right), left + right))
                if right:
                    supported.append(left / right)
                if left:
                    supported.append(right / left)
                    supported.append(abs(right - left) / abs(left) * 100.0)
        return supported

    def _verify_numeric_grounding(
        self,
        *,
        answer: str,
        tables: list[dict[str, Any]],
        charts: list[dict[str, Any]],
        citations: list[dict[str, object]],
        tool_payloads: list[object],
        question: str,
    ) -> None:
        # Numbers in the user's question are requests, not proof. Only governed
        # tool output and verified source excerpts may ground factual numbers.
        evidence_values: list[float] = []
        for payload in tool_payloads:
            evidence_values.extend(self._numbers(payload))
        for citation in citations:
            evidence_values.extend(self._numbers(citation.get("excerpt", "")))
        answer_claims = self._claim_numbers(answer)
        presentation_claims = self._numbers(tables) + self._numbers(charts)
        if not answer_claims and not presentation_claims:
            return
        supported = self._supported_values(evidence_values)
        unsupported_presentations = [
            claim
            for claim in presentation_claims
            if not any(
                abs(claim - evidence) <= max(0.01, abs(evidence) * 0.005)
                for evidence in evidence_values
            )
        ]
        unsupported_answers = [
            claim
            for claim in answer_claims
            if not any(
                abs(claim - evidence) <= max(0.01, abs(evidence) * 0.005)
                for evidence in supported
            )
        ]
        if unsupported_presentations or unsupported_answers:
            raise CodexRuntimeError(
                "Codex answer contains unsupported numeric evidence"
            )

    @staticmethod
    def _validated_tables(raw_tables: object) -> list[dict[str, Any]]:
        if not isinstance(raw_tables, list) or len(raw_tables) > 4:
            raise CodexRuntimeError("Codex table payload is invalid")
        tables: list[dict[str, Any]] = []
        for raw_table in raw_tables:
            if not isinstance(raw_table, dict):
                raise CodexRuntimeError("Codex table payload is invalid")
            title = raw_table.get("title")
            columns = raw_table.get("columns")
            rows = raw_table.get("rows")
            if (
                not isinstance(title, str)
                or len(title) > 100
                or not isinstance(columns, list)
                or not 1 <= len(columns) <= 12
                or not all(
                    isinstance(column, str) and 0 < len(column) <= 80
                    for column in columns
                )
                or not isinstance(rows, list)
                or len(rows) > 100
            ):
                raise CodexRuntimeError("Codex table payload is invalid")
            normalized_rows: list[list[object]] = []
            for row in rows:
                if not isinstance(row, list) or len(row) != len(columns):
                    raise CodexRuntimeError("Codex table payload is invalid")
                normalized_row: list[object] = []
                for cell in row:
                    if cell is None or isinstance(cell, (str, bool, int)):
                        if isinstance(cell, str) and len(cell) > 500:
                            raise CodexRuntimeError("Codex table payload is invalid")
                        normalized_row.append(cell)
                    elif isinstance(cell, float) and math.isfinite(cell):
                        normalized_row.append(cell)
                    else:
                        raise CodexRuntimeError("Codex table payload is invalid")
                normalized_rows.append(normalized_row)
            tables.append(
                {"title": title, "columns": list(columns), "rows": normalized_rows}
            )
        return tables

    @staticmethod
    def _validated_charts(raw_charts: object) -> list[dict[str, Any]]:
        if not isinstance(raw_charts, list) or len(raw_charts) > 4:
            raise CodexRuntimeError("Codex chart payload is invalid")
        charts: list[dict[str, Any]] = []
        for raw_chart in raw_charts:
            if not isinstance(raw_chart, dict):
                raise CodexRuntimeError("Codex chart payload is invalid")
            kind = raw_chart.get("kind")
            title = raw_chart.get("title")
            unit = raw_chart.get("unit")
            points = raw_chart.get("points")
            if (
                kind not in {"line", "bar"}
                or not isinstance(title, str)
                or len(title) > 100
                or not isinstance(unit, str)
                or len(unit) > 30
                or not isinstance(points, list)
                or not 1 <= len(points) <= 200
            ):
                raise CodexRuntimeError("Codex chart payload is invalid")
            normalized_points: list[dict[str, object]] = []
            for point in points:
                if not isinstance(point, dict):
                    raise CodexRuntimeError("Codex chart payload is invalid")
                label = point.get("label")
                value = point.get("value")
                series = point.get("series")
                if (
                    not isinstance(label, str)
                    or not 0 < len(label) <= 100
                    or isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    or (series is not None and not isinstance(series, str))
                    or (isinstance(series, str) and len(series) > 80)
                ):
                    raise CodexRuntimeError("Codex chart payload is invalid")
                normalized = {"label": label, "value": value}
                if series is not None:
                    normalized["series"] = series
                normalized_points.append(normalized)
            charts.append(
                {
                    "kind": kind,
                    "title": title,
                    "unit": unit,
                    "points": normalized_points,
                }
            )
        return charts

    def _verified_citations(
        self,
        requested: list[object],
        tool_citations: dict[str, dict[str, object]],
    ) -> list[dict[str, object]]:
        citations: list[dict[str, object]] = []
        seen: set[str] = set()
        for raw in requested:
            if not isinstance(raw, dict):
                raise CodexRuntimeError("Codex citation payload is invalid")
            filename = str(raw.get("filename", "")).strip()
            if (
                not filename
                or filename in seen
                or filename not in self.prepared.source_files
            ):
                raise CodexRuntimeError("Codex citation filename is not verified")
            source = self.prepared.source_files[filename]
            canonical = tool_citations.get(filename)
            if canonical is not None:
                citations.append(dict(canonical))
                seen.add(filename)
                continue
            if source is None:
                raise CodexRuntimeError("Codex data citation is not verified")
            raise CodexRuntimeError(
                "Codex document citation is not verified by the knowledge tool"
            )
        for filename, canonical in tool_citations.items():
            if filename in seen:
                continue
            citations.append(dict(canonical))
            seen.add(filename)
        return citations


class CodexProcessRunner:
    def run(self, launch: CodexLaunch, prompt: str, timeout_seconds: int) -> str:
        launch.prepared.events_log.parent.mkdir(parents=True, exist_ok=True)
        try:
            with (
                launch.prepared.events_log.open("w", encoding="utf-8") as stdout,
                launch.prepared.stderr_log.open("w", encoding="utf-8") as stderr,
            ):
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


class CodexPythonSdkRunner:
    def __init__(
        self,
        settings: CodexRuntimeSettings,
        *,
        process_runner: Any = subprocess.run,
    ) -> None:
        self.settings = settings
        self.process_runner = process_runner

    def run(self, launch: CodexLaunch, prompt: str, timeout_seconds: int) -> str:
        prepared = launch.prepared
        prepared.events_log.parent.mkdir(parents=True, exist_ok=True)
        request = {
            "codex_bin": str(self.settings.codex_bin),
            "cwd": str(prepared.workspace_root),
            "prompt": prompt,
            "model": self.settings.model,
            "model_provider": "company",
            "effort": self.settings.reasoning_effort,
            "output_schema": json.loads(
                prepared.output_schema.read_text(encoding="utf-8")
            ),
        }
        argv = [
            str(self.settings.python_executable),
            "-m",
            "project_copilot.codex_sdk_worker",
        ]
        try:
            completed = self.process_runner(
                argv,
                input=json.dumps(request, ensure_ascii=False),
                cwd=prepared.workspace_root,
                env=launch.env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodexRuntimeError(
                "Codex Agent exceeded the configured time budget"
            ) from exc
        stdout = str(completed.stdout or "")
        stderr = str(completed.stderr or "")
        prepared.events_log.write_text(stdout, encoding="utf-8")
        prepared.stderr_log.write_text(stderr, encoding="utf-8")
        if completed.returncode != 0:
            raise CodexRuntimeError(
                f"Codex Agent exited with code {completed.returncode}; see private runtime log"
            )
        return stdout


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
            sum(
                1
                for path in (self.corpus_root / "docs" / "source").rglob("*")
                if path.is_file()
            )
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
            raise CodexRuntimeError("Codex mode requires PROJECT_COPILOT_CODEX_BIN")
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
            enforce_windows_acl=True,
        )
        validate_elevated_sandbox_preflight(settings)
        transport = (
            os.environ.get("PROJECT_COPILOT_CODEX_TRANSPORT", "python-sdk")
            .strip()
            .casefold()
        )
        if transport == "python-sdk":
            runner: Any = CodexPythonSdkRunner(settings)
        elif transport == "cli-jsonl":
            runner = CodexProcessRunner()
        else:
            raise CodexRuntimeError(
                "Unknown Codex transport; expected python-sdk or cli-jsonl"
            )
        return cls(settings, corpus_root, runner=runner)

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
            return CodexTurnParser(prepared).parse(jsonl, question=question)
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
                    "conclusion. Do not run Shell, PowerShell, Python, file-change, or Web-search "
                    "operations; use search_project_knowledge for all document evidence. "
                    "Put comparison rows in tables and chart-ready numeric series in "
                    "charts only when they materially improve the answer; otherwise return empty "
                    "arrays. Every displayed value must come from inspected project evidence or a "
                    "governed data-tool result. Every citation "
                    "must use an exact human filename and an exact excerpt copied from that source. "
                    "For telemetry.csv use an empty excerpt; it is accepted only after a governed MCP call."
                ),
            ]
            if part
        )
