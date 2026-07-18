from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

import project_copilot.codex_runtime as codex_runtime
from project_copilot.company_api import CompanyAPISettings
from project_copilot.codex_mcp_server import run_operation, run_typed_operation
from project_copilot.codex_sdk_worker import run_sdk_turn
from project_copilot.codex_runtime import (
    CodexLaunch,
    CodexPythonSdkRunner,
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


def _settings(
    tmp_path: Path,
    *,
    enforce_windows_acl: bool = False,
) -> CodexRuntimeSettings:
    codex_bin = tmp_path / "codex.exe"
    codex_bin.write_bytes(b"codex")
    return CodexRuntimeSettings(
        codex_bin=codex_bin,
        runtime_root=tmp_path / "runtime",
        base_url="https://approved.example/v1",
        api_key="top-secret",
        model="gpt-test",
        python_executable=Path(sys.executable),
        enforce_windows_acl=enforce_windows_acl,
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
    agents = (first.workspace_root / "AGENTS.md").read_text(encoding="utf-8")
    assert "Do not execute Shell" in agents
    assert "search_project_knowledge" in agents
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
    assert (first.database_path.parent / "manifest.json").is_file()
    assert first.source_files["manifest.json"] == (
        first.database_path.parent / "manifest.json"
    )
    assert first.source_files["config_history.csv"] is None
    assert (
        first.database_path.parent
        / "docs"
        / "source"
        / "configuration"
        / "current-unit-configuration.md"
    ).is_file()
    assert first.workspace_root not in first.database_path.parents
    assert not list(first.session_root.rglob("duckdb.exe"))

    config = first.config_file.read_text(encoding="utf-8")
    assert 'default_permissions = "project-copilot"' in config
    assert 'sandbox = "elevated"' in config
    assert str(first.workspace_root).replace("\\", "\\\\") in config
    assert str(first.database_path).replace("\\", "\\\\") in config
    assert f'{json.dumps(str(first.database_path.parent))} = "deny"' in config
    assert f'{json.dumps(str(first.codex_home))} = "deny"' in config
    assert "required = true" in config
    assert '"search_project_knowledge"' in config
    assert '"inspect_configuration_change_effect"' in config
    assert "PROJECT_COPILOT_MCP_CORPUS" in config
    assert '":root"' not in config
    assert "enabled = false" in config
    assert first.output_schema.is_file()
    output_schema = json.loads(first.output_schema.read_text(encoding="utf-8"))
    assert {"answer_markdown", "citations", "tables", "charts"} <= set(
        output_schema["required"]
    )


def test_workspace_builder_protects_private_runtime_when_acl_is_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protected: list[Path] = []
    monkeypatch.setattr(
        codex_runtime,
        "protect_private_runtime_paths",
        lambda paths: protected.extend(paths),
    )

    prepared = CodexWorkspaceBuilder(
        CORPUS_ROOT,
        _settings(tmp_path, enforce_windows_acl=True),
    ).prepare()

    assert protected == [prepared.database_path.parent]


def test_private_runtime_acl_denies_read_to_codex_sandbox_group(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private"
    private.mkdir()
    calls: list[list[str]] = []

    def fake_runner(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    codex_runtime.protect_private_runtime_paths(
        [private],
        runner=fake_runner,
        sid_lookup=lambda: "S-1-5-21-test-1004",
    )

    assert calls == [
        [
            "icacls.exe",
            str(private),
            "/deny",
            "*S-1-5-21-test-1004:(OI)(CI)(R)",
            "/T",
            "/Q",
        ]
    ]


def test_private_runtime_acl_fails_closed_when_icacls_fails(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir()

    def failed_runner(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 5, stdout="", stderr="private detail")

    with pytest.raises(CodexRuntimeError, match="private runtime ACL") as error:
        codex_runtime.protect_private_runtime_paths(
            [private],
            runner=failed_runner,
            sid_lookup=lambda: "S-1-5-21-test-1004",
        )

    assert "private detail" not in str(error.value)


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
    timeouts: list[object] = []

    def fake_runner(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        timeouts.append(kwargs.get("timeout"))
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
    assert len(calls) == 3
    assert calls[0][1:6] == ["sandbox", "-P", "project-copilot", "-C", calls[0][5]]
    assert "AGENTS.md" in " ".join(calls[0])
    assert "private-evidence" in " ".join(calls[1])
    assert "web.py" in " ".join(calls[2])
    assert timeouts == [120, 120, 120]


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


def test_elevated_sandbox_preflight_rejects_readable_application_source(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)

    def source_readable_runner(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        joined = " ".join(argv)
        return subprocess.CompletedProcess(
            argv,
            73 if "private-evidence" in joined else 0,
            stdout="",
            stderr="",
        )

    with pytest.raises(CodexRuntimeError, match="failed to isolate application source"):
        codex_runtime.verify_elevated_sandbox_preflight(
            settings,
            CORPUS_ROOT,
            runner=source_readable_runner,
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
                "codex_bin": str(settings.codex_bin.resolve()),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CodexRuntimeError, match="preflight is outdated"):
        CodexRuntime.from_environment(
            corpus_root=CORPUS_ROOT,
            application_runtime=tmp_path / "application-runtime",
        )

    marker.write_text(
        json.dumps(
            {
                "schema_version": 2,
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


def test_runtime_selects_python_sdk_by_default_and_cli_only_when_explicit(
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
    marker = settings.runtime_root / "elevated-sandbox-preflight.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "schema_version": codex_runtime.PREFLIGHT_SCHEMA_VERSION,
                "permissions_profile_version": codex_runtime.PERMISSIONS_PROFILE_VERSION,
                "status": "passed",
                "codex_bin": str(settings.codex_bin.resolve()),
            }
        ),
        encoding="utf-8",
    )

    runtime = CodexRuntime.from_environment(
        corpus_root=CORPUS_ROOT,
        application_runtime=tmp_path / "application-runtime",
    )
    assert isinstance(runtime.runner, CodexPythonSdkRunner)

    monkeypatch.setenv("PROJECT_COPILOT_CODEX_TRANSPORT", "cli-jsonl")
    runtime = CodexRuntime.from_environment(
        corpus_root=CORPUS_ROOT,
        application_runtime=tmp_path / "application-runtime",
    )
    assert isinstance(runtime.runner, CodexProcessRunner)

    monkeypatch.setenv("PROJECT_COPILOT_CODEX_TRANSPORT", "silent-fallback")
    with pytest.raises(CodexRuntimeError, match="transport"):
        CodexRuntime.from_environment(
            corpus_root=CORPUS_ROOT,
            application_runtime=tmp_path / "application-runtime",
        )


def _structured_answer(excerpt: str) -> str:
    return json.dumps(
        {
            "answer_markdown": "## 结论\n\nHP-03 最优，数据质量需要保留意见。",
            "tables": [],
            "charts": [],
            "citations": [
                {"filename": "data-analysis-sop.md", "excerpt": excerpt},
                {"filename": "telemetry.csv", "excerpt": ""},
            ],
        },
        ensure_ascii=False,
    )


def _telemetry_answer() -> str:
    return json.dumps(
        {
            "answer_markdown": "## Conclusion\n\nThe governed telemetry was checked.",
            "tables": [],
            "charts": [],
            "citations": [{"filename": "telemetry.csv", "excerpt": ""}],
        }
    )


def _mcp_result(*rows: dict[str, object]) -> dict[str, object]:
    return {
        "content": [],
        "structured_content": {"result": list(rows)},
    }


def _mcp_payload(payload: dict[str, object]) -> dict[str, object]:
    return {"content": [], "structured_content": payload}


def _knowledge_event(excerpt: str) -> dict[str, object]:
    return {
        "type": "item.completed",
        "item": {
            "id": "knowledge",
            "type": "mcp_tool_call",
            "server": "hvac",
            "tool": "search_project_knowledge",
            "arguments": {"query": "approved analysis SOP"},
            "result": _mcp_payload(
                {
                    "summary": excerpt,
                    "tables": [],
                    "charts": [],
                    "citations": [
                        {
                            "filename": "data-analysis-sop.md",
                            "excerpt": excerpt,
                            "location": "sops/data-analysis-sop.md",
                        }
                    ],
                }
            ),
            "status": "completed",
        },
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
        _knowledge_event(excerpt),
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
        "search-project-knowledge",
        "data-quality",
        "cop-ranking",
    ]
    assert [item["filename"] for item in payload["citations"]] == [
        "data-analysis-sop.md",
        "telemetry.csv",
    ]
    assert payload["citations"][0]["location"] == "sops/data-analysis-sop.md"
    assert payload["grounding_status"] == "grounded"


def test_turn_parser_preserves_bounded_structured_presentations(
    tmp_path: Path,
) -> None:
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, _settings(tmp_path)).prepare()
    answer = json.dumps(
        {
            "answer_markdown": "## 结论\n\nHP-03 在快照期内排名第一。",
            "tables": [
                {
                    "title": "COP 排名",
                    "columns": ["机组", "负荷加权 COP"],
                    "rows": [["HP-03", 4.001643], ["HP-02", 3.995067]],
                }
            ],
            "charts": [
                {
                    "kind": "bar",
                    "title": "COP 排名",
                    "unit": "COP",
                    "points": [
                        {"label": "HP-03", "value": 4.001643},
                        {"label": "HP-02", "value": 3.995067},
                    ],
                }
            ],
            "citations": [
                {"filename": "telemetry.csv", "excerpt": ""},
            ],
        },
        ensure_ascii=False,
    )
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": "mcp-ranking",
                "type": "mcp_tool_call",
                "server": "hvac",
                "tool": "cop_ranking",
                "arguments": {},
                "result": _mcp_result(
                    {"asset_id": "HP-03", "load_weighted_cop": 4.001643},
                    {"asset_id": "HP-02", "load_weighted_cop": 3.995067},
                ),
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {"id": "answer", "type": "agent_message", "text": answer},
        },
        {"type": "turn.completed", "usage": {}},
    ]

    payload = CodexTurnParser(prepared).parse(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events)
    )

    assert payload["tables"][0]["columns"] == ["机组", "负荷加权 COP"]
    assert payload["tables"][0]["rows"][0] == ["HP-03", 4.001643]
    assert payload["charts"][0]["kind"] == "bar"
    assert payload["charts"][0]["points"][0] == {
        "label": "HP-03",
        "value": 4.001643,
    }


def test_turn_parser_prefers_governed_tool_presentations(
    tmp_path: Path,
) -> None:
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, _settings(tmp_path)).prepare()
    tool_payload = {
        "summary": "configuration effect",
        "tables": [
            {
                "title": "配置变更前后两小时",
                "columns": ["比较窗口", "送风温度", "电耗"],
                "rows": [["变更前", 12.2, 36.0], ["变更后", 10.3, 40.0]],
            }
        ],
        "charts": [
            {
                "kind": "bar",
                "title": "配置变更前后电耗",
                "unit": "kWh",
                "points": [
                    {"label": "变更前", "value": 36.0},
                    {"label": "变更后", "value": 40.0},
                ],
            }
        ],
        "citations": [
            {
                "filename": "telemetry.csv",
                "excerpt": "governed telemetry evidence",
                "location": "datasets/telemetry.csv",
            }
        ],
    }
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": "configuration-effect",
                "type": "mcp_tool_call",
                "server": "hvac",
                "tool": "inspect_configuration_change_effect",
                "arguments": {
                    "asset_id": "HP-02",
                    "parameter_name": "supply_air_sp_c",
                },
                "result": _mcp_payload(tool_payload),
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "answer",
                "type": "agent_message",
                "text": _telemetry_answer(),
            },
        },
        {"type": "turn.completed", "usage": {}},
    ]

    payload = CodexTurnParser(prepared).parse(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events)
    )

    assert payload["activities"][0]["tool"] == "inspect_configuration_change_effect"
    assert payload["tables"] == tool_payload["tables"]
    assert payload["charts"] == tool_payload["charts"]


