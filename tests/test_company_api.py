import json

import httpx
import pytest
from openai import OpenAI

from project_copilot.company_api import (
    CompanyAPIError,
    CompanyAPISettings,
    CompanyModelClient,
)


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
