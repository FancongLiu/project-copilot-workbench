from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

import project_copilot.codex_runtime as codex_runtime
from project_copilot.company_api import CompanyAPISettings
from project_copilot.codex_mcp_server import run_operation
from project_copilot.codex_runtime import (
    CodexLaunch,
    CodexProcessRunner,
    CodexRuntime,
    CodexRuntimeError,
    CodexRuntimeSettings,
    CodexTurnParser,
    CodexWorkspaceBuilder,
    build_codex_launch,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = REPOSITORY_ROOT / "examples" / "agentic_hvac_bakeoff"
WEB_PROJECT_ROOT = REPOSITORY_ROOT / "examples" / "synthetic_hvac"


def _settings(tmp_path: Path) -> CodexRuntimeSettings:
    codex_bin = tmp_path / "codex.exe"
    codex_bin.write_bytes(b"codex")
    return CodexRuntimeSettings(
        codex_bin=codex_bin,
        runtime_root=tmp_path / "runtime",
        base_url="https://approved.example/v1",
        api_key="top-secret",
        model="gpt-test",
        python_executable=Path(sys.executable),
    )


def test_workspace_builder_creates_fresh_least_privilege_session(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    builder = CodexWorkspaceBuilder(CORPUS_ROOT, settings)

    first = builder.prepare()
    second = builder.prepare()

    assert first.session_root != second.session_root
    assert (first.workspace_root / "AGENTS.md").is_file()
    assert (
        first.workspace_root
        / "docs"
        / "source"
        / "configuration"
        / "current-unit-configuration.md"
    ).is_file()
    assert not (first.workspace_root / "hidden_truth").exists()
    assert not (first.workspace_root / "datasets").exists()
    assert first.database_path.is_file()
    assert first.workspace_root not in first.database_path.parents
    assert not list(first.session_root.rglob("duckdb.exe"))

    config = first.config_file.read_text(encoding="utf-8")
    assert 'default_permissions = "project-copilot"' in config
    assert 'sandbox = "elevated"' in config
    assert str(first.workspace_root).replace("\\", "\\\\") in config
    assert str(first.database_path).replace("\\", "\\\\") in config
    assert (
        f'{json.dumps(str(first.database_path.parent))} = "deny"' in config
    )
    assert f'{json.dumps(str(first.codex_home))} = "deny"' in config
    assert 'required = true' in config
    assert 'enabled_tools = ["schema", "data_quality", "cop_ranking"]' in config
    assert '":root"' not in config
    assert 'enabled = false' in config
    assert first.output_schema.is_file()


def test_codex_launch_is_ephemeral_and_keeps_secret_out_of_argv(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, settings).prepare()

    launch = build_codex_launch(settings, prepared)

    joined = " ".join(launch.argv)
    assert "top-secret" not in joined
    assert launch.env["CODEX_API_KEY"] == "top-secret"
    assert launch.env["CODEX_HOME"] == str(prepared.codex_home)
    assert "--ephemeral" in launch.argv
    assert "--strict-config" in launch.argv
    assert "--output-schema" in launch.argv
    assert str(prepared.output_schema) in launch.argv
    assert "--sandbox" not in launch.argv
    assert "resume" not in launch.argv
    assert launch.argv[-1] == "-"


def test_elevated_sandbox_preflight_proves_allowed_and_denied_reads(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    calls: list[list[str]] = []

    def fake_runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        joined = " ".join(argv)
        return subprocess.CompletedProcess(
            argv,
            0 if "AGENTS.md" in joined else 73,
            stdout="",
            stderr="",
        )

    marker = codex_runtime.verify_elevated_sandbox_preflight(
        settings,
        CORPUS_ROOT,
        runner=fake_runner,
    )

    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
    assert payload["codex_bin"] == str(settings.codex_bin.resolve())
    assert len(calls) == 2
    assert calls[0][1:6] == ["sandbox", "-P", "project-copilot", "-C", calls[0][5]]
    assert "AGENTS.md" in " ".join(calls[0])
    assert "private-evidence" in " ".join(calls[1])


def test_elevated_sandbox_preflight_rejects_readable_private_database(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)

    def readable_runner(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with pytest.raises(CodexRuntimeError, match="failed to block private database"):
        codex_runtime.verify_elevated_sandbox_preflight(
            settings,
            CORPUS_ROOT,
            runner=readable_runner,
        )

    assert not codex_runtime.preflight_marker_path(settings).exists()


def test_runtime_refuses_start_without_matching_elevated_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setenv("PROJECT_COPILOT_CODEX_BIN", str(settings.codex_bin))
    monkeypatch.setenv("PROJECT_COPILOT_CODEX_RUNTIME_ROOT", str(settings.runtime_root))
    monkeypatch.setattr(
        "project_copilot.company_api.load_codex_switch_settings",
        lambda: CompanyAPISettings(
            base_url=settings.base_url,
            api_key=settings.api_key,
            model=settings.model,
            allowed_hosts=("approved.example",),
            wire_api="responses",
        ),
    )

    with pytest.raises(CodexRuntimeError, match="preflight has not passed"):
        CodexRuntime.from_environment(
            corpus_root=CORPUS_ROOT,
            application_runtime=tmp_path / "application-runtime",
        )

    marker = settings.runtime_root / "elevated-sandbox-preflight.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "permissions_profile_version": 1,
                "status": "passed",
                "codex_bin": str(tmp_path / "different-codex.exe"),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CodexRuntimeError, match="different Codex executable"):
        CodexRuntime.from_environment(
            corpus_root=CORPUS_ROOT,
            application_runtime=tmp_path / "application-runtime",
        )


def _structured_answer(excerpt: str) -> str:
    return json.dumps(
        {
            "answer_markdown": "## 结论\n\nHP-03 最优，数据质量需要保留意见。",
            "citations": [
                {"filename": "data-analysis-sop.md", "excerpt": excerpt},
                {"filename": "telemetry.csv", "excerpt": ""},
            ],
        },
        ensure_ascii=False,
    )


def _mcp_result(*rows: dict[str, object]) -> dict[str, object]:
    return {
        "content": [],
        "structured_content": {"result": list(rows)},
    }


def test_turn_parser_requires_verified_mcp_calls_and_source_excerpts(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, settings).prepare()
    source = prepared.source_files["data-analysis-sop.md"]
    assert source is not None
    excerpt = source.read_text(encoding="utf-8")[:80]
    events = [
        {"type": "thread.started", "thread_id": "private-thread"},
        {
            "type": "item.completed",
            "item": {
                "id": "mcp-1",
                "type": "mcp_tool_call",
                "server": "hvac",
                "tool": "data_quality",
                "arguments": {},
                "result": _mcp_result({"asset_id": "HP-02", "missing_rows": 12}),
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "mcp-2",
                "type": "mcp_tool_call",
                "server": "hvac",
                "tool": "cop_ranking",
                "arguments": {},
                "result": _mcp_result(
                    {"asset_id": "HP-03", "load_weighted_cop": 4.001643}
                ),
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "answer",
                "type": "agent_message",
                "text": _structured_answer(excerpt),
            },
        },
        {"type": "turn.completed", "usage": {}},
    ]

    payload = CodexTurnParser(prepared).parse(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events)
    )

    assert payload["answer_markdown"].startswith("## 结论")
    assert "runtime_thread_id" not in payload
    assert [item["tool"] for item in payload["activities"]] == [
        "data-quality",
        "cop-ranking",
    ]
    assert [item["filename"] for item in payload["citations"]] == [
        "data-analysis-sop.md",
        "telemetry.csv",
    ]
    assert payload["grounding_status"] == "grounded"


def test_turn_parser_rejects_empty_structured_data_result(tmp_path: Path) -> None:
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, _settings(tmp_path)).prepare()
    source = prepared.source_files["data-analysis-sop.md"]
    assert source is not None
    excerpt = source.read_text(encoding="utf-8")[:80]
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": "mcp-empty",
                "type": "mcp_tool_call",
                "server": "hvac",
                "tool": "data_quality",
                "arguments": {},
                "result": _mcp_result(),
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "answer",
                "type": "agent_message",
                "text": _structured_answer(excerpt),
            },
        },
        {"type": "turn.completed", "usage": {}},
    ]

    with pytest.raises(CodexRuntimeError, match="did not return evidence"):
        CodexTurnParser(prepared).parse(
            "\n".join(json.dumps(event, ensure_ascii=False) for event in events)
        )


