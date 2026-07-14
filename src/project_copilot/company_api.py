from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from openai import OpenAI, OpenAIError


class CompanyAPIError(RuntimeError):
    """Raised when the approved company model endpoint is unavailable or unsafe."""


@dataclass(frozen=True)
class CompanyAPISettings:
    base_url: str
    api_key: str
    model: str
    allowed_hosts: tuple[str, ...]

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        host = (parsed.hostname or "").casefold()
        allowlist = {item.casefold() for item in self.allowed_hosts if item.strip()}
        if not host or not allowlist or host not in allowlist:
            raise CompanyAPIError(
                "Company API host is missing from the explicit allowlist"
            )
        if parsed.scheme != "https" and host not in {"127.0.0.1", "::1", "localhost"}:
            raise CompanyAPIError("Company API requires HTTPS for non-loopback hosts")
        if not self.model.strip():
            raise CompanyAPIError("Company API model must be configured")


class CompanyModelClient:
    def __init__(
        self, settings: CompanyAPISettings, *, client: OpenAI | None = None
    ) -> None:
        self.settings = settings
        self.http_client: httpx.Client | None = None
        if client is None:
            self.http_client = httpx.Client(timeout=30.0, trust_env=False)
            self.client = OpenAI(
                api_key=settings.api_key,
                base_url=settings.base_url.rstrip("/"),
                http_client=self.http_client,
            )
        else:
            self.client = client

    def answer(self, *, question: str, evidence: list[str]) -> str:
        if not evidence:
            raise CompanyAPIError("Model calls require selected evidence")

        request_payload = {
            "question": question,
            "evidence": evidence,
            "rules": [
                "Use only the supplied evidence.",
                "State that the evidence is insufficient when the answer is not supported.",
                "Do not request tools, files, URLs, or additional data.",
            ],
        }
        try:
            response = self.client.chat.completions.create(
                model=self.settings.model,
                temperature=0,
                messages=[
                    {
                        "role": "system",
                        "content": "You answer project questions from bounded evidence only.",
                    },
                    {
                        "role": "user",
                        "content": json.dumps(request_payload, ensure_ascii=False),
                    },
                ],
            )
        except OpenAIError as exc:
            raise CompanyAPIError(f"Company model request failed: {exc}") from exc

        content = response.choices[0].message.content
        if not content:
            raise CompanyAPIError("Company model returned an empty answer")
        return content.strip()
