from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest
from fastapi.testclient import TestClient

import project_copilot.opencode_runtime as opencode_module
from project_copilot.codex_runtime import CodexTurnParser, CodexWorkspaceBuilder
from project_copilot.opencode_runtime import (
    OpenCodeRuntime,
    OpenCodeRuntimeError,
    OpenCodeRuntimeSettings,
    OpenCodeSdkRunner,
    OpenCodeTurnAdapter,
    build_opencode_config,
    build_opencode_launch,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = REPOSITORY_ROOT / "examples" / "agentic_hvac_bakeoff"


def _settings(tmp_path: Path) -> OpenCodeRuntimeSettings:
    opencode_bin = tmp_path / "opencode.exe"
    node_bin = tmp_path / "node.exe"
    sdk_entrypoint = tmp_path / "sdk" / "v2" / "index.js"
    opencode_bin.write_bytes(b"opencode")
    node_bin.write_bytes(b"node")
    sdk_entrypoint.parent.mkdir(parents=True)
    sdk_entrypoint.write_text("export {};\n", encoding="utf-8")
    return OpenCodeRuntimeSettings(
        opencode_bin=opencode_bin,
        node_bin=node_bin,
        sdk_entrypoint=sdk_entrypoint,
        runtime_root=tmp_path / "runtime",
        base_url="https://approved.example/v1",
        api_key="top-secret",
        model="gpt-5.6-sol",
        python_executable=Path(sys.executable),
        reasoning_effort="xhigh",
        timeout_seconds=360,
        max_steps=10,
    )


def _answer() -> dict[str, object]:
    return {
        "answer_markdown": "HP-02 has 12 missing rows.",
        "citations": [
            {
                "filename": "telemetry.csv",
                "excerpt": "",
            }
        ],
        "tables": [],
        "charts": [],
    }


def test_runtime_dependency_contract_includes_duckdb_timezone_provider() -> None:
    project = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    dependencies = project["project"]["dependencies"]
    assert "tzdata==2026.3" in dependencies
    assert not any(dependency.startswith("pytz==") for dependency in dependencies)


def _raw_sdk_payload() -> dict[str, object]:
    result = [{"asset_id": "HP-02", "missing_rows": 12}]
    return {
        "prompt_result": {
            "info": {"role": "assistant", "structured": _answer()},
            "parts": [],
        },
        "messages": [
            {
                "info": {"role": "assistant"},
                "parts": [
                    {
                        "id": "tool-part",
                        "type": "tool",
                        "callID": "call-1",
                        "tool": "hvac_data_quality",
                        "state": {
                            "status": "completed",
                            "input": {},
                            "output": json.dumps(result),
                            "metadata": {"structuredContent": {"result": result}},
                        },
                    }
                ],
            }
        ],
    }


def test_opencode_config_uses_responses_provider_and_denies_non_mcp_tools(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, settings.codex_settings()).prepare()

    config = build_opencode_config(settings, prepared)

    provider = config["provider"]["openai"]
    assert provider["npm"] == "@ai-sdk/openai"
    assert provider["options"]["baseURL"] == "https://approved.example/v1"
    assert provider["options"]["apiKey"] == "{env:CODEX_API_KEY}"
    assert "top-secret" not in json.dumps(config)
    assert config["enabled_providers"] == ["openai"]
    assert config["share"] == "disabled"
    assert config["autoupdate"] is False
    assert config["snapshot"] is False
    assert config["tools"]["*"] is False
    assert config["tools"]["hvac_*"] is True
    agent = config["agent"]["project-copilot"]
    assert agent["model"] == "openai/gpt-5.6-sol"
    assert agent["reasoningEffort"] == "xhigh"
    assert agent["steps"] == 10
    assert "Never repeat an evidence search" in agent["prompt"]
    assert "state that evidence is insufficient" in agent["prompt"]
    assert agent["permission"]["*"] == "deny"
    assert agent["permission"]["hvac_*"] == "allow"
    mcp = config["mcp"]["hvac"]
    assert mcp["type"] == "local"
    assert mcp["command"][-2:] == ["-m", "project_copilot.codex_mcp_server"]
    assert mcp["timeout"] == 60_000
    assert mcp["environment"]["PROJECT_COPILOT_MCP_DATABASE"] == str(
        prepared.database_path
    )


def test_opencode_launch_is_isolated_and_keeps_secret_out_of_payload(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, settings.codex_settings()).prepare()

    launch = build_opencode_launch(settings, prepared)

    assert launch.argv[0] == str(settings.node_bin)
    assert launch.argv[1].endswith("opencode_sdk_worker.mjs")
    assert launch.env["CODEX_API_KEY"] == "top-secret"
    assert launch.env["HOME"] == str(prepared.codex_home)
    assert launch.env["OPENCODE_CONFIG_DIR"] == str(prepared.codex_home / "config")
    assert launch.env["OPENCODE_EXPERIMENTAL_NATIVE_LLM"] == "true"
    assert "top-secret" not in json.dumps(launch.request)
    assert "top-secret" not in " ".join(launch.argv)
    assert launch.request["sdk_entrypoint"] == str(settings.sdk_entrypoint)
    assert launch.request["cwd"] == str(prepared.workspace_root)
    assert launch.request["max_steps"] == 10
    assert launch.request["output_mode"] == "text_json"


def test_opencode_worker_uses_v1_sdk_path_query_and_body_contract() -> None:
    worker = (
        REPOSITORY_ROOT / "src" / "project_copilot" / "opencode_sdk_worker.mjs"
    ).read_text(encoding="utf-8")

    assert "path: { id: sessionID }" in worker
    assert "query: { directory: request.cwd }" in worker
    assert "body: promptBody" in worker
    assert "runtime.client.session.prompt(\n      promptBody," not in worker


def test_opencode_chat_completions_mode_uses_official_compatible_adapter(
    tmp_path: Path,
) -> None:
    settings = replace(_settings(tmp_path), wire_api="chat_completions")
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, settings.codex_settings()).prepare()

    launch = build_opencode_launch(settings, prepared)
    provider = launch.request["config"]["provider"]["company"]

    assert provider["npm"] == "@ai-sdk/openai-compatible"
    assert launch.request["provider_id"] == "company"
    assert launch.request["wire_api"] == "chat_completions"
    assert launch.env["OPENCODE_EXPERIMENTAL_NATIVE_LLM"] == "false"
    assert launch.request["config"]["model"] == "company/gpt-5.6-sol"


