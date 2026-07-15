from __future__ import annotations

from collections.abc import Callable

import httpx
from openai import OpenAI, OpenAIError

from project_copilot.company_api import CompanyAPISettings
from project_copilot.tls import build_tls_context


class EmbeddingError(RuntimeError):
    """Raised when the approved embedding endpoint fails."""


class OpenAIEmbeddingBackend:
    def __init__(
        self,
        settings: CompanyAPISettings,
        *,
        client: OpenAI | None = None,
        ca_bundle: str | None = None,
        http_client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self.settings = settings
        self.http_client: httpx.Client | None = None
        if client is None:
            self.http_client = http_client_factory(
                timeout=15.0,
                trust_env=False,
                verify=build_tls_context(ca_bundle),
            )
            self.client = OpenAI(
                api_key=settings.api_key,
                base_url=settings.base_url.rstrip("/"),
                http_client=self.http_client,
                max_retries=0,
            )
        else:
            self.client = client

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = self.client.embeddings.create(
                model=self.settings.model,
                input=texts,
            )
        except OpenAIError as exc:
            raise EmbeddingError(f"Company embedding request failed: {exc}") from exc
        return [
            list(item.embedding)
            for item in sorted(response.data, key=lambda item: item.index)
        ]

    def embed_query(self, text: str) -> list[float]:
        embeddings = self.embed_documents([text])
        if not embeddings:
            raise EmbeddingError("Company embedding endpoint returned no vectors")
        return embeddings[0]
