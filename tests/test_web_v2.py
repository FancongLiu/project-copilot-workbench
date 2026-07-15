import json
import ssl
from pathlib import Path

import certifi
import pytest
from fastapi.testclient import TestClient
from haystack.components.generators.chat import OpenAIChatGenerator

from project_copilot.web import (
    _build_chat_generator,
    _build_reranker,
    _category_for_path,
    create_app,
)


HEADERS = {"X-Project-Copilot": "1"}


def test_sop_directory_wins_over_control_keyword_in_filename() -> None:
    assert _category_for_path(Path("sops/sop-change-control.md")) == "SOP"


def test_web_workspace_upload_inventory_and_primary_agent_flow(tmp_path: Path) -> None:
    client = TestClient(create_app(runtime_root=tmp_path / "runtime"))

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert 'data-testid="workspace-panel"' in dashboard.text
    assert 'data-testid="copilot-panel"' in dashboard.text

    created = client.post(
        "/api/workspaces",
        json={
            "project_id": "cooling-upgrade",
            "display_name": "Cooling Upgrade",
        },
        headers=HEADERS,
    )
    assert created.status_code == 201
    assert (
        client.post(
            "/api/workspaces/cooling-upgrade/activate", headers=HEADERS
        ).status_code
        == 200
    )

    uploaded = client.post(
        "/api/workspaces/cooling-upgrade/sources",
        data={"category": "decision"},
        files={
            "files": (
                "meeting.md",
                b"Decision D-014 approved a 6 C chilled-water supply setpoint.",
                "text/markdown",
            )
        },
        headers=HEADERS,
    )
    assert uploaded.status_code == 201

    inventory = client.get("/api/workspaces/cooling-upgrade/sources")
    assert inventory.json()[0]["filename"] == "meeting.md"
    assert inventory.json()[0]["status"] == "indexed"

    answer = client.post(
        "/api/workspaces/cooling-upgrade/copilot/query",
        json={"question": "What setpoint did the project approve?"},
        headers=HEADERS,
    )
    assert answer.status_code == 200
    assert answer.json()["project_id"] == "cooling-upgrade"
    assert "6 C" in answer.json()["answer"]
    assert answer.json()["citations"][0]["source"] == "meeting.md"
    assert answer.json()["activities"][0]["tool"] == "configuration_lookup"


def test_web_reindex_and_source_delete_are_auditable(tmp_path: Path) -> None:
    client = TestClient(create_app(runtime_root=tmp_path / "runtime"))
    project_id = client.get("/api/health").json()["project_id"]

    sources = client.get(f"/api/workspaces/{project_id}/sources").json()
    assert sources
    assert (
        client.post(f"/api/workspaces/{project_id}/reindex", headers=HEADERS).json()[
            "indexed_chunks"
        ]
        > 0
    )

    source_id = sources[0]["source_id"]
    deleted = client.delete(
        f"/api/workspaces/{project_id}/sources/{source_id}", headers=HEADERS
    )
    assert deleted.status_code == 204
    remaining_ids = {
        item["source_id"]
        for item in client.get(f"/api/workspaces/{project_id}/sources").json()
    }
    assert source_id not in remaining_ids


def test_web_combines_defrost_rule_citations_with_bounded_replay(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(runtime_root=tmp_path / "runtime-defrost"))

    project_id = client.get("/api/health").json()["project_id"]
    answer = client.post(
        f"/api/workspaces/{project_id}/copilot/query",
        json={
            "question": (
                "Did HP-01 follow the defrost control logic from "
                "2026-07-15T15:59:00 to 2026-07-15T16:08:00?"
            )
        },
        headers=HEADERS,
    )

    assert answer.status_code == 200
    payload = answer.json()
    assert "non-compliant" in payload["answer"]
    assert {item["tool"] for item in payload["activities"]} == {
        "configuration_lookup",
        "defrost_diagnostics",
    }
    assert any(
        item["source"] == "defrost-control-sequence.md" for item in payload["citations"]
    )