def test_opencode_turn_adapter_reuses_strict_grounding_parser(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, settings.codex_settings()).prepare()

    jsonl = OpenCodeTurnAdapter().to_codex_jsonl(_raw_sdk_payload())
    answer = CodexTurnParser(prepared).parse(jsonl, question="Check HP-02")

    assert answer["answer_markdown"] == "HP-02 has 12 missing rows."
    assert answer["grounding_status"] == "grounded"
    assert answer["citations"][0]["filename"] == "telemetry.csv"
    assert answer["activities"][0]["tool"] == "data-quality"


def test_opencode_turn_adapter_rejects_non_mcp_tool() -> None:
    payload = _raw_sdk_payload()
    payload["messages"][0]["parts"][0]["tool"] = "bash"

    with pytest.raises(OpenCodeRuntimeError, match="unapproved tool"):
        OpenCodeTurnAdapter().to_codex_jsonl(payload)


def test_opencode_turn_adapter_accepts_valid_text_after_structured_output_error(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, settings.codex_settings()).prepare()
    payload = _raw_sdk_payload()
    tool = payload["messages"][0]["parts"][0]
    prompt_result = {
        "info": {
            "role": "assistant",
            "error": {
                "name": "StructuredOutputError",
                "data": {"message": "Model did not produce structured output"},
            },
        },
        "parts": [tool, {"type": "text", "text": json.dumps(_answer())}],
    }
    payload["prompt_result"] = prompt_result
    payload["messages"] = [prompt_result]

    jsonl = OpenCodeTurnAdapter().to_codex_jsonl(payload)
    answer = CodexTurnParser(prepared).parse(jsonl, question="Check HP-02")

    assert answer["grounding_status"] == "grounded"


def test_opencode_turn_adapter_accepts_plain_json_text_after_tool_evidence(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, settings.codex_settings()).prepare()
    payload = _raw_sdk_payload()
    final = {
        "info": {"role": "assistant", "finish": "stop"},
        "parts": [{"type": "text", "text": json.dumps(_answer())}],
    }
    payload["prompt_result"] = final
    payload["messages"].append(final)

    jsonl = OpenCodeTurnAdapter().to_codex_jsonl(payload)
    answer = CodexTurnParser(prepared).parse(jsonl, question="Check HP-02")

    assert answer["answer_markdown"] == "HP-02 has 12 missing rows."
    assert answer["grounding_status"] == "grounded"


def test_opencode_turn_adapter_keeps_approved_plain_text_tool_output() -> None:
    payload = _raw_sdk_payload()
    payload["messages"][0]["parts"][0]["tool"] = "hvac_schema"
    payload["messages"][0]["parts"][0]["state"]["output"] = (
        '{"table_name":"assets"}\n\n{"table_name":"telemetry_clean"}'
    )
    payload["messages"][0]["parts"][0]["state"]["metadata"] = {"truncated": False}
    payload["prompt_result"]["info"]["structured"]["citations"] = []

    jsonl = OpenCodeTurnAdapter().to_codex_jsonl(payload)
    events = [json.loads(line) for line in jsonl.splitlines()]
    result = events[0]["item"]["result"]["structured_content"]["result"]

    assert "telemetry_clean" in result["raw_output"]


