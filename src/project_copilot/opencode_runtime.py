from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from typing import Any
from urllib.parse import urlparse

from project_copilot.codex_runtime import (
    MCP_TOOLS,
    PACKAGE_DIR,
    REPOSITORY_ROOT,
    CodexRuntime,
    CodexRuntimeError,
    CodexRuntimeSettings,
    CodexTurnParser,
    CodexWorkspaceBuilder,
    PreparedCodexWorkspace,
    _controlled_environment,
)


WORKER_PATH = PACKAGE_DIR / "opencode_sdk_worker.mjs"
AGENT_ID = "project-copilot"


class OpenCodeRuntimeError(CodexRuntimeError):
    """A public-safe OpenCode failure that never triggers a fallback."""


@dataclass(frozen=True)
class OpenCodeRuntimeSettings:
    opencode_bin: Path
    node_bin: Path
    sdk_entrypoint: Path
    runtime_root: Path
    base_url: str
    api_key: str = field(repr=False)
    model: str
    wire_api: str = "responses"
    output_mode: str = "text_json"
    python_executable: Path = field(default_factory=lambda: Path(sys.executable))
    reasoning_effort: str = "xhigh"
    timeout_seconds: int = 360
    max_steps: int = 10
    enforce_windows_acl: bool = False

    def __post_init__(self) -> None:
        if self.wire_api not in {"responses", "chat_completions"}:
            raise ValueError("wire_api must be responses or chat_completions")
        if self.output_mode not in {"text_json", "native_schema"}:
            raise ValueError("output_mode must be text_json or native_schema")

    def codex_settings(self) -> CodexRuntimeSettings:
        return CodexRuntimeSettings(
            codex_bin=self.opencode_bin,
            runtime_root=self.runtime_root,
            base_url=self.base_url,
            api_key=self.api_key,
            model=self.model,
            python_executable=self.python_executable,
            reasoning_effort=self.reasoning_effort,
            timeout_seconds=self.timeout_seconds,
            enforce_windows_acl=self.enforce_windows_acl,
        )


@dataclass(frozen=True)
class OpenCodeLaunch:
    argv: list[str]
    env: dict[str, str] = field(repr=False)
    request: dict[str, Any]
    prepared: PreparedCodexWorkspace


def _mcp_tool_policy(value: bool) -> dict[str, bool]:
    policy = {"*": not value, "hvac_*": value}
    policy.update({f"hvac_{tool}": value for tool in MCP_TOOLS})
    return policy


def _mcp_permission_policy() -> dict[str, str]:
    policy = {
        "*": "deny",
        "hvac_*": "allow",
        "edit": "deny",
        "bash": "deny",
        "read": "deny",
        "glob": "deny",
        "grep": "deny",
        "list": "deny",
        "task": "deny",
        "webfetch": "deny",
        "websearch": "deny",
        "external_directory": "deny",
        "doom_loop": "deny",
        "question": "deny",
    }
    policy.update({f"hvac_{tool}": "allow" for tool in MCP_TOOLS})
    return policy


