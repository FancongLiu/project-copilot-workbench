from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

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
    assert "Asia/Shanghai" in page.text


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
    assert 'href="/workbench"' in homepage.text
    assert 'data-testid="workspace-panel"' in client.get("/workbench").text
    assert response.json()["model_backed"] is True
    assert captured["history"] == [
        {"role": "user", "content": "先看 HP-02。"},
        {"role": "assistant", "content": "好的。"},
    ]