def test_turn_parser_rejects_schema_only_telemetry_grounding(tmp_path: Path) -> None:
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, _settings(tmp_path)).prepare()
    source = prepared.source_files["data-analysis-sop.md"]
    assert source is not None
    excerpt = source.read_text(encoding="utf-8")[:80]
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": "schema-only",
                "type": "mcp_tool_call",
                "server": "hvac",
                "tool": "schema",
                "arguments": {},
                "result": _mcp_result(
                    {"table_name": "telemetry", "column_name": "asset_id"}
                ),
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "answer",
                "type": "agent_message",
                "text": _structured_answer(excerpt),
            },
        },
        {"type": "turn.completed", "usage": {}},
    ]

    with pytest.raises(CodexRuntimeError, match="data citation is not verified"):
        CodexTurnParser(prepared).parse(
            "\n".join(json.dumps(event, ensure_ascii=False) for event in events)
        )


@pytest.mark.parametrize(
    "jsonl",
    [
        "not-json",
        json.dumps({"type": "turn.failed", "error": {"message": "private detail"}}),
    ],
)
def test_turn_parser_fails_closed_on_invalid_stream(
    tmp_path: Path,
    jsonl: str,
) -> None:
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, _settings(tmp_path)).prepare()

    with pytest.raises(CodexRuntimeError, match="did not produce a verifiable answer"):
        CodexTurnParser(prepared).parse(jsonl)