def build_opencode_config(
    settings: OpenCodeRuntimeSettings,
    prepared: PreparedCodexWorkspace,
) -> dict[str, Any]:
    provider_id = "openai" if settings.wire_api == "responses" else "company"
    provider_npm = (
        "@ai-sdk/openai"
        if settings.wire_api == "responses"
        else "@ai-sdk/openai-compatible"
    )
    python_paths = [str(REPOSITORY_ROOT / "src")]
    for value in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if value and value not in python_paths:
            python_paths.append(value)
        candidate = Path(value)
        if candidate.name.casefold() == "site-packages":
            for relative in ("pywin32_system32", "win32", "win32/lib"):
                child = candidate / relative
                if child.is_dir() and str(child) not in python_paths:
                    python_paths.append(str(child))
    mcp_environment = {
        "PROJECT_COPILOT_MCP_DATABASE": str(prepared.database_path),
        "PROJECT_COPILOT_MCP_CORPUS": str(prepared.database_path.parent),
        "POLARS_SKIP_CPU_CHECK": "1",
        "PYTHONPATH": os.pathsep.join(python_paths),
    }
    permissions = _mcp_permission_policy()
    tools = _mcp_tool_policy(True)
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": f"{provider_id}/{settings.model}",
        "small_model": f"{provider_id}/{settings.model}",
        "enabled_providers": [provider_id],
        "provider": {
            provider_id: {
                "npm": provider_npm,
                "name": "Approved company model service",
                "options": {
                    "baseURL": settings.base_url.rstrip("/"),
                    "apiKey": "{env:CODEX_API_KEY}",
                    "timeout": settings.timeout_seconds * 1000,
                },
                "models": {
                    settings.model: {
                        "name": settings.model,
                        "reasoning": True,
                        "tool_call": True,
                    }
                },
            }
        },
        "mcp": {
            "hvac": {
                "type": "local",
                "command": [
                    str(settings.python_executable),
                    "-m",
                    "project_copilot.codex_mcp_server",
                ],
                "environment": mcp_environment,
                "enabled": True,
                "timeout": 60_000,
            }
        },
        "agent": {
            AGENT_ID: {
                "description": "Read-only project knowledge and HVAC data analyst",
                "mode": "primary",
                "model": f"{provider_id}/{settings.model}",
                "prompt": (
                    "Use only the approved hvac MCP tools for evidence. Do not use "
                    "built-in filesystem search, file access, execution, browsing, "
                    "delegation, or external paths. Never repeat an evidence search "
                    "with the same intent. Synthesize as soon as the available evidence "
                    "supports an answer; otherwise state that evidence is insufficient. "
                    "When evidence is sufficient, stop calling tools and return the "
                    "required JSON answer immediately. Lead with the direct "
                    "conclusion and preserve exact human filenames."
                ),
                "steps": settings.max_steps,
                "reasoningEffort": settings.reasoning_effort,
                "textVerbosity": "medium",
                "permission": permissions,
                "tools": tools,
            }
        },
        "permission": permissions,
        "tools": tools,
        "instructions": ["AGENTS.md"],
        "plugin": [],
        "formatter": False,
        "lsp": False,
        "snapshot": False,
        "share": "disabled",
        "autoupdate": False,
        "logLevel": "ERROR",
        "experimental": {
            "chatMaxRetries": 1,
            "batch_tool": False,
            "openTelemetry": False,
        },
    }


def build_opencode_launch(
    settings: OpenCodeRuntimeSettings,
    prepared: PreparedCodexWorkspace,
) -> OpenCodeLaunch:
    provider_id = "openai" if settings.wire_api == "responses" else "company"
    env = _controlled_environment()
    env["CODEX_API_KEY"] = settings.api_key
    env["HOME"] = str(prepared.codex_home)
    env["USERPROFILE"] = str(prepared.codex_home)
    env["XDG_DATA_HOME"] = str(prepared.codex_home / "data")
    env["XDG_CONFIG_HOME"] = str(prepared.codex_home / "config")
    env["XDG_CACHE_HOME"] = str(prepared.codex_home / "cache")
    env["OPENCODE_CONFIG_DIR"] = str(prepared.codex_home / "config")
    env["OPENCODE_EXPERIMENTAL_NATIVE_LLM"] = (
        "true" if settings.wire_api == "responses" else "false"
    )
    env["OPENCODE_PURE"] = "true"
    env["OPENCODE_DISABLE_DEFAULT_PLUGINS"] = "true"
    env["OPENCODE_DISABLE_EXTERNAL_SKILLS"] = "true"
    env["OPENCODE_DISABLE_LSP_DOWNLOAD"] = "true"
    env["NO_COLOR"] = "1"
    env["PATH"] = os.pathsep.join(
        [str(settings.opencode_bin.parent), env.get("PATH", "")]
    ).rstrip(os.pathsep)
    request = {
        "sdk_entrypoint": str(settings.sdk_entrypoint),
        "cwd": str(prepared.workspace_root),
        "model": settings.model,
        "provider_id": provider_id,
        "wire_api": settings.wire_api,
        "agent": AGENT_ID,
        "config": build_opencode_config(settings, prepared),
        "output_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["answer_markdown", "tables", "charts", "citations"],
            "properties": {
                "answer_markdown": {"type": "string"},
                "tables": {"type": "array", "items": {"type": "object"}},
                "charts": {"type": "array", "items": {"type": "object"}},
                "citations": {"type": "array", "items": {"type": "object"}},
            },
        },
        "startup_timeout_ms": 60_000,
        "max_steps": settings.max_steps,
        "output_mode": settings.output_mode,
    }
    return OpenCodeLaunch(
        argv=[str(settings.node_bin), str(WORKER_PATH)],
        env=env,
        request=request,
        prepared=prepared,
    )


