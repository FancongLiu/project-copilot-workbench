from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from project_copilot.knowledge import Citation, KnowledgeResult


class AnythingLLMError(RuntimeError):
    """Raised when the bounded AnythingLLM API contract fails."""


@dataclass(frozen=True)
class AnythingLLMSettings:
    base_url: str
    api_key: str
    workspace_slug: str
    allowed_hosts: tuple[str, ...]

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        host = (parsed.hostname or "").casefold()
        allowlist = {item.casefold() for item in self.allowed_hosts if item.strip()}
        if not host or host not in allowlist:
            raise AnythingLLMError(
                "AnythingLLM host is missing from the explicit allowlist"
            )
        if parsed.scheme != "https" and host not in {"127.0.0.1", "::1", "localhost"}:
            raise AnythingLLMError("AnythingLLM requires HTTPS for non-loopback hosts")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", self.workspace_slug):
            raise AnythingLLMError("AnythingLLM workspace slug is invalid")


class AnythingLLMClient:
    def __init__(
        self,
        settings: AnythingLLMSettings,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.http_client = http_client or httpx.Client(
            timeout=30.0,
            trust_env=False,
        )

    def query(self, question: str) -> KnowledgeResult:
        endpoint = (
            f"{self.settings.base_url.rstrip('/')}"
            f"/v1/workspace/{self.settings.workspace_slug}/chat"
        )
        try:
            response = self.http_client.post(
                endpoint,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                json={"message": question, "mode": "query", "reset": False},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AnythingLLMError(f"AnythingLLM request failed: {exc}") from exc

        payload = response.json()
        if payload.get("error") or payload.get("type") == "abort":
            raise AnythingLLMError(
                str(payload.get("error") or "AnythingLLM aborted the query")
            )

        answer = str(payload.get("textResponse") or "").strip()
        if not answer:
            return KnowledgeResult(
                "当前项目资料中没有找到足够证据，无法可靠回答。", (), True
            )

        citations = tuple(
            Citation(
                source=str(source.get("title") or "Untitled source"),
                excerpt=str(source.get("chunk") or "")[:500],
                score=1.0,
            )
            for source in payload.get("sources") or []
        )
        if not citations:
            return KnowledgeResult(
                "当前项目资料中没有找到足够证据，无法可靠回答。", (), True
            )
        return KnowledgeResult(answer=answer, citations=citations, refused=False)