def test_turn_parser_rejects_unverified_excerpt(tmp_path: Path) -> None:
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, _settings(tmp_path)).prepare()
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": "answer",
                "type": "agent_message",
                "text": _structured_answer("text that is not in the source"),
            },
        },
        {"type": "turn.completed", "usage": {}},
    ]

    with pytest.raises(CodexRuntimeError, match="citation excerpt"):
        CodexTurnParser(prepared).parse(
            "\n".join(json.dumps(event, ensure_ascii=False) for event in events)
        )


def test_codex_runtime_uses_fresh_session_and_carries_compact_history(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    launches: list[CodexLaunch] = []
    prompts: list[str] = []

    class FakeRunner:
        def run(self, launch, prompt: str, timeout_seconds: int) -> str:  # type: ignore[no-untyped-def]
            launches.append(launch)
            prompts.append(prompt)
            prepared = launch.prepared
            source = prepared.source_files["data-analysis-sop.md"]
            assert source is not None
            excerpt = source.read_text(encoding="utf-8")[:80]
            return "\n".join(
                [
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "id": "mcp",
                                "type": "mcp_tool_call",
                                "server": "hvac",
                                "tool": "data_quality",
                                "arguments": {},
                                "result": _mcp_result(
                                    {"asset_id": "HP-02", "missing_rows": 12}
                                ),
                                "status": "completed",
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "id": "answer",
                                "type": "agent_message",
                                "text": _structured_answer(excerpt),
                            },
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps({"type": "turn.completed", "usage": {}}),
                ]
            )

    runtime = CodexRuntime(settings, CORPUS_ROOT, runner=FakeRunner())

    first = asyncio.run(
        runtime.answer_async(
            "继续核对 HP-02。",
            history=[
                {"role": "user", "content": "先看配置。"},
                {"role": "assistant", "content": "当前设定为 10 C。"},
            ],
            workflow_id="configuration-review",
        )
    )
    asyncio.run(runtime.answer_async("再核对一次。", history=[]))

    assert launches[0].prepared.session_root != launches[1].prepared.session_root
    assert "先看配置" in prompts[0]
    assert "configuration-review" in prompts[0]
    assert "runtime_thread_id" not in first