def test_turn_parser_rejects_telemetry_citation_from_configuration_only_tool(
    tmp_path: Path,
) -> None:
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, _settings(tmp_path)).prepare()
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": "configuration-history",
                "type": "mcp_tool_call",
                "server": "hvac",
                "tool": "inspect_configuration_history",
                "arguments": {
                    "asset_id": "HP-02",
                    "parameter_name": "supply_air_sp_c",
                },
                "result": _mcp_payload(
                    {
                        "summary": "configuration only",
                        "tables": [],
                        "charts": [],
                        "citations": [
                            {
                                "filename": "config_history.csv",
                                "excerpt": "approved configuration history",
                                "location": "datasets/config_history.csv",
                            }
                        ],
                    }
                ),
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "answer",
                "type": "agent_message",
                "text": _telemetry_answer(),
            },
        },
        {"type": "turn.completed", "usage": {}},
    ]

    with pytest.raises(CodexRuntimeError, match="data citation"):
        CodexTurnParser(prepared).parse(
            "\n".join(json.dumps(event, ensure_ascii=False) for event in events)
        )


def test_turn_parser_accepts_canonical_document_citations_from_knowledge_tool(
    tmp_path: Path,
) -> None:
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, _settings(tmp_path)).prepare()
    source = prepared.source_files["data-analysis-sop.md"]
    assert source is not None
    excerpt = source.read_text(encoding="utf-8")[:80]
    answer = json.dumps(
        {
            "answer_markdown": "## Conclusion\n\nUse the approved analysis SOP.",
            "tables": [],
            "charts": [],
            "citations": [{"filename": "data-analysis-sop.md", "excerpt": excerpt}],
        },
        ensure_ascii=False,
    )
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": "knowledge",
                "type": "mcp_tool_call",
                "server": "hvac",
                "tool": "search_project_knowledge",
                "arguments": {"query": "approved analysis SOP"},
                "result": _mcp_payload(
                    {
                        "summary": excerpt,
                        "tables": [],
                        "charts": [],
                        "citations": [
                            {
                                "filename": "data-analysis-sop.md",
                                "excerpt": excerpt,
                                "location": "sops/data-analysis-sop.md",
                            }
                        ],
                    }
                ),
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {"id": "answer", "type": "agent_message", "text": answer},
        },
        {"type": "turn.completed", "usage": {}},
    ]

    payload = CodexTurnParser(prepared).parse(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events)
    )

    assert payload["activities"][0]["tool"] == "search-project-knowledge"
    assert payload["citations"][0]["location"] == "sops/data-analysis-sop.md"


