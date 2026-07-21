from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from project_copilot.web import create_app


def build_project(root: Path, *, allow_approved_provider: bool = False) -> Path:
    docs = root / "docs" / "source"
    data = root / "datasets" / "raw"
    docs.mkdir(parents=True)
    data.mkdir(parents=True)
    (root / "project.yaml").write_text(
        f"""schema_version: "0.1"
project_id: synthetic-hvac-demo
display_name: Synthetic HVAC Plant
documents:
  root: docs/source
datasets:
  root: datasets/raw
security:
  allow_network: false
  allow_nl2sql: false
  allow_approved_provider: {str(allow_approved_provider).lower()}
""",
        encoding="utf-8",
    )
    (docs / "control.md").write_text(
        "冷冻水供水温度设定值为 7 摄氏度，回水温度通常为 12 摄氏度。",
        encoding="utf-8",
    )
    (data / "telemetry.csv").write_text(
        """timestamp,supply_temp_c,return_temp_c,power_kw,cooling_kw,load_pct
2026-07-01T08:00:00,7.0,12.0,100.0,400.0,55.0
2026-07-01T09:00:00,7.2,12.7,110.0,462.0,62.0
2026-07-01T10:00:00,7.5,13.5,120.0,540.0,70.0
""",
        encoding="utf-8",
    )
    return root


def test_web_app_serves_single_chat_homepage(tmp_path: Path) -> None:
    app = create_app(
        project_root=build_project(tmp_path / "project"),
        runtime_root=tmp_path / "runtime",
    )
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Synthetic HVAC Plant" in response.text
    assert 'data-testid="direction-chat"' in response.text
    assert 'data-testid="active-project"' in response.text
    assert 'data-testid="knowledge-panel"' not in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_web_app_exposes_bounded_knowledge_and_analysis_apis(tmp_path: Path) -> None:
    app = create_app(
        project_root=build_project(tmp_path / "project"),
        runtime_root=tmp_path / "runtime",
    )
    client = TestClient(app)

    health = client.get("/api/health")
    knowledge = client.post(
        "/api/knowledge/query",
        json={"question": "供水温度设定值是多少？"},
        headers={"X-Project-Copilot": "1"},
    )
    analysis = client.post(
        "/api/analytics/analyze",
        json={"question": "哪个时刻负荷最高？"},
        headers={"X-Project-Copilot": "1"},
    )

    assert health.json() == {
        "status": "ok",
        "project_id": "synthetic-hvac-demo",
        "agent_runtime": "haystack",
        "knowledge_provider": "haystack-local",
        "network_allowed": False,
        "nl2sql_allowed": False,
        "egress_mode": "loopback-only",
        "egress_channels": [],
        "egress_detail": {
            "chat": "disabled",
            "embedding": "disabled",
            "knowledge": "local",
        },
        "downstream_approval_acknowledged": False,
    }
    assert knowledge.status_code == 200
    assert knowledge.json()["citations"][0]["source"] == "control.md"
    assert analysis.status_code == 200
    assert analysis.json()["intent"] == "peak-load"
    assert analysis.json()["rows"][0]["peak_load_pct"] == 70.0


def test_bootstrap_analytics_uses_the_current_dataset_hash(tmp_path: Path) -> None:
    project = build_project(tmp_path / "project-changing-bootstrap")
    runtime = tmp_path / "runtime-changing-bootstrap"
    first = TestClient(create_app(project_root=project, runtime_root=runtime))
    first_peak = first.post(
        "/api/analytics/analyze",
        json={"question": "peak load"},
        headers={"X-Project-Copilot": "1"},
    )
    assert first_peak.json()["rows"][0]["peak_load_pct"] == 70.0

    telemetry = project / "datasets" / "raw" / "telemetry.csv"
    telemetry.write_text(
        telemetry.read_text(encoding="utf-8").replace(
            "2026-07-01T10:00:00,7.5,13.5,120.0,540.0,70.0",
            "2026-07-01T10:00:00,7.5,13.5,120.0,540.0,99.0",
        ),
        encoding="utf-8",
    )
    restarted = TestClient(create_app(project_root=project, runtime_root=runtime))
    second_peak = restarted.post(
        "/api/analytics/analyze",
        json={"question": "peak load"},
        headers={"X-Project-Copilot": "1"},
    )

    assert second_peak.json()["rows"][0]["peak_load_pct"] == 99.0


def test_repository_synthetic_example_starts_without_external_services(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(runtime_root=tmp_path / "runtime"))

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["project_id"] == "synthetic-hvac-demo"
    assert response.json()["network_allowed"] is False


def test_health_identifies_anythingllm_without_exposing_provider_jargon_in_chat(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("PROJECT_COPILOT_KNOWLEDGE_PROVIDER", "anythingllm")
    monkeypatch.setenv("ANYTHINGLLM_BASE_URL", "https://anythingllm.internal/api")
    monkeypatch.setenv("ANYTHINGLLM_API_KEY", "placeholder")
    monkeypatch.setenv("ANYTHINGLLM_WORKSPACE_SLUG", "synthetic-hvac")
    monkeypatch.setenv("PROJECT_COPILOT_ALLOWED_HOSTS", "anythingllm.internal")
    monkeypatch.setenv("PROJECT_COPILOT_ACK_DOWNSTREAM_APPROVED", "true")
    client = TestClient(
        create_app(
            project_root=build_project(
                tmp_path / "project", allow_approved_provider=True
            ),
            runtime_root=tmp_path / "runtime",
        )
    )

    response = client.get("/")
    health = client.get("/api/health")

    assert response.status_code == 200
    assert "AnythingLLM query" not in response.text
    assert health.json()["knowledge_provider"] == "anythingllm-query"


def test_web_app_blocks_untrusted_hosts_and_cross_site_posts(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            project_root=build_project(tmp_path / "project"),
            runtime_root=tmp_path / "runtime",
        )
    )

    assert client.get("/", headers={"Host": "attacker.example"}).status_code == 400
    assert (
        client.post(
            "/api/knowledge/query",
            json={"question": "供水温度设定值是多少？"},
        ).status_code
        == 403
    )


def test_web_app_rejects_symlinked_dataset(tmp_path: Path) -> None:
    project = build_project(tmp_path / "project")
    outside = tmp_path / "outside.csv"
    outside.write_text(
        "timestamp,supply_temp_c,return_temp_c,power_kw,cooling_kw,load_pct\n",
        encoding="utf-8",
    )
    dataset = project / "datasets" / "raw" / "telemetry.csv"
    dataset.unlink()
    try:
        dataset.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic links are unavailable in this Windows environment")

    with pytest.raises(RuntimeError, match="symbolic link"):
        create_app(project_root=project, runtime_root=tmp_path / "runtime")


def test_web_app_checks_dataset_symlink_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = build_project(tmp_path / "project")
    original_is_symlink = Path.is_symlink

    def report_dataset_symlink(path: Path) -> bool:
        return path.name == "telemetry.csv" or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", report_dataset_symlink)

    with pytest.raises(RuntimeError, match="symbolic link"):
        create_app(project_root=project, runtime_root=tmp_path / "runtime")