class OpenCodeTurnAdapter:
    def to_codex_jsonl(self, payload: object) -> str:
        if not isinstance(payload, dict) or payload.get("error"):
            raise OpenCodeRuntimeError(
                "OpenCode Agent did not produce a verifiable answer"
            )
        prompt_result = payload.get("prompt_result")
        messages = payload.get("messages", [])
        if not isinstance(prompt_result, dict) or not isinstance(messages, list):
            raise OpenCodeRuntimeError(
                "OpenCode Agent did not produce a verifiable answer"
            )

        events: list[dict[str, Any]] = []
        seen_calls: set[str] = set()
        records = list(messages)
        if not records:
            records = [prompt_result]
        for record in records:
            if not isinstance(record, dict):
                continue
            info = record.get("info", {})
            if isinstance(info, dict) and info.get("error"):
                error = info["error"]
                if not (
                    isinstance(error, dict)
                    and error.get("name") == "StructuredOutputError"
                ):
                    raise OpenCodeRuntimeError("OpenCode model request failed")
            parts = record.get("parts", [])
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict) or part.get("type") != "tool":
                    continue
                call_id = str(part.get("callID") or part.get("id") or "")
                if call_id and call_id in seen_calls:
                    continue
                if call_id:
                    seen_calls.add(call_id)
                tool = self._tool_name(part.get("tool"))
                state = part.get("state")
                if not isinstance(state, dict) or state.get("status") != "completed":
                    raise OpenCodeRuntimeError("Governed data operation failed")
                structured = self._structured_tool_result(state)
                events.append(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": call_id,
                            "type": "mcp_tool_call",
                            "server": "hvac",
                            "tool": tool,
                            "arguments": state.get("input", {}),
                            "result": {"structured_content": structured},
                            "status": "completed",
                        },
                    }
                )

        structured_answer = self._structured_answer(prompt_result, messages)
        events.extend(
            [
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": json.dumps(structured_answer, ensure_ascii=False),
                    },
                },
                {"type": "turn.completed", "usage": {}},
            ]
        )
        return "".join(
            json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            for event in events
        )

    @staticmethod
    def _tool_name(raw: object) -> str:
        name = str(raw or "").strip()
        for prefix in ("hvac_", "hvac.", "hvac/"):
            if name.startswith(prefix):
                name = name[len(prefix) :]
                break
        if name not in MCP_TOOLS:
            raise OpenCodeRuntimeError("OpenCode attempted an unapproved tool")
        return name

    @classmethod
    def _structured_tool_result(cls, state: dict[str, Any]) -> dict[str, Any]:
        metadata = state.get("metadata")
        if isinstance(metadata, dict):
            for key in ("structuredContent", "structured_content"):
                candidate = metadata.get(key)
                if isinstance(candidate, dict):
                    return cls._normalize_tool_payload(candidate)
            nested = metadata.get("mcp")
            if isinstance(nested, dict):
                for key in ("structuredContent", "structured_content"):
                    candidate = nested.get(key)
                    if isinstance(candidate, dict):
                        return cls._normalize_tool_payload(candidate)
        output = state.get("output")
        parsed = cls._parse_json_value(output)
        if isinstance(parsed, dict):
            for key in ("structuredContent", "structured_content"):
                candidate = parsed.get(key)
                if isinstance(candidate, dict):
                    return cls._normalize_tool_payload(candidate)
            content = parsed.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        nested = cls._parse_json_value(item.get("text"))
                        if isinstance(nested, dict):
                            return {"result": cls._normalize_tool_payload(nested)}
                        if isinstance(nested, list):
                            return {"result": nested}
            return {"result": cls._normalize_tool_payload(parsed)}
        if isinstance(parsed, list):
            return {"result": parsed}
        if isinstance(output, str) and output.strip():
            return {"result": {"raw_output": output}}
        raise OpenCodeRuntimeError("Governed data operation did not return evidence")

    @staticmethod
    def _normalize_tool_payload(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        validators = {
            "tables": CodexTurnParser._validated_tables,
            "charts": CodexTurnParser._validated_charts,
        }
        for key, validator in validators.items():
            if key not in normalized:
                continue
            try:
                normalized[key] = validator(normalized[key])
            except CodexRuntimeError:
                if key == "tables" and isinstance(normalized[key], list):
                    normalized["grounding_tables"] = normalized[key]
                normalized[key] = []
        return normalized

    @staticmethod
    def _parse_json_value(raw: object) -> object:
        if isinstance(raw, (dict, list)):
            return raw
        if not isinstance(raw, str):
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    @classmethod
    def _structured_answer(
        cls,
        prompt_result: dict[str, Any],
        messages: list[object],
    ) -> dict[str, Any]:
        candidates: list[object] = [prompt_result]
        candidates.extend(reversed(messages))
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            info = candidate.get("info")
            if isinstance(info, dict):
                for key in ("structured", "structured_output"):
                    structured = info.get(key)
                    if isinstance(structured, dict):
                        return structured
            parts = candidate.get("parts", [])
            if not isinstance(parts, list):
                continue
            for part in reversed(parts):
                if not isinstance(part, dict) or part.get("type") != "text":
                    continue
                structured = cls._parse_json_value(part.get("text"))
                if isinstance(structured, dict):
                    return structured
        raise OpenCodeRuntimeError("OpenCode Agent did not produce a verifiable answer")


class OpenCodeSdkRunner:
    def __init__(
        self,
        settings: OpenCodeRuntimeSettings,
        *,
        process_runner: Any | None = None,
    ) -> None:
        self.settings = settings
        self.process_runner = process_runner

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            try:
                cleanup = subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=15,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                process.kill()
                process.wait(timeout=5)
                raise OpenCodeRuntimeError(
                    "OpenCode process-tree cleanup failed"
                ) from exc
            if cleanup.returncode != 0:
                process.kill()
                process.wait(timeout=5)
                raise OpenCodeRuntimeError("OpenCode process-tree cleanup failed")
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.kill()
            process.wait(timeout=5)
        if process.poll() is None:
            raise OpenCodeRuntimeError("OpenCode process-tree cleanup failed")

    def _run_process(
        self,
        launch: OpenCodeLaunch,
        request_json: str,
        timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            launch.argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=launch.prepared.workspace_root,
            env=launch.env,
            text=True,
            encoding="utf-8",
            errors="strict",
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            ),
            start_new_session=os.name != "nt",
        )
        try:
            stdout, stderr = process.communicate(
                input=request_json,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            self._terminate_process_tree(process)
            process.communicate()
            raise
        return subprocess.CompletedProcess(
            launch.argv,
            int(process.returncode or 0),
            stdout=stdout,
            stderr=stderr,
        )

    def run(
        self,
        launch: OpenCodeLaunch,
        prompt: str,
        timeout_seconds: int,
    ) -> str:
        request = dict(launch.request)
        request["prompt"] = prompt
        request["turn_timeout_ms"] = max(1_000, (timeout_seconds - 15) * 1_000)
        try:
            request_json = json.dumps(request, ensure_ascii=False)
            completed = (
                self.process_runner(
                    launch.argv,
                    input=request_json,
                    cwd=launch.prepared.workspace_root,
                    env=launch.env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="strict",
                    timeout=timeout_seconds,
                    check=False,
                )
                if self.process_runner is not None
                else self._run_process(launch, request_json, timeout_seconds)
            )
        except subprocess.TimeoutExpired as exc:
            raise OpenCodeRuntimeError(
                "OpenCode Agent exceeded the configured time budget"
            ) from exc
        stdout = str(completed.stdout or "")
        stderr = str(completed.stderr or "")
        launch.prepared.events_log.write_text(stdout, encoding="utf-8")
        launch.prepared.stderr_log.write_text(stderr, encoding="utf-8")
        if completed.returncode != 0:
            raise OpenCodeRuntimeError(
                f"OpenCode Agent exited with code {completed.returncode}; "
                "see private runtime log"
            )
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise OpenCodeRuntimeError(
                "OpenCode Agent did not produce a verifiable answer"
            ) from exc
        return OpenCodeTurnAdapter().to_codex_jsonl(payload)


class OpenCodeRuntime:
    WORKFLOWS = CodexRuntime.WORKFLOWS
    workspace_name = CodexRuntime.workspace_name

    def __init__(
        self,
        settings: OpenCodeRuntimeSettings,
        corpus_root: str | Path,
        *,
        runner: Any | None = None,
    ) -> None:
        self.settings = settings
        self.corpus_root = Path(corpus_root).resolve()
        self.builder = CodexWorkspaceBuilder(
            self.corpus_root,
            settings.codex_settings(),
        )
        self.runner = runner or OpenCodeSdkRunner(settings)
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
    ) -> OpenCodeRuntime:
        from project_copilot.company_api import load_codex_switch_settings

        provider = load_codex_switch_settings()
        required = {
            "opencode_bin": os.environ.get("PROJECT_COPILOT_OPENCODE_BIN", ""),
            "node_bin": os.environ.get("PROJECT_COPILOT_OPENCODE_NODE_BIN", ""),
            "sdk_entrypoint": os.environ.get("PROJECT_COPILOT_OPENCODE_SDK", ""),
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise OpenCodeRuntimeError(
                "OpenCode mode requires explicit pinned runtime paths: "
                + ", ".join(missing)
            )
        resolved = {name: Path(value).resolve() for name, value in required.items()}
        unavailable = [name for name, path in resolved.items() if not path.is_file()]
        if unavailable:
            raise OpenCodeRuntimeError(
                "OpenCode runtime dependency is unavailable: " + ", ".join(unavailable)
            )
        settings = OpenCodeRuntimeSettings(
            opencode_bin=resolved["opencode_bin"],
            node_bin=resolved["node_bin"],
            sdk_entrypoint=resolved["sdk_entrypoint"],
            runtime_root=Path(
                os.environ.get(
                    "PROJECT_COPILOT_OPENCODE_RUNTIME_ROOT",
                    str(Path(application_runtime) / "opencode-agent"),
                )
            ),
            base_url=provider.base_url,
            api_key=provider.api_key,
            model=provider.model,
            wire_api=os.environ.get("PROJECT_COPILOT_OPENCODE_WIRE_API", "responses"),
            output_mode=os.environ.get(
                "PROJECT_COPILOT_OPENCODE_OUTPUT_MODE", "text_json"
            ),
            python_executable=Path(sys.executable),
            reasoning_effort=os.environ.get(
                "PROJECT_COPILOT_OPENCODE_REASONING_EFFORT", "xhigh"
            ),
            timeout_seconds=int(
                os.environ.get("PROJECT_COPILOT_OPENCODE_TIMEOUT_SECONDS", "360")
            ),
            max_steps=int(os.environ.get("PROJECT_COPILOT_OPENCODE_MAX_STEPS", "10")),
            enforce_windows_acl=os.environ.get(
                "PROJECT_COPILOT_OPENCODE_ENFORCE_WINDOWS_ACL", "false"
            ).casefold()
            == "true",
        )
        return cls(settings, corpus_root)

    async def answer_async(
        self,
        question: str,
        *,
        history: list[dict[str, str]] | None = None,
        workflow_id: str | None = None,
    ) -> dict[str, Any]:
        prepared = self.builder.prepare()
        prompt = CodexRuntime._prompt(
            question,
            history or [],
            workflow_id=workflow_id,
        )
        launch = build_opencode_launch(self.settings, prepared)
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
            raise OpenCodeRuntimeError(
                "OpenCode Agent did not produce a verifiable answer"
            ) from exc