def test_turn_parser_rejects_document_citation_without_knowledge_tool(
    tmp_path: Path,
) -> None:
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, _settings(tmp_path)).prepare()
    source = prepared.source_files["data-analysis-sop.md"]
    assert source is not None
    excerpt = source.read_text(encoding="utf-8")[:80]
    answer = json.dumps(
        {
            "answer_markdown": "## Conclusion\n\nUse the analysis SOP.",
            "tables": [],
            "charts": [],
            "citations": [{"filename": "data-analysis-sop.md", "excerpt": excerpt}],
        },
        ensure_ascii=False,
    )
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": "answer",
                "type": "agent_message",
                "text": answer,
            },
        },
        {"type": "turn.completed", "usage": {}},
    ]

    with pytest.raises(CodexRuntimeError, match="knowledge tool"):
        CodexTurnParser(prepared).parse(
            "\n".join(json.dumps(event, ensure_ascii=False) for event in events)
        )


def test_turn_parser_rejects_document_citation_from_non_knowledge_tool(
    tmp_path: Path,
) -> None:
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, _settings(tmp_path)).prepare()
    source = prepared.source_files["data-analysis-sop.md"]
    assert source is not None
    excerpt = source.read_text(encoding="utf-8")[:80]
    answer = json.dumps(
        {
            "answer_markdown": "## Conclusion\n\nUse the analysis SOP.",
            "tables": [],
            "charts": [],
            "citations": [{"filename": "data-analysis-sop.md", "excerpt": excerpt}],
        }
    )
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": "configuration-history",
                "type": "mcp_tool_call",
                "server": "hvac",
                "tool": "inspect_configuration_history",
                "arguments": {"asset_id": "HP-02"},
                "result": _mcp_payload(
                    {
                        "summary": "configuration history",
                        "tables": [],
                        "charts": [],
                        "citations": [
                            {
                                "filename": "data-analysis-sop.md",
                                "excerpt": excerpt,
                                "location": "sops/data-analysis-sop.md",
                            }
                        ],
                    }
                ),
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {"id": "answer", "type": "agent_message", "text": answer},
        },
        {"type": "turn.completed", "usage": {}},
    ]

    with pytest.raises(CodexRuntimeError, match="knowledge tool"):
        CodexTurnParser(prepared).parse(
            "\n".join(json.dumps(event) for event in events)
        )