def test_opencode_turn_adapter_omits_invalid_tool_display_table() -> None:
    wide_table = {
        "title": "Mixed events",
        "columns": [f"c{index}" for index in range(13)],
        "rows": [[index for index in range(13)]],
    }
    state = {
        "status": "completed",
        "output": json.dumps(
            {
                "summary": "Approved evidence remains available.",
                "tables": [wide_table],
                "charts": [],
                "citations": [{"filename": "telemetry.csv", "excerpt": ""}],
            }
        ),
    }

    result = OpenCodeTurnAdapter._structured_tool_result(state)["result"]

    assert result["summary"] == "Approved evidence remains available."
    assert result["tables"] == []
    assert result["grounding_tables"] == [wide_table]
    assert result["citations"][0]["filename"] == "telemetry.csv"


def test_opencode_sdk_runner_normalizes_official_sdk_response(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, settings.codex_settings()).prepare()
    launch = build_opencode_launch(settings, prepared)
    captured: dict[str, object] = {}

    def fake_process(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        captured["request"] = json.loads(str(kwargs["input"]))
        captured["timeout"] = kwargs["timeout"]
        captured["encoding"] = kwargs["encoding"]
        captured["errors"] = kwargs["errors"]
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps(_raw_sdk_payload(), ensure_ascii=False),
            stderr="",
        )

    jsonl = OpenCodeSdkRunner(settings, process_runner=fake_process).run(
        launch,
        "查询 HP-02 当前配置",
        123,
    )

    assert captured["argv"] == launch.argv
    assert captured["request"]["prompt"] == "查询 HP-02 当前配置"
    assert captured["request"]["turn_timeout_ms"] == 108_000
    assert captured["request"]["max_steps"] == 10
    assert captured["timeout"] == 123
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "strict"
    assert json.loads(jsonl.splitlines()[-1])["type"] == "turn.completed"


def test_opencode_sdk_runner_kills_windows_process_tree_on_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, settings.codex_settings()).prepare()
    launch = build_opencode_launch(settings, prepared)
    calls: list[list[str]] = []

    class FakeProcess:
        pid = 4321
        returncode = None

        def communicate(self, **kwargs: object) -> tuple[str, str]:
            if "timeout" in kwargs:
                raise subprocess.TimeoutExpired(launch.argv, kwargs["timeout"])
            self.returncode = 1
            return "", ""

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: int) -> int:
            del timeout
            self.returncode = 1
            return 1

        def kill(self) -> None:
            self.returncode = 1

    monkeypatch.setattr(opencode_module.os, "name", "nt")
    monkeypatch.delattr(
        opencode_module.subprocess,
        "CREATE_NEW_PROCESS_GROUP",
        raising=False,
    )
    monkeypatch.setattr(
        opencode_module.subprocess, "Popen", lambda *a, **k: FakeProcess()
    )

    def fake_cleanup(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="SUCCESS", stderr="")

    monkeypatch.setattr(opencode_module.subprocess, "run", fake_cleanup)

    with pytest.raises(OpenCodeRuntimeError, match="time budget"):
        OpenCodeSdkRunner(settings).run(launch, "timeout", 20)

    assert calls == [["taskkill", "/PID", "4321", "/T", "/F"]]


@pytest.mark.parametrize("cleanup_failure", ["nonzero", "timeout", "oserror"])
def test_opencode_sdk_runner_fails_closed_when_windows_tree_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_failure: str,
) -> None:
    settings = _settings(tmp_path)
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, settings.codex_settings()).prepare()
    launch = build_opencode_launch(settings, prepared)

    class FakeProcess:
        pid = 4321
        returncode = None

        def communicate(self, **kwargs: object) -> tuple[str, str]:
            raise subprocess.TimeoutExpired(launch.argv, kwargs.get("timeout", 20))

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: int) -> int:
            del timeout
            self.returncode = 1
            return 1

        def kill(self) -> None:
            self.returncode = 1

    monkeypatch.setattr(opencode_module.os, "name", "nt")
    monkeypatch.setattr(
        opencode_module.subprocess, "Popen", lambda *a, **k: FakeProcess()
    )

    def failed_cleanup(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        if cleanup_failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 15)
        if cleanup_failure == "oserror":
            raise OSError("taskkill unavailable")
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="failed")

    monkeypatch.setattr(opencode_module.subprocess, "run", failed_cleanup)

    with pytest.raises(OpenCodeRuntimeError, match="process-tree cleanup failed"):
        OpenCodeSdkRunner(settings).run(launch, "timeout", 20)


