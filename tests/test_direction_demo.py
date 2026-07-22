from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from project_copilot.ingestion import ImportedFile, ParsedDocumentChunk
from project_copilot.web import create_app


HEADERS = {"Origin": "http://testserver", "X-Project-Copilot": "1"}


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(runtime_root=tmp_path / "runtime"))


def test_direction_page_is_one_plain_chat_without_defrost_navigation(
    tmp_path: Path,
) -> None:
    page = _client(tmp_path).get("/direction")

    assert page.status_code == 200
    assert 'data-testid="direction-chat"' in page.text
    assert page.text.count('data-testid="direction-chat"') == 1
    assert "Ask project" not in page.text
    assert "Telemetry" not in page.text
    assert "Check a defrost sequence" not in page.text
    assert "离线方向演示" in page.text
    assert "本地私有索引" in page.text
    assert 'href="/workbench"' not in page.text
    assert 'data-testid="project-map"' in page.text
    assert 'id="project-map-expand"' in page.text
    assert 'id="project-map-close"' in page.text
    assert 'id="direction-files"' in page.text
    assert "multiple" in page.text
    assert "/static/vendor/cytoscape-3.34.0.min.js" in page.text
    assert "data-workspace-name=" in page.text
    assert "data-source-count=" in page.text
    assert 'data-testid="active-project"' in page.text
    assert 'data-testid="active-source-count"' in page.text