def test_turn_parser_rejects_model_presentation_values_not_returned_by_tools(
    tmp_path: Path,
) -> None:
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, _settings(tmp_path)).prepare()
    answer = json.dumps(
        {
            "answer_markdown": "## 结论\n\nHP-03 的 COP 为 99.9。",
            "tables": [
                {
                    "title": "COP 排名",
                    "columns": ["机组", "COP"],
                    "rows": [["HP-03", 99.9]],
                }
            ],
            "charts": [
                {
                    "kind": "bar",
                    "title": "COP 排名",
                    "unit": "COP",
                    "points": [{"label": "HP-03", "value": 99.9}],
                }
            ],
            "citations": [
                {"filename": "telemetry.csv", "excerpt": ""},
            ],
        },
        ensure_ascii=False,
    )
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": "mcp-ranking",
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
            "item": {"id": "answer", "type": "agent_message", "text": answer},
        },
        {"type": "turn.completed", "usage": {}},
    ]

    with pytest.raises(CodexRuntimeError, match="unsupported numeric evidence"):
        CodexTurnParser(prepared).parse(
            "\n".join(json.dumps(event, ensure_ascii=False) for event in events),
            question="Could the COP be 99.9?",
        )


def test_turn_parser_allows_explicit_rejection_of_an_unsupported_user_number(
    tmp_path: Path,
) -> None:
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, _settings(tmp_path)).prepare()
    answer = json.dumps(
        {
            "answer_markdown": (
                "## Conclusion\n\nI cannot verify 99.9 as a factual COP value."
            ),
            "tables": [],
            "charts": [],
            "citations": [{"filename": "telemetry.csv", "excerpt": ""}],
        }
    )
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": "mcp-ranking",
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
            "item": {"id": "answer", "type": "agent_message", "text": answer},
        },
        {"type": "turn.completed", "usage": {}},
    ]

    payload = CodexTurnParser(prepared).parse(
        "\n".join(json.dumps(event) for event in events),
        question="Could the COP be 99.9?",
    )

    assert "cannot verify 99.9" in payload["answer_markdown"]


def test_turn_parser_allows_explicit_chinese_rejection_of_unsafe_setpoint(
    tmp_path: Path,
) -> None:
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, _settings(tmp_path)).prepare()
    answer = json.dumps(
        {
            "answer_markdown": "## 结论\n\n拒绝将 140°C 作为安全设定。",
            "tables": [],
            "charts": [],
            "citations": [{"filename": "telemetry.csv", "excerpt": ""}],
        },
        ensure_ascii=False,
    )
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": "mcp-ranking",
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
            "item": {"id": "answer", "type": "agent_message", "text": answer},
        },
        {"type": "turn.completed", "usage": {}},
    ]

    payload = CodexTurnParser(prepared).parse(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events),
        question="请把安全设定改成 140°C。",
    )

    assert "拒绝将 140°C" in payload["answer_markdown"]


def test_turn_parser_rejects_double_negative_numeric_bypass(
    tmp_path: Path,
) -> None:
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, _settings(tmp_path)).prepare()
    answer = json.dumps(
        {
            "answer_markdown": (
                "## Conclusion\n\nIt is false that 99.9 is unsupported; "
                "this is the factual COP."
            ),
            "tables": [],
            "charts": [],
            "citations": [{"filename": "telemetry.csv", "excerpt": ""}],
        }
    )
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": "mcp-ranking",
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
            "item": {"id": "answer", "type": "agent_message", "text": answer},
        },
        {"type": "turn.completed", "usage": {}},
    ]

    with pytest.raises(CodexRuntimeError, match="unsupported numeric evidence"):
        CodexTurnParser(prepared).parse(
            "\n".join(json.dumps(event) for event in events),
            question="Could the COP be 99.9?",
        )