def test_web_rejects_self_declared_event_reconstruction_without_fake_verdict(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(runtime_root=tmp_path / "runtime-disabled-scope"))
    project_id = "disabled-defrost-scope"
    assert (
        client.post(
            "/api/workspaces",
            json={"project_id": project_id, "display_name": "Disabled Defrost Scope"},
            headers=HEADERS,
        ).status_code
        == 201
    )
    rules = {
        "schema_version": "1.0",
        "rule_id": "UNAPPROVED-RULE",
        "version": "1",
        "asset_id": "HP-01",
        "controller_model": "FAKE-CTRL",
        "firmware_version": "FAKE-1",
        "compliance_scope": "event_reconstruction",
        "timezone": "Asia/Shanghai",
        "source_file": "unapproved.md",
        "source_section": "unapproved",
        "sample_interval_seconds": 10,
        "required_resolution_seconds": 10,
        "gap_tolerance_seconds": 2,
        "candidate_outdoor_temp_c_max": 5,
        "candidate_coil_temp_c_max": 0,
        "candidate_min_seconds": 20,
        "initiation_max_delay_seconds": 60,
        "defrost_max_seconds": 120,
        "exit_coil_temp_c_min": 5,
        "recovery_min_seconds": 10,
        "defrost_fan_expected": 0,
        "defrost_reversing_valve_expected": 1,
    }
    context = {
        "schema_version": "1.0",
        "asset_id": "HP-01",
        "controller_model": "FAKE-CTRL",
        "firmware_version": "FAKE-1",
        "source_file": "fake-register.md",
        "source_section": "HP-01",
    }
    telemetry = (
        "timestamp,asset_id,mode,outdoor_temp_c,outdoor_coil_temp_c,"
        "suction_pressure_kpa,discharge_pressure_kpa,suction_temp_c,"
        "discharge_temp_c,superheat_k,subcooling_k,compressor_command,"
        "outdoor_fan_command,reversing_valve_command,defrost_command,"
        "alarm_code,data_quality\n"
        "2026-07-15T04:00:00,HP-01,heating,2,-2,420,1700,2,76,4,5,1,1,0,0,,good\n"
    )
    for filename, content, category, media_type in (
        (
            "defrost-rules.json",
            json.dumps(rules).encode(),
            "configuration",
            "application/json",
        ),
        (
            "defrost-asset-context.json",
            json.dumps(context).encode(),
            "configuration",
            "application/json",
        ),
        ("defrost_telemetry.csv", telemetry.encode(), "dataset", "text/csv"),
    ):
        response = client.post(
            f"/api/workspaces/{project_id}/sources",
            data={"category": category},
            files={"files": (filename, content, media_type)},
            headers=HEADERS,
        )
        assert response.status_code == 201

    answer = client.post(
        f"/api/workspaces/{project_id}/copilot/query",
        json={
            "question": (
                "Did HP-01 follow the defrost control logic from "
                "2026-07-15T04:00:00 to 2026-07-15T04:01:00?"
            )
        },
        headers=HEADERS,
    )

    assert answer.status_code == 200
    payload = answer.json()
    assert payload["refused"] is True
    assert payload["clarification"] is True
    assert payload["diagnostic"] is None
    assert "event_reconstruction and oem_exact are disabled" in payload["answer"]
    assert "verdict: None" not in payload["answer"]


