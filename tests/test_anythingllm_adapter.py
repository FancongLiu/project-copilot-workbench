import json

import httpx
import pytest

from project_copilot.anythingllm import (
    AnythingLLMClient,
    AnythingLLMError,
    AnythingLLMSettings,
)


def test_anythingllm_adapter_uses_query_mode_and_returns_sources() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "chat-1",
                "type": "textResponse",
                "textResponse": "供水设定值为 7 摄氏度。",
                "sources": [
                    {
                        "title": "control.md",
                        "chunk": "冷冻水供水设定值为 7 摄氏度。",
                    }
                ],
                "close": True,
                "error": None,
            },
        )

    settings = AnythingLLMSettings(
        base_url="https://anythingllm.internal/api",
        api_key="test-key",
        workspace_slug="synthetic-hvac",
        allowed_hosts=("anythingllm.internal",),
    )
    client = AnythingLLMClient(
        settings,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.query("供水设定值是多少？")

    assert result.answer == "供水设定值为 7 摄氏度。"
    assert result.citations[0].source == "control.md"
    assert captured["path"] == "/api/v1/workspace/synthetic-hvac/chat"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["payload"] == {
        "message": "供水设定值是多少？",
        "mode": "query",
        "reset": False,
    }


def test_anythingllm_settings_reject_plain_http_for_non_loopback_hosts() -> None:
    with pytest.raises(AnythingLLMError, match="HTTPS"):
        AnythingLLMSettings(
            base_url="http://anythingllm.internal/api",
            api_key="test-key",
            workspace_slug="synthetic-hvac",
            allowed_hosts=("anythingllm.internal",),
        )


def test_anythingllm_discards_uncited_model_answers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "type": "textResponse",
                "textResponse": "Unsupported answer from general model knowledge.",
                "sources": [],
                "error": None,
            },
        )

    client = AnythingLLMClient(
        AnythingLLMSettings(
            base_url="https://anythingllm.internal/api",
            api_key="test-key",
            workspace_slug="synthetic-hvac",
            allowed_hosts=("anythingllm.internal",),
        ),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.query("没有证据的问题")

    assert result.refused is True
    assert "无法可靠回答" in result.answer
    assert "Unsupported answer" not in result.answer


def test_anythingllm_default_client_ignores_environment_proxies() -> None:
    client = AnythingLLMClient(
        AnythingLLMSettings(
            base_url="http://localhost:3001/api",
            api_key="test-key",
            workspace_slug="synthetic-hvac",
            allowed_hosts=("localhost",),
        )
    )

    try:
        assert client.http_client._trust_env is False
    finally:
        client.http_client.close()