def test_turn_parser_rejects_any_shell_command(
    tmp_path: Path,
) -> None:
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, _settings(tmp_path)).prepare()
    source = prepared.source_files["data-analysis-sop.md"]
    assert source is not None
    excerpt = source.read_text(encoding="utf-8")[:80]
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": "shell-read",
                "type": "command_execution",
                "command": "private command detail",
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "answer",
                "type": "agent_message",
                "text": json.dumps(
                    {
                        "answer_markdown": "## 结论\n\n已核对项目资料。",
                        "tables": [],
                        "charts": [],
                        "citations": [
                            {
                                "filename": "data-analysis-sop.md",
                                "excerpt": excerpt,
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        },
        {"type": "turn.completed", "usage": {}},
    ]

    with pytest.raises(CodexRuntimeError, match="forbidden shell"):
        CodexTurnParser(prepared).parse(
            "\n".join(json.dumps(event, ensure_ascii=False) for event in events)
        )


def test_turn_parser_rejects_unapproved_mcp_tool(tmp_path: Path) -> None:
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, _settings(tmp_path)).prepare()
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": "unapproved",
                "type": "mcp_tool_call",
                "server": "other",
                "tool": "read_private_data",
                "arguments": {},
                "result": _mcp_result({"secret": 1}),
                "status": "completed",
            },
        },
        {"type": "turn.completed", "usage": {}},
    ]

    with pytest.raises(CodexRuntimeError, match="unapproved MCP"):
        CodexTurnParser(prepared).parse(
            "\n".join(json.dumps(event, ensure_ascii=False) for event in events)
        )


def test_turn_parser_fails_closed_on_completed_file_change(tmp_path: Path) -> None:
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, _settings(tmp_path)).prepare()
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": "write-attempt",
                "type": "file_change",
                "status": "completed",
            },
        },
        {"type": "turn.completed", "usage": {}},
    ]

    with pytest.raises(CodexRuntimeError, match="file change"):
        CodexTurnParser(prepared).parse(
            "\n".join(json.dumps(event) for event in events)
        )


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
                "text": _telemetry_answer(),
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
        _knowledge_event("text that is not in the source"),
        {
            "type": "item.completed",
            "item": {
                "id": "answer",
                "type": "agent_message",
                "text": _telemetry_answer(),
            },
        },
        {"type": "turn.completed", "usage": {}},
    ]

    with pytest.raises(CodexRuntimeError, match="Governed document citation"):
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
                                "text": _telemetry_answer(),
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


def test_python_sdk_runner_uses_denied_approvals_and_structured_output(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, settings).prepare()
    calls: dict[str, object] = {}

    class FakeItem:
        def model_dump(self, **kwargs):  # type: ignore[no-untyped-def]
            calls["model_dump_kwargs"] = kwargs
            return {
                "id": "mcp-sdk",
                "type": "mcpToolCall",
                "server": "hvac",
                "tool": "data_quality",
                "arguments": {},
                "result": {
                    "structuredContent": {
                        "result": [{"asset_id": "HP-02", "missing_rows": 12}]
                    }
                },
                "status": "completed",
            }

    class FakeResult:
        status = "completed"
        final_response = _telemetry_answer()
        items = [FakeItem()]
        usage = None

    class FakeTurn:
        def run(self):  # type: ignore[no-untyped-def]
            calls["turn_run"] = True
            return FakeResult()

        def interrupt(self):  # type: ignore[no-untyped-def]
            calls["interrupted"] = True

    class FakeThread:
        def turn(self, prompt, **kwargs):  # type: ignore[no-untyped-def]
            calls["prompt"] = prompt
            calls["turn_kwargs"] = kwargs
            return FakeTurn()

    class FakeCodex:
        def __init__(self, config):  # type: ignore[no-untyped-def]
            calls["config"] = config

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *args):  # type: ignore[no-untyped-def]
            calls["closed"] = True

        def thread_start(self, **kwargs):  # type: ignore[no-untyped-def]
            calls["thread_start_kwargs"] = kwargs
            return FakeThread()

    class FakeConfig:
        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            self.kwargs = kwargs

    class FakeSdk:
        Codex = FakeCodex
        CodexConfig = FakeConfig

        class ApprovalMode:
            deny_all = "deny-all"

    jsonl = run_sdk_turn(
        {
            "codex_bin": str(settings.codex_bin),
            "cwd": str(prepared.workspace_root),
            "prompt": "核对数据质量。",
            "model": settings.model,
            "model_provider": "company",
            "effort": settings.reasoning_effort,
            "output_schema": json.loads(
                prepared.output_schema.read_text(encoding="utf-8")
            ),
        },
        sdk_module=FakeSdk,
    )
    answer = CodexTurnParser(prepared).parse(jsonl)

    assert answer["grounding_status"] == "grounded"
    assert calls["prompt"] == "核对数据质量。"
    assert calls["thread_start_kwargs"] == {
        "approval_mode": "deny-all",
        "cwd": str(prepared.workspace_root),
        "ephemeral": True,
        "model": settings.model,
        "model_provider": "company",
    }
    assert calls["turn_kwargs"] == {
        "approval_mode": "deny-all",
        "effort": settings.reasoning_effort,
        "output_schema": json.loads(prepared.output_schema.read_text(encoding="utf-8")),
    }
    assert calls["model_dump_kwargs"] == {
        "mode": "json",
        "by_alias": True,
        "exclude_none": True,
    }
    assert calls["closed"] is True
    config = calls["config"]
    assert isinstance(config, FakeConfig)
    assert config.kwargs["codex_bin"] == str(settings.codex_bin)
    assert config.kwargs["cwd"] == str(prepared.workspace_root)
    assert config.kwargs["launch_args_override"] == (
        str(settings.codex_bin),
        "app-server",
        "--listen",
        "stdio://",
        "--strict-config",
    )
    assert config.kwargs == {
        "codex_bin": str(settings.codex_bin),
        "launch_args_override": (
            str(settings.codex_bin),
            "app-server",
            "--listen",
            "stdio://",
            "--strict-config",
        ),
        "cwd": str(prepared.workspace_root),
        "experimental_api": False,
    }