def test_copilot_query_is_bound_to_route_workspace_not_global_active_state(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(runtime_root=tmp_path / "runtime-isolation"))
    for project_id, display_name, fact in (
        ("project-alpha", "Project Alpha", "Alpha decision is ALPHA-ONLY."),
        ("project-beta", "Project Beta", "Beta decision is BETA-ONLY."),
    ):
        assert (
            client.post(
                "/api/workspaces",
                json={"project_id": project_id, "display_name": display_name},
                headers=HEADERS,
            ).status_code
            == 201
        )
        assert (
            client.post(
                f"/api/workspaces/{project_id}/sources",
                data={"category": "decision"},
                files={"files": (f"{project_id}.md", fact.encode(), "text/markdown")},
                headers=HEADERS,
            ).status_code
            == 201
        )

    assert (
        client.post(
            "/api/workspaces/project-beta/activate", headers=HEADERS
        ).status_code
        == 200
    )
    response = client.post(
        "/api/workspaces/project-alpha/copilot/query",
        json={"question": "What is the project decision?", "request_id": "req-alpha"},
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["project_id"] == "project-alpha"
    assert response.json()["request_id"] == "req-alpha"
    assert response.json()["citations"][0]["source"] == "project-alpha.md"
    assert (
        client.post(
            "/api/copilot/query",
            json={"question": "This unsafe global route must not exist."},
            headers=HEADERS,
        ).status_code
        == 404
    )


def test_analytics_summary_is_workspace_scoped_and_clears_when_dataset_missing(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(runtime_root=tmp_path / "runtime-analytics"))
    for project_id, power in (("data-alpha", 10), ("data-beta", 80)):
        client.post(
            "/api/workspaces",
            json={"project_id": project_id, "display_name": project_id},
            headers=HEADERS,
        )
        csv = (
            "timestamp,supply_temp_c,return_temp_c,power_kw,cooling_kw,load_pct\n"
            f"2026-07-15T00:00:00,6,12,{power},{power * 4},50\n"
            f"2026-07-15T00:01:00,6,12,{power},{power * 4},50\n"
        )
        client.post(
            f"/api/workspaces/{project_id}/sources",
            data={"category": "dataset"},
            files={"files": ("telemetry.csv", csv.encode(), "text/csv")},
            headers=HEADERS,
        )

    alpha = client.get("/api/workspaces/data-alpha/analytics/summary").json()
    beta = client.get("/api/workspaces/data-beta/analytics/summary").json()
    missing = client.post(
        "/api/workspaces",
        json={"project_id": "data-empty", "display_name": "Empty"},
        headers=HEADERS,
    )
    assert missing.status_code == 201
    empty = client.get("/api/workspaces/data-empty/analytics/summary").json()

    assert alpha["project_id"] == "data-alpha"
    assert alpha["dataset_filename"] == "telemetry.csv"
    assert alpha["available"] is True
    assert alpha["average_power_kw"] == 10
    assert beta["average_power_kw"] == 80
    assert empty == {
        "project_id": "data-empty",
        "available": False,
        "dataset_filename": None,
        "row_count": 0,
        "average_power_kw": None,
        "average_delta_t_c": None,
        "average_cop": None,
    }


def test_company_mode_builds_haystack_openai_compatible_generator(monkeypatch) -> None:
    monkeypatch.setenv("PROJECT_COPILOT_MODEL_MODE", "company")
    monkeypatch.setenv("PROJECT_COPILOT_OPENAI_BASE_URL", "https://ai.internal/v1")
    monkeypatch.setenv("PROJECT_COPILOT_OPENAI_API_KEY", "placeholder")
    monkeypatch.setenv("PROJECT_COPILOT_OPENAI_MODEL", "company-model")
    monkeypatch.setenv("PROJECT_COPILOT_ALLOWED_HOSTS", "ai.internal")

    generator, mode = _build_chat_generator()

    assert isinstance(generator, OpenAIChatGenerator)
    assert generator.api_base_url == "https://ai.internal/v1"
    assert generator.model == "company-model"
    assert mode == "company-openai-compatible"


def test_company_mode_uses_ssl_context_for_internal_ca(monkeypatch) -> None:
    monkeypatch.setenv("PROJECT_COPILOT_MODEL_MODE", "company")
    monkeypatch.setenv("PROJECT_COPILOT_OPENAI_BASE_URL", "https://ai.internal/v1")
    monkeypatch.setenv("PROJECT_COPILOT_OPENAI_API_KEY", "placeholder")
    monkeypatch.setenv("PROJECT_COPILOT_OPENAI_MODEL", "company-model")
    monkeypatch.setenv("PROJECT_COPILOT_ALLOWED_HOSTS", "ai.internal")
    monkeypatch.setenv("PROJECT_COPILOT_CA_BUNDLE", certifi.where())

    generator, _ = _build_chat_generator()

    assert isinstance(generator.http_client_kwargs["verify"], ssl.SSLContext)


def test_local_reranker_requires_explicit_approval(monkeypatch, tmp_path: Path) -> None:
    model_path = tmp_path / "reranker"
    model_path.mkdir()
    monkeypatch.setenv("PROJECT_COPILOT_RERANKER_MODEL_PATH", str(model_path))
    monkeypatch.delenv("PROJECT_COPILOT_ACK_RERANKER_APPROVED", raising=False)

    with pytest.raises(RuntimeError, match="ACK_RERANKER_APPROVED"):
        _build_reranker()


def test_local_reranker_builder_uses_approved_local_path(
    monkeypatch, tmp_path: Path
) -> None:
    model_path = tmp_path / "reranker"
    model_path.mkdir()
    marker = object()
    monkeypatch.setenv("PROJECT_COPILOT_RERANKER_MODEL_PATH", str(model_path))
    monkeypatch.setenv("PROJECT_COPILOT_ACK_RERANKER_APPROVED", "true")
    monkeypatch.setattr(
        "project_copilot.web.SentenceTransformersReranker",
        lambda selected: marker if Path(selected) == model_path else None,
    )

    assert _build_reranker() is marker