def test_web_selects_truthful_fixed_codex_evaluation_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARS_SKIP_CPU_CHECK", "1")
    from project_copilot.web import create_app

    captured: dict[str, object] = {}

    class FakeRuntime:
        model = "gpt-test"
        workspace_name = "Agentic HVAC Bakeoff"
        source_count = 11

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
                "answer_markdown": "## 结论\n\n已核对。",
                "tables": [],
                "charts": [],
                "citations": [],
                "activities": [],
                "clarification": False,
                "refused": False,
                "grounding_status": "grounded",
            }

    monkeypatch.setenv("PROJECT_COPILOT_AGENT_RUNTIME", "codex")
    monkeypatch.setenv("PROJECT_COPILOT_EMBEDDING_MODEL", "must-not-load")
    monkeypatch.setattr(
        "project_copilot.web._build_embedding_backend",
        lambda: (_ for _ in ()).throw(
            AssertionError("Codex runtime must not initialize the legacy embedding stack")
        ),
    )
    monkeypatch.setattr(
        "project_copilot.web.CodexRuntime.from_environment",
        lambda **kwargs: FakeRuntime(),
    )
    client = TestClient(
        create_app(
            project_root=WEB_PROJECT_ROOT,
            runtime_root=tmp_path / "web-runtime",
        ),
        base_url="http://127.0.0.1",
    )

    page = client.get("/")
    health = client.get("/api/health")
    response = client.post(
        "/api/direction/query",
        json={
            "question": "继续核对 HP-02。",
            "history": [{"role": "user", "content": "先看配置。"}],
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

    assert page.status_code == 200
    assert "Agentic HVAC Bakeoff" in page.text
    assert "11 个文件" in page.text
    assert "固定合成测试资料" in page.text
    assert "上传文件" not in page.text
    assert health.json()["agent_runtime"] == "codex"
    assert health.json()["network_allowed"] is True
    assert health.json()["egress_mode"] == "approved-provider"
    assert response.status_code == 200
    assert captured == {
        "question": "继续核对 HP-02。",
        "history": [{"role": "user", "content": "先看配置。"}],
        "workflow_id": "configuration-review",
    }
    assert upload.status_code == 409
    assert "fixed synthetic evidence" in upload.json()["detail"]


def test_single_chat_frontend_keeps_named_workflows_without_raw_thread_ids() -> None:
    script = (
        REPOSITORY_ROOT / "src" / "project_copilot" / "static" / "direction.js"
    ).read_text(encoding="utf-8")
    template = (
        REPOSITORY_ROOT
        / "src"
        / "project_copilot"
        / "templates"
        / "direction.html"
    ).read_text(encoding="utf-8")

    assert "runtimeThreadId" not in script
    assert "runtime_thread_id" not in script
    assert "workflow_id: workflowId" in script
    assert 'data-workflow="project-overview"' in template
    assert 'data-workflow="configuration-review"' in template
    assert 'data-workflow="data-quality-efficiency"' in template


def test_process_runner_returns_jsonl_and_persists_private_run_log(
    tmp_path: Path,
) -> None:
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, _settings(tmp_path)).prepare()
    env = os.environ.copy()
    env["CODEX_HOME"] = str(prepared.codex_home)
    event = {"type": "turn.completed", "usage": {}}
    launch = CodexLaunch(
        argv=[
            sys.executable,
            "-c",
            "import json,sys; sys.stdin.read(); " f"print(json.dumps({event!r}))",
        ],
        env=env,
        prepared=prepared,
    )

    result = CodexProcessRunner().run(launch, "question", 30)

    assert json.loads(result)["type"] == "turn.completed"
    assert prepared.events_log.read_text(encoding="utf-8") == result


def test_process_runner_sanitizes_nonzero_exit(tmp_path: Path) -> None:
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, _settings(tmp_path)).prepare()
    env = os.environ.copy()
    env["CODEX_HOME"] = str(prepared.codex_home)
    launch = CodexLaunch(
        argv=[
            sys.executable,
            "-c",
            "import sys; print('private provider detail', file=sys.stderr); raise SystemExit(7)",
        ],
        env=env,
        prepared=prepared,
    )

    with pytest.raises(CodexRuntimeError, match="exited with code 7") as error:
        CodexProcessRunner().run(launch, "question", 30)

    assert "private provider detail" not in str(error.value)
    assert "private provider detail" in prepared.stderr_log.read_text(encoding="utf-8")


def test_mcp_operations_are_fixed_and_read_only() -> None:
    database = CORPUS_ROOT / "datasets" / "hvac_bakeoff.duckdb"

    quality = run_operation("data_quality", database)
    ranking = run_operation("cop_ranking", database)

    assert any(row["asset_id"] == "HP-02" for row in quality)
    assert ranking[0]["asset_id"] == "HP-03"
    with pytest.raises(ValueError, match="Unsupported governed operation"):
        run_operation("arbitrary_sql", database)


def test_codex_windows_bootstrap_pins_official_runtime_without_credentials() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "bootstrap-codex-runtime.ps1").read_text(
        encoding="utf-8"
    )

    assert '@openai/codex@0.144.5' in script
    assert 'duckdb_cli-windows-amd64.zip' not in script
    assert 'OPENAI_API_KEY' not in script


def test_codex_windows_run_wrapper_is_explicit_and_loopback_only() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "run-codex.ps1").read_text(
        encoding="utf-8"
    )

    assert 'PROJECT_COPILOT_AGENT_RUNTIME' in script
    assert 'PROJECT_COPILOT_ACK_CODEX_SWITCH' in script
    assert 'PROJECT_COPILOT_CODEX_CONFIG' in script
    assert '--host", "127.0.0.1"' in script
    assert 'PROJECT_COPILOT_CODEX_BIN' in script
    assert 'PROJECT_COPILOT_GOVERNED_DUCKDB_CLI' not in script
    assert 'project-copilot-codex-preflight.exe' in script
    assert script.index('project-copilot-codex-preflight.exe') < script.index(
        '& $exe @arguments'
    )
    pyproject = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert (
        'project-copilot-codex-preflight = "project_copilot.codex_preflight:main"'
        in pyproject
    )