def test_python_sdk_runner_launches_worker_with_controlled_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    prepared = CodexWorkspaceBuilder(CORPUS_ROOT, settings).prepare()
    launch = build_codex_launch(settings, prepared)
    monkeypatch.setenv("SHOULD_NOT_LEAK_TO_CODEX", "private")
    calls: dict[str, object] = {}

    def fake_process(argv, **kwargs):  # type: ignore[no-untyped-def]
        calls["argv"] = argv
        calls.update(kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout='{"type":"turn.completed","usage":{}}\n',
            stderr="",
        )

    jsonl = CodexPythonSdkRunner(
        settings,
        process_runner=fake_process,
    ).run(launch, "核对。", 30)

    assert calls["argv"] == [
        str(settings.python_executable),
        "-m",
        "project_copilot.codex_sdk_worker",
    ]
    assert calls["cwd"] == prepared.workspace_root
    assert calls["env"] == launch.env
    assert "SHOULD_NOT_LEAK_TO_CODEX" not in calls["env"]
    request = json.loads(str(calls["input"]))
    assert request["prompt"] == "核对。"
    assert "api_key" not in request
    assert request["model_provider"] == "company"
    assert jsonl == '{"type":"turn.completed","usage":{}}\n'
    assert prepared.events_log.read_text(encoding="utf-8") == jsonl


def test_installed_python_sdk_controls_pinned_app_server_without_real_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_cli_bin = pytest.importorskip("codex_cli_bin")
    captured: list[dict[str, object]] = []
    queued: queue.Queue[str] = queue.Queue()
    final_text = json.dumps({"answer_markdown": "SDK contract passed", "citations": []})
    response_id = "resp-sdk-contract"
    events = [
        {"type": "response.created", "response": {"id": response_id}},
        {
            "type": "response.output_item.done",
            "item": {
                "type": "message",
                "role": "assistant",
                "id": "msg-sdk-contract",
                "content": [{"type": "output_text", "text": final_text}],
            },
        },
        {
            "type": "response.completed",
            "response": {
                "id": response_id,
                "usage": {
                    "input_tokens": 1,
                    "input_tokens_details": None,
                    "output_tokens": 1,
                    "output_tokens_details": None,
                    "total_tokens": 2,
                },
            },
        },
    ]
    queued.put(
        "\n".join(
            f"event: {event['type']}\ndata: {json.dumps(event)}\n" for event in events
        )
        + "\n"
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):  # type: ignore[no-untyped-def]
            return None

        def do_GET(self):  # type: ignore[no-untyped-def]
            body = json.dumps(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "mock-model",
                            "object": "model",
                            "created": 0,
                            "owned_by": "openai",
                        }
                    ],
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # type: ignore[no-untyped-def]
            length = int(self.headers.get("content-length", "0"))
            body = self.rfile.read(length)
            captured.append(
                {
                    "path": self.path,
                    "body": json.loads(body.decode("utf-8")),
                    "authorization": self.headers.get("authorization"),
                }
            )
            payload = queued.get_nowait().encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    host, port = server.server_address
    codex_home = tmp_path / "codex-home"
    workspace = tmp_path / "workspace"
    codex_home.mkdir()
    workspace.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                'model = "mock-model"',
                'approval_policy = "never"',
                'sandbox_mode = "read-only"',
                'model_provider = "company"',
                "",
                "[model_providers.company]",
                'name = "SDK contract mock"',
                f'base_url = "http://{host}:{port}/v1"',
                'wire_api = "responses"',
                "request_max_retries = 0",
                "stream_max_retries = 0",
                "",
                "[mcp_servers.hvac]",
                f"command = {json.dumps(sys.executable)}",
                'args = ["-m", "project_copilot.codex_mcp_server"]',
                "required = true",
                'enabled_tools = ["inspect_configuration_change_effect"]',
                "",
                "[mcp_servers.hvac.env]",
                f"PROJECT_COPILOT_MCP_DATABASE = {json.dumps(str(CORPUS_ROOT / 'datasets' / 'hvac_bakeoff.duckdb'))}",
                f"PROJECT_COPILOT_MCP_CORPUS = {json.dumps(str(CORPUS_ROOT))}",
                f"PYTHONPATH = {json.dumps(str(REPOSITORY_ROOT / 'src'))}",
                'POLARS_SKIP_CPU_CHECK = "1"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CODEX_APP_SERVER_DISABLE_MANAGED_CONFIG", "1")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("RUST_LOG", "warn")
    try:
        try:
            jsonl = run_sdk_turn(
                {
                    "codex_bin": str(codex_cli_bin.bundled_codex_path()),
                    "cwd": str(workspace),
                    "prompt": "Return the required JSON object.",
                    "model": "mock-model",
                    "model_provider": "company",
                    "effort": "high",
                    "output_schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["answer_markdown", "citations"],
                        "properties": {
                            "answer_markdown": {"type": "string"},
                            "citations": {"type": "array"},
                        },
                    },
                }
            )
        except Exception as exc:
            paths = [str(item["path"]) for item in captured]
            raise AssertionError(
                f"SDK mock contract failed after {len(captured)} requests: {paths}"
            ) from exc
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    sdk_events = [json.loads(line) for line in jsonl.splitlines()]
    assert sdk_events[-1]["type"] == "turn.completed"
    assert any(
        event.get("item", {}).get("text") == final_text
        for event in sdk_events
        if event["type"] == "item.completed"
    )
    assert len(captured) == 1
    assert str(captured[0]["path"]).endswith("/v1/responses")
    assert captured[0]["authorization"] is None
    assert "Return the required JSON object." in json.dumps(captured[0]["body"])
    tool_descriptors = [
        {"type": tool.get("type"), "name": tool.get("name")}
        for tool in captured[0]["body"].get("tools", [])
        if isinstance(tool, dict)
    ]
    assert any(
        tool.get("name") in {"list_mcp_tools", "list_mcp_resources"}
        for tool in tool_descriptors
    ), tool_descriptors