def test_offline_root_keeps_the_same_single_chat_and_retires_legacy_workbench(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    homepage = client.get("/")
    workbench = client.get("/workbench", follow_redirects=False)

    assert homepage.status_code == 200
    assert homepage.text.count('data-testid="direction-chat"') == 1
    assert 'data-testid="workspace-panel"' not in homepage.text
    assert workbench.status_code == 307
    assert workbench.headers["location"] == "/"


def test_four_architecture_routes_share_one_chat_backend_and_distinct_shells(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    overview = client.get("/versions")
    assert overview.status_code == 200
    for architecture in ("baseline", "conversation", "evidence", "canvas"):
        assert f"/versions/{architecture}" in overview.text
        page = client.get(f"/versions/{architecture}")
        assert page.status_code == 200
        assert page.text.count('data-testid="direction-chat"') == 1
        assert f'data-architecture="{architecture}"' in page.text
        assert 'data-testid="project-map"' not in page.text
        assert "/static/vendor/cytoscape-3.34.0.min.js" not in page.text

    conversation = client.get("/versions/conversation").text
    evidence = client.get("/versions/evidence").text
    canvas = client.get("/versions/canvas").text

    assert 'data-testid="prompt-queue"' in conversation
    assert 'data-testid="evidence-workbench"' in evidence
    assert 'data-testid="artifact-canvas"' in canvas


def test_root_remains_the_frozen_baseline_architecture(tmp_path: Path) -> None:
    page = _client(tmp_path).get("/")

    assert 'data-architecture="baseline"' in page.text
    assert 'data-testid="prompt-queue"' not in page.text
    assert 'data-testid="evidence-workbench"' not in page.text
    assert 'data-testid="artifact-canvas"' not in page.text


def test_chat_upload_indexes_files_and_preserves_original_filename(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    original_filename = "HP03_\u63a7\u5236\u903b\u8f91\u8bf4\u660e\u4e66.md"

    response = client.post(
        "/api/direction/sources",
        files={
            "files": (
                original_filename,
                "# HP-03\n\n\u9664\u971c\u7ed3\u675f\u9700\u540c\u65f6\u6ee1\u8db3\u76d8\u7ba1\u6e29\u5ea6\u548c\u6700\u77ed\u8fd0\u884c\u65f6\u95f4\u3002".encode(),
                "text/markdown",
            )
        },
        headers=HEADERS,
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["files"][0]["filename"] == original_filename
    assert payload["files"][0]["status"] == "indexed"
    assert "source_id" not in payload["files"][0]

    project_id = client.get("/api/health").json()["project_id"]
    inventory = client.get(f"/api/workspaces/{project_id}/sources").json()
    assert original_filename in {item["filename"] for item in inventory}


def test_direction_project_graph_uses_friendly_names_without_private_paths(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    response = client.get("/api/direction/graph")

    assert response.status_code == 200
    payload = response.json()
    assert payload["nodes"]
    assert payload["edges"]
    assert any(node["kind"] == "file" for node in payload["nodes"])
    assert any(node["label"] == "telemetry.csv" for node in payload["nodes"])
    serialized = response.text.casefold()
    assert "source_id" not in serialized
    assert "absolute_path" not in serialized
    assert str(tmp_path).casefold().replace("\\", "/") not in serialized.replace(
        "\\", "/"
    )


def test_project_graph_is_directory_flow_with_original_file_names(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    project_id = client.get("/api/health").json()["project_id"]
    client.app.state.indexer.replace_files(
        project_id,
        [
            ImportedFile(
                filename="safe-a.md",
                original_filename="现场会议纪要.md",
                source_location="projects/alpha/meetings/现场会议纪要.md",
                category="meeting",
                content="MEETING-111".encode(),
            ),
            ImportedFile(
                filename="safe-b.pdf",
                original_filename="机组配置表.pdf",
                source_location="projects/alpha/config/机组配置表.pdf",
                category="configuration",
                content=b"fixture",
            ),
        ],
    )

    payload = client.get("/api/direction/graph").json()
    nodes = payload["nodes"]
    labels = {node["label"] for node in nodes}

    assert payload["layout"] == "directory-flow"
    assert payload["summarized"] is False
    assert {"projects", "alpha", "meetings", "config"} <= labels
    assert "现场会议纪要.md" in labels
    assert "机组配置表.pdf" in labels
    assert "safe-a.md" not in labels
    assert "safe-b.pdf" not in labels
    assert any(node["kind"] == "folder" for node in nodes)
    assert any(
        node.get("location") == "projects/alpha/meetings/现场会议纪要.md"
        for node in nodes
    )


def test_direction_layout_prioritizes_chat_and_optional_evidence_paths() -> None:
    root = Path(__file__).resolve().parents[1]
    css = (root / "src/project_copilot/static/direction.css").read_text(
        encoding="utf-8"
    )
    script = (root / "src/project_copilot/static/direction.js").read_text(
        encoding="utf-8"
    )

    assert "width: min(1440px" in css
    assert ".direction-header" in css and "padding: 10px 18px" in css
    assert ".evidence-path" in css
    assert "function renderEvidencePath" in script


def test_uploaded_file_is_immediately_searchable_by_direction_toolbox(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    class CapturingDirectionAgent:
        def __init__(self, toolbox, generator) -> None:  # type: ignore[no-untyped-def]
            del generator
            captured["toolbox"] = toolbox

    monkeypatch.setattr(
        "project_copilot.web._build_chat_generator",
        lambda: (object(), "codex-switch-responses"),
    )
    monkeypatch.setattr("project_copilot.web.DirectionAgent", CapturingDirectionAgent)
    client = _client(tmp_path)
    filename = "uploaded-sequence-note.md"
    marker = "OMEGA-778 isolation threshold"
    upload = client.post(
        "/api/direction/sources",
        files={"files": (filename, marker.encode(), "text/markdown")},
        headers=HEADERS,
    )

    assert upload.status_code == 201
    result = captured["toolbox"].search_knowledge(marker)  # type: ignore[union-attr]
    assert filename in {citation["filename"] for citation in result["citations"]}
    assert any(marker in citation["excerpt"] for citation in result["citations"])
    filename_result = captured["toolbox"].search_knowledge(filename)  # type: ignore[union-attr]
    matching = [
        citation
        for citation in filename_result["citations"]
        if citation["filename"] == filename
    ]
    assert len(matching) == 1
    assert filename_result["citations"] == matching
    assert marker in matching[0]["excerpt"]

    late_filename = "long-sequence-note.txt"
    late_marker = "OMEGA-LATE-991 isolation threshold"
    late_upload = client.post(
        "/api/direction/sources",
        files={
            "files": (
                late_filename,
                (("routine operating note " * 500) + late_marker).encode(),
                "text/plain",
            )
        },
        headers=HEADERS,
    )
    assert late_upload.status_code == 201
    late_result = captured["toolbox"].search_knowledge(  # type: ignore[union-attr]
        f"{late_filename} {late_marker}"
    )
    assert late_result["citations"][0]["filename"] == late_filename
    assert late_marker in late_result["citations"][0]["excerpt"]


def test_exact_office_filename_uses_chunks_from_that_source_only(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    class CapturingDirectionAgent:
        def __init__(self, toolbox, generator) -> None:  # type: ignore[no-untyped-def]
            del generator
            captured["toolbox"] = toolbox

    monkeypatch.setattr(
        "project_copilot.web._build_chat_generator",
        lambda: (object(), "codex-switch-responses"),
    )
    monkeypatch.setattr("project_copilot.web.DirectionAgent", CapturingDirectionAgent)
    monkeypatch.setattr(
        "project_copilot.ingestion.DoclingOfficeParser.parse",
        lambda self, path: [
            ParsedDocumentChunk(
                content=(
                    "PDF-ONLY-881 requires a 300 second minimum confirmation window."
                    if path.name == "sequence.pdf"
                    else "OTHER-992 unrelated office content."
                ),
                page=2,
            )
        ],
    )
    client = _client(tmp_path)
    for filename in ("sequence.pdf", "unrelated.pdf"):
        response = client.post(
            "/api/direction/sources",
            files={"files": (filename, b"synthetic office fixture", "application/pdf")},
            headers=HEADERS,
        )
        assert response.status_code == 201

    result = captured["toolbox"].search_knowledge(  # type: ignore[union-attr]
        "sequence.pdf 的 PDF-ONLY-881 确认窗口是多少？"
    )

    assert result["clarification"] is False
    assert {item["filename"] for item in result["citations"]} == {"sequence.pdf"}
    assert "PDF-ONLY-881" in result["citations"][0]["excerpt"]
    assert "OTHER-992" not in result["summary"]


def test_duplicate_original_filename_requires_path_then_resolves_exact_path(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    class CapturingDirectionAgent:
        def __init__(self, toolbox, generator) -> None:  # type: ignore[no-untyped-def]
            del generator
            captured["toolbox"] = toolbox

    monkeypatch.setattr(
        "project_copilot.web._build_chat_generator",
        lambda: (object(), "codex-switch-responses"),
    )
    monkeypatch.setattr("project_copilot.web.DirectionAgent", CapturingDirectionAgent)
    client = _client(tmp_path)
    project_id = client.get("/api/health").json()["project_id"]
    client.app.state.indexer.import_files(
        project_id,
        [
            ImportedFile(
                filename="a1__README.md",
                original_filename="README.md",
                source_location="docs/README.md",
                category="background",
                content=b"DOCS-READ-117 deployment instructions",
            ),
            ImportedFile(
                filename="b2__README.md",
                original_filename="README.md",
                source_location="examples/README.md",
                category="background",
                content=b"EXAMPLE-READ-229 demonstration instructions",
            ),
            ImportedFile(
                filename="c3__README.md",
                original_filename="README.md",
                source_location="archive/docs/README.md",
                category="background",
                content=b"ARCHIVE-READ-330 archived instructions",
            ),
        ],
    )

    ambiguous = captured["toolbox"].search_knowledge(  # type: ignore[union-attr]
        "README.md 里写了什么？"
    )
    resolved = captured["toolbox"].search_knowledge(  # type: ignore[union-attr]
        "docs/README.md 里的部署说明是什么？"
    )
    nested = captured["toolbox"].search_knowledge(  # type: ignore[union-attr]
        "archive/docs/README.md 里的归档说明是什么？"
    )

    assert ambiguous["clarification"] is True
    assert ambiguous["citations"] == []
    assert "docs/README.md" in ambiguous["summary"]
    assert "examples/README.md" in ambiguous["summary"]
    assert "archive/docs/README.md" in ambiguous["summary"]
    assert resolved["clarification"] is False
    assert {item["location"] for item in resolved["citations"]} == {"docs/README.md"}
    assert "DOCS-READ-117" in resolved["summary"]
    assert "EXAMPLE-READ-229" not in resolved["summary"]
    assert {item["location"] for item in nested["citations"]} == {
        "archive/docs/README.md"
    }
    assert "ARCHIVE-READ-330" in nested["summary"]
    assert "DOCS-READ-117" not in nested["summary"]


def test_direction_trace_labels_all_typed_data_tools_as_runtime_data() -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "project_copilot"
        / "static"
        / "direction.js"
    ).read_text(encoding="utf-8")

    for tool in (
        "query_hvac_database",
        "inspect_hvac_snapshot",
        "inspect_configuration_change_effect",
        "inspect_metric_extreme",
    ):
        assert f'{tool}: "已计算运行数据"' in script


def test_direction_combined_question_returns_engineer_readable_evidence(
    tmp_path: Path,
) -> None:
    response = _client(tmp_path).post(
        "/api/direction/query",
        json={"question": "HP-02为什么修改送风设定，修改后的效果怎么样？"},
        headers=HEADERS,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "combined"
    assert payload["demo_mode"] is True
    assert "提高合成工艺区降温能力" in payload["answer_markdown"]
    assert "1.9" in payload["answer_markdown"]
    assert "4 kWh" in payload["answer_markdown"]
    assert payload["tables"][0]["columns"] == ["比较窗口", "送风均值", "电耗"]
    assert payload["charts"][0]["kind"] == "line"
    assert {item["filename"] for item in payload["citations"]} >= {
        "controls-review.md",
        "change-register.md",
    }
    assert all(item["excerpt"] for item in payload["citations"])
    assert all(item["location"] for item in payload["citations"])
    assert sum(item["support_share_pct"] for item in payload["citations"]) == 100
    assert all("source_id" not in item for item in payload["citations"])


def test_direction_data_question_uses_recomputed_database_truth(tmp_path: Path) -> None:
    response = _client(tmp_path).post(
        "/api/direction/query",
        json={"question": "原始数据和去重后的数据分别有多少行？"},
        headers=HEADERS,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "data"
    assert "103,650" in payload["answer_markdown"]
    assert "103,620" in payload["answer_markdown"]
    assert payload["tables"][0]["rows"] == [
        ["原始数据", "103,650"],
        ["按机组和时间去重", "103,620"],
        ["理想采样网格", "103,680"],
    ]


def test_direction_vague_question_clarifies_instead_of_guessing(tmp_path: Path) -> None:
    response = _client(tmp_path).post(
        "/api/direction/query",
        json={"question": "哪台机组更节能？"},
        headers=HEADERS,
    )

    payload = response.json()
    assert payload["clarification"] is True
    assert payload["refused"] is False
    assert "时间范围" in payload["answer_markdown"]
    assert "负荷" in payload["answer_markdown"]


def test_direction_refuses_data_deletion_or_equipment_control(tmp_path: Path) -> None:
    client = _client(tmp_path)
    for question in ("删除这些异常数据。", "把排气温度阈值改成140度。"):
        response = client.post(
            "/api/direction/query",
            json={"question": question},
            headers=HEADERS,
        )
        payload = response.json()
        assert payload["refused"] is True
        assert payload["clarification"] is False
        assert payload["tables"] == []
        assert payload["charts"] == []


def test_direction_route_uses_model_backed_agent_when_provider_is_configured(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    class FakeDirectionAgent:
        def answer(
            self, question: str, history: list[dict[str, str]] | None = None
        ) -> dict[str, object]:
            captured["history"] = history
            return {
                "mode": "knowledge",
                "demo_mode": False,
                "model_backed": True,
                "answer_markdown": f"### 已核对\n\n{question}",
                "tables": [],
                "charts": [],
                "citations": [],
                "activities": [],
                "clarification": False,
                "refused": False,
                "grounding_status": "grounded",
            }

    marker = object()
    monkeypatch.setattr(
        "project_copilot.web._build_chat_generator",
        lambda: (marker, "codex-switch-responses"),
    )
    monkeypatch.setattr(
        "project_copilot.web.DirectionAgent",
        lambda toolbox, generator: FakeDirectionAgent(),
    )

    client = _client(tmp_path)
    page = client.get("/direction")
    homepage = client.get("/")
    response = client.post(
        "/api/direction/query",
        json={
            "question": "当前配置是什么？",
            "history": [
                {"role": "user", "content": "先看 HP-02。"},
                {"role": "assistant", "content": "好的。"},
            ],
        },
        headers=HEADERS,
    )

    assert page.status_code == 200
    assert "真实模型 · 只读分析" in page.text
    assert "离线方向演示" not in page.text
    assert 'data-testid="direction-chat"' in homepage.text
    assert "Check a defrost sequence" not in homepage.text
    assert 'href="/workbench"' not in homepage.text
    assert client.get("/workbench", follow_redirects=False).status_code == 307
    assert response.json()["model_backed"] is True
    assert captured["history"] == [
        {"role": "user", "content": "先看 HP-02。"},
        {"role": "assistant", "content": "好的。"},
    ]
