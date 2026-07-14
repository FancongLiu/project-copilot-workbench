from __future__ import annotations

import os
from typing import Protocol

from project_copilot.anythingllm import AnythingLLMClient, AnythingLLMSettings
from project_copilot.contract import ProjectPackage
from project_copilot.knowledge import KnowledgeResult, LocalKnowledgeIndex


class KnowledgeProvider(Protocol):
    def query(self, question: str) -> KnowledgeResult: ...


class ProviderConfigurationError(ValueError):
    """Raised when a provider is missing required fail-closed settings."""


def _required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ProviderConfigurationError(f"Missing required setting: {name}")
    return value


def resolve_knowledge_provider(
    package: ProjectPackage,
) -> tuple[KnowledgeProvider, str]:
    provider_name = os.getenv("PROJECT_COPILOT_KNOWLEDGE_PROVIDER", "local").casefold()
    if provider_name == "local":
        return LocalKnowledgeIndex.from_directory(
            package.documents_root
        ), "haystack-local"
    if provider_name == "anythingllm":
        if not package.security.allow_approved_provider:
            raise ProviderConfigurationError(
                "The Project Package does not permit an approved remote provider"
            )
        if (
            os.getenv("PROJECT_COPILOT_ACK_DOWNSTREAM_APPROVED", "").casefold()
            != "true"
        ):
            raise ProviderConfigurationError(
                "AnythingLLM downstream approval must be explicitly acknowledged"
            )
        allowed_hosts = tuple(
            host.strip()
            for host in _required_environment("PROJECT_COPILOT_ALLOWED_HOSTS").split(
                ","
            )
            if host.strip()
        )
        settings = AnythingLLMSettings(
            base_url=_required_environment("ANYTHINGLLM_BASE_URL"),
            api_key=_required_environment("ANYTHINGLLM_API_KEY"),
            workspace_slug=_required_environment("ANYTHINGLLM_WORKSPACE_SLUG"),
            allowed_hosts=allowed_hosts,
        )
        return AnythingLLMClient(settings), "anythingllm-query"
    raise ProviderConfigurationError(f"Unsupported knowledge provider: {provider_name}")