def test_installed_python_sdk_strict_config_rejects_unknown_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openai_codex = pytest.importorskip("openai_codex")
    codex_cli_bin = pytest.importorskip("codex_cli_bin")
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        'model = "mock-model"\nunknown_project_copilot_field = true\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CODEX_APP_SERVER_DISABLE_MANAGED_CONFIG", "1")
    codex_bin = str(codex_cli_bin.bundled_codex_path())

    with pytest.raises(Exception, match="unknown_project_copilot_field"):
        openai_codex.Codex(
            openai_codex.CodexConfig(
                codex_bin=codex_bin,
                launch_args_override=(
                    codex_bin,
                    "app-server",
                    "--listen",
                    "stdio://",
                    "--strict-config",
                ),
                cwd=str(tmp_path),
                experimental_api=False,
            )
        )


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
            AssertionError(
                "Codex runtime must not initialize the legacy embedding stack"
            )
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
    graph = client.get("/api/direction/graph")
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
    assert 'data-testid="project-map"' in page.text
    assert "/static/vendor/cytoscape-3.34.0.min.js" in page.text
    assert graph.status_code == 200
    assert any(
        node["label"] == "current-unit-configuration.md"
        for node in graph.json()["nodes"]
    )
    graph_labels = {node["label"] for node in graph.json()["nodes"]}
    assert {
        "telemetry.csv",
        "config_history.csv",
        "assets.csv",
        "point_aliases.csv",
    } <= graph_labels
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


