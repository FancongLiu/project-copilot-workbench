import json
import ssl

import certifi
import httpx
import pytest
from openai import OpenAI

from project_copilot.company_api import (
    CompanyAPIError,
    CompanyAPISettings,
    CompanyModelClient,
)
from project_copilot.embeddings import OpenAIEmbeddingBackend


def test_company_api_settings_fail_closed_for_unapproved_host() -> None:
    with pytest.raises(CompanyAPIError, match="allowlist"):
        CompanyAPISettings(
            base_url="https://api.openai.com/v1",
            api_key="placeholder",
            model="company-model",
            allowed_hosts=("ai.internal.example",),
        )


def test_company_api_settings_reject_plain_http_for_non_loopback_host() -> None:
    with pytest.raises(CompanyAPIError, match="HTTPS"):
        CompanyAPISettings(
            base_url="http://ai.internal.example/v1",
            api_key="placeholder",
            model="company-model",
            allowed_hosts=("ai.internal.example",),
        )


def test_company_model_sends_only_question_and_selected_evidence() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "company-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "供水设定值为 7 摄氏度。",
                        },
                    }
                ],
            },
        )

    settings = CompanyAPISettings(
        base_url="https://ai.internal.example/v1",
        api_key="placeholder",
        model="company-model",
        allowed_hosts=("ai.internal.example",),
    )
    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    openai_client = OpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        http_client=http_client,
    )
    client = CompanyModelClient(settings, client=openai_client)

    answer = client.answer(
        question="供水设定值是多少？",
        evidence=["[control.md] 冷冻水供水设定值为 7 摄氏度。"],
    )

    assert answer == "供水设定值为 7 摄氏度。"
    payload = json.dumps(captured, ensure_ascii=False)
    assert "供水设定值是多少" in payload
    assert "control.md" in payload
    assert "private.csv" not in payload


def test_company_model_default_client_ignores_environment_proxies() -> None:
    client = CompanyModelClient(
        CompanyAPISettings(
            base_url="http://localhost:8080/v1",
            api_key="placeholder",
            model="company-model",
            allowed_hosts=("localhost",),
        )
    )

    try:
        assert client.http_client is not None
        assert client.http_client._trust_env is False
    finally:
        if client.http_client is not None:
            client.http_client.close()


def test_company_embedding_backend_uses_approved_openai_compatible_endpoint() -> None:
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        inputs = captured[-1]["input"]
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "company-embedding",
                "data": [
                    {
                        "object": "embedding",
                        "index": index,
                        "embedding": [float(index), 1.0],
                    }
                    for index, _ in enumerate(inputs)
                ],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    settings = CompanyAPISettings(
        base_url="https://ai.internal.example/v1",
        api_key="placeholder",
        model="company-embedding",
        allowed_hosts=("ai.internal.example",),
    )
    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    openai_client = OpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        http_client=http_client,
    )
    backend = OpenAIEmbeddingBackend(settings, client=openai_client)

    assert backend.embed_documents(["alpha", "beta"]) == [[0.0, 1.0], [1.0, 1.0]]
    assert backend.embed_query("question") == [0.0, 1.0]
    assert captured[0]["model"] == "company-embedding"


def test_company_embedding_backend_uses_explicit_internal_ca_context() -> None:
    captured: dict[str, object] = {}
    real_client = httpx.Client

    def client_factory(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return real_client(transport=httpx.MockTransport(lambda _: httpx.Response(200)))

    backend = OpenAIEmbeddingBackend(
        CompanyAPISettings(
            base_url="https://ai.internal.example/v1",
            api_key="placeholder",
            model="company-embedding",
            allowed_hosts=("ai.internal.example",),
        ),
        ca_bundle=certifi.where(),
        http_client_factory=client_factory,
    )

    try:
        assert isinstance(captured["verify"], ssl.SSLContext)
        assert captured["trust_env"] is False
    finally:
        assert backend.http_client is not None
        backend.http_client.close()