def test_opencode_runtime_uses_fresh_session_and_compact_history(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    launches = []
    prompts: list[str] = []

    class FakeRunner:
        def run(self, launch, prompt: str, timeout_seconds: int) -> str:  # type: ignore[no-untyped-def]
            launches.append(launch)
            prompts.append(prompt)
            return OpenCodeTurnAdapter().to_codex_jsonl(_raw_sdk_payload())

    runtime = OpenCodeRuntime(settings, CORPUS_ROOT, runner=FakeRunner())

    result = asyncio.run(
        runtime.answer_async(
            "Continue checking HP-02",
            history=[{"role": "user", "content": "Start with data quality"}],
            workflow_id="data-quality-efficiency",
        )
    )
    asyncio.run(runtime.answer_async("Check again", history=[]))

    assert launches[0].prepared.session_root != launches[1].prepared.session_root
    assert "Start with data quality" in prompts[0]
    assert "data-quality-efficiency" in prompts[0]
    assert result["model_backed"] is True


def test_web_selects_truthful_fixed_opencode_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARS_SKIP_CPU_CHECK", "1")
    monkeypatch.setenv("PROJECT_COPILOT_AGENT_RUNTIME", "opencode")
    from project_copilot.web import create_app

    captured: dict[str, object] = {}

    class FakeRuntime:
        model = "gpt-5.6-sol"
        workspace_name = "Agentic HVAC Bakeoff"
        source_count = 11
        provider_is_remote = True

        async def answer_async(
            self,
            question: str,
            *,
            history: list[dict[str, str]],
            workflow_id: str | None,
        ) -> dict[str, object]:
            captured.update(
                question=question,
                history=history,
                workflow_id=workflow_id,
            )
            return {
                "mode": "knowledge",
                "demo_mode": False,
                "model_backed": True,
                "answer_markdown": "Verified.",
                "tables": [],
                "charts": [],
                "citations": [],
                "activities": [],
                "clarification": False,
                "refused": False,
                "grounding_status": "grounded",
            }

    monkeypatch.setattr(
        "project_copilot.web._build_embedding_backend",
        lambda: (_ for _ in ()).throw(
            AssertionError("OpenCode mode must not load the legacy embedding stack")
        ),
    )
    monkeypatch.setattr(
        "project_copilot.web._build_reranker",
        lambda: (_ for _ in ()).throw(
            AssertionError("OpenCode mode must not load the legacy reranker")
        ),
    )
    monkeypatch.setattr(
        "project_copilot.web.resolve_knowledge_provider",
        lambda package: (_ for _ in ()).throw(
            AssertionError("OpenCode mode must not initialize legacy knowledge")
        ),
    )
    monkeypatch.setattr(
        "project_copilot.web._build_chat_generator",
        lambda: (_ for _ in ()).throw(
            AssertionError("OpenCode mode must not initialize legacy chat")
        ),
    )
    monkeypatch.setattr(
        "project_copilot.web.OpenCodeRuntime.from_environment",
        lambda **kwargs: FakeRuntime(),
    )
    client = TestClient(
        create_app(
            project_root=REPOSITORY_ROOT / "examples" / "synthetic_hvac",
            runtime_root=tmp_path / "web-runtime",
        ),
        base_url="http://127.0.0.1",
    )

    page = client.get("/")
    health = client.get("/api/health")
    response = client.post(
        "/api/direction/query",
        json={
            "question": "Continue checking HP-02",
            "history": [{"role": "user", "content": "Start with configuration"}],
            "workflow_id": "configuration-review",
        },
        headers={
            "host": "127.0.0.1",
            "origin": "http://127.0.0.1",
            "X-Project-Copilot": "1",
        },
    )
    upload = client.post(
        "/api/direction/sources",
        files={"files": ("meeting.md", b"approved", "text/markdown")},
        headers={
            "host": "127.0.0.1",
            "origin": "http://127.0.0.1",
            "X-Project-Copilot": "1",
        },
    )
    legacy_knowledge = client.post(
        "/api/knowledge/query",
        json={"question": "legacy"},
        headers={"X-Project-Copilot": "1"},
    )
    legacy_copilot = client.post(
        "/api/workspaces/synthetic-hvac-demo/copilot/query",
        json={"question": "legacy"},
        headers={"X-Project-Copilot": "1"},
    )

    assert page.status_code == 200
    assert "Agentic HVAC Bakeoff" in page.text
    assert health.json()["agent_runtime"] == "opencode"
    assert health.json()["egress_mode"] == "approved-provider"
    assert response.status_code == 200
    assert captured["workflow_id"] == "configuration-review"
    assert upload.status_code == 409
    assert legacy_knowledge.status_code == 409
    assert legacy_copilot.status_code == 409