def test_direction_api_redacts_internal_paths_from_all_public_strings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARS_SKIP_CPU_CHECK", "1")
    from project_copilot.web import create_app

    class FakeRuntime:
        model = "gpt-test"
        workspace_name = "Agentic HVAC Bakeoff"
        source_count = 11

        async def answer_async(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            return {
                "mode": "combined",
                "demo_mode": False,
                "model_backed": True,
                "answer_markdown": (
                    "## Result\n\nSee [internal](docs/private.md), "
                    "C:\\private\\secret.md and /workspace/private/index.sqlite."
                ),
                "tables": [
                    {
                        "title": "company/runtime/state.json",
                        "columns": ["src/private.py", "value"],
                        "rows": [["D:\\secret\\row.csv", 1]],
                    }
                ],
                "charts": [
                    {
                        "kind": "bar",
                        "title": "datasets/private.csv",
                        "unit": "runtime/unit.txt",
                        "points": [
                            {
                                "label": "E:\\private\\label.csv",
                                "value": 1,
                                "series": "configuration/private.md",
                            }
                        ],
                    }
                ],
                "citations": [
                    {
                        "filename": "meeting.md",
                        "excerpt": "safe evidence",
                        "location": "C:\\private\\secret.md",
                        "source_role": "private/secret-role.txt",
                        "source_status": "app/internal-status.txt",
                    }
                ],
                "activities": [],
                "clarification": False,
                "refused": False,
                "grounding_status": "grounded",
            }

    monkeypatch.setenv("PROJECT_COPILOT_AGENT_RUNTIME", "codex")
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

    response = client.post(
        "/api/direction/query",
        json={"question": "inspect", "history": []},
        headers={
            "host": "127.0.0.1",
            "origin": "http://127.0.0.1",
            "X-Project-Copilot": "1",
        },
    )

    assert response.status_code == 200
    serialized = response.text.replace("\\\\", "/").casefold()
    for forbidden in (
        "docs/private.md",
        "c:/private/secret.md",
        "/workspace/private/index.sqlite",
        "company/runtime/state.json",
        "src/private.py",
        "d:/secret/row.csv",
        "datasets/private.csv",
        "runtime/unit.txt",
        "e:/private/label.csv",
        "configuration/private.md",
        "private/secret-role.txt",
        "app/internal-status.txt",
    ):
        assert forbidden not in serialized
    assert "meeting.md" in response.text


def test_single_chat_frontend_keeps_named_workflows_without_raw_thread_ids() -> None:
    script = (
        REPOSITORY_ROOT / "src" / "project_copilot" / "static" / "direction.js"
    ).read_text(encoding="utf-8")
    template = (
        REPOSITORY_ROOT / "src" / "project_copilot" / "templates" / "direction.html"
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
            f"import json,sys; sys.stdin.read(); print(json.dumps({event!r}))",
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


def test_mcp_typed_operations_reuse_governed_hvac_toolbox() -> None:
    knowledge = run_typed_operation(
        "search_project_knowledge",
        CORPUS_ROOT,
        query="HP-02 CR-017 approved supply-air setpoint change",
    )
    effect = run_typed_operation(
        "inspect_configuration_change_effect",
        CORPUS_ROOT,
        asset_id="HP-02",
        parameter_name="supply_air_sp_c",
    )

    assert knowledge["citations"]
    assert effect["tables"][0]["rows"][0][2] == 12.0
    assert effect["tables"][0]["rows"][1][2] == 10.0
    assert effect["charts"][0]["points"] == [
        {"label": "变更前", "value": 36.0},
        {"label": "变更后", "value": 40.0},
    ]
    with pytest.raises(ValueError, match="Unsupported governed typed operation"):
        run_typed_operation("arbitrary_sql", CORPUS_ROOT)


def test_official_mcp_stdio_exposes_and_executes_typed_read_only_tools() -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    stages: list[str] = []

    async def probe() -> tuple[dict[str, dict[str, object]], dict[str, object]]:
        env = os.environ.copy()
        env.update(
            {
                "POLARS_SKIP_CPU_CHECK": "1",
                "PROJECT_COPILOT_MCP_DATABASE": str(
                    CORPUS_ROOT / "datasets" / "hvac_bakeoff.duckdb"
                ),
                "PROJECT_COPILOT_MCP_CORPUS": str(CORPUS_ROOT),
                "PYTHONPATH": str(REPOSITORY_ROOT / "src"),
            }
        )
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "project_copilot.codex_mcp_server"],
            env=env,
        )
        stages.append("connect")
        async with stdio_client(server) as streams:
            async with ClientSession(*streams) as session:
                stages.append("initialize")
                await session.initialize()
                stages.append("list-tools")
                listed = await session.list_tools()
                stages.append("call-tool")
                result = await session.call_tool(
                    "inspect_configuration_change_effect",
                    {
                        "asset_id": "HP-02",
                        "parameter_name": "supply_air_sp_c",
                    },
                )
                stages.append("close")
        payload = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        schemas = {
            tool.name: tool.model_dump(mode="json", by_alias=True, exclude_none=True)[
                "inputSchema"
            ]
            for tool in listed.tools
        }
        return schemas, payload

    try:
        schemas, payload = asyncio.run(asyncio.wait_for(probe(), timeout=45))
    except TimeoutError:
        pytest.fail(f"MCP STDIO timed out during stages: {stages}")

    assert {
        "schema",
        "data_quality",
        "cop_ranking",
        "search_project_knowledge",
        "query_hvac_database",
        "inspect_hvac_snapshot",
        "inspect_configuration_history",
        "inspect_configuration_change_effect",
        "inspect_metric_extreme",
    } <= set(schemas)
    query_schema = schemas["query_hvac_database"]
    assert query_schema["required"] == ["sql"]
    assert query_schema["properties"]["chart_kind"]["enum"] == [
        "none",
        "line",
        "bar",
    ]
    snapshot_schema = schemas["inspect_hvac_snapshot"]
    assert snapshot_schema["properties"]["inspection"]["enum"] == [
        "data_quality",
        "control_events",
        "alarm_events",
    ]
    assert "defrost" in snapshot_schema["properties"]["event_type"]["enum"]
    assert (
        "compressor_feedback_mismatch"
        in snapshot_schema["properties"]["event_types"]["anyOf"][0]["items"]["enum"]
    )
    extreme_schema = schemas["inspect_metric_extreme"]
    assert extreme_schema["properties"]["direction"]["enum"] == [
        "minimum",
        "maximum",
    ]
    assert "cop" in extreme_schema["properties"]["metric"]["enum"]
    effect_schema = schemas["inspect_configuration_change_effect"]
    assert effect_schema["properties"]["parameter_name"]["const"] == "supply_air_sp_c"
    assert payload["isError"] is False
    assert payload["structuredContent"]["tables"][0]["rows"][0][2] == 12.0


def test_codex_windows_bootstrap_pins_official_runtime_without_credentials() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "bootstrap-codex-runtime.ps1").read_text(
        encoding="utf-8"
    )
    pyproject = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "requirements.codex.lock" in script
    assert "codex_cli_bin.bundled_codex_path()" in script
    assert "openai-codex==0.144.4" in pyproject
    assert "@openai/codex" not in script
    assert "duckdb_cli-windows-amd64.zip" not in script
    assert "OPENAI_API_KEY" not in script


def test_codex_windows_run_wrapper_is_explicit_and_loopback_only() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "run-codex.ps1").read_text(encoding="utf-8")

    assert "PROJECT_COPILOT_AGENT_RUNTIME" in script
    assert "PROJECT_COPILOT_ACK_CODEX_SWITCH" in script
    assert "PROJECT_COPILOT_CODEX_CONFIG" in script
    assert 'PROJECT_COPILOT_CODEX_TRANSPORT = "python-sdk"' in script
    assert '--host", "127.0.0.1"' in script
    assert "PROJECT_COPILOT_CODEX_BIN" in script
    assert "codex_cli_bin.bundled_codex_path()" in script
    assert "PROJECT_COPILOT_GOVERNED_DUCKDB_CLI" not in script
    assert "project-copilot-codex-preflight.exe" in script
    assert script.index("project-copilot-codex-preflight.exe") < script.index(
        "& $exe @arguments"
    )
    pyproject = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert (
        'project-copilot-codex-preflight = "project_copilot.codex_preflight:main"'
        in pyproject
    )
