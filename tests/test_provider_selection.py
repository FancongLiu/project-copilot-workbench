from pathlib import Path

import pytest

from project_copilot.anythingllm import AnythingLLMClient
from project_copilot.contract import load_project_package
from project_copilot.providers import (
    ProviderConfigurationError,
    resolve_knowledge_provider,
)


def example_package() -> Path:
    return Path(__file__).resolve().parents[1] / "examples" / "synthetic_hvac"


def approved_provider_package(root: Path) -> Path:
    (root / "docs" / "source").mkdir(parents=True)
    (root / "datasets" / "raw").mkdir(parents=True)
    (root / "project.yaml").write_text(
        """schema_version: "0.1"
project_id: approved-provider-demo
display_name: Approved Provider Demo
documents:
  root: docs/source
datasets:
  root: datasets/raw
security:
  allow_network: false
  allow_nl2sql: false
  allow_approved_provider: true
""",
        encoding="utf-8",
    )
    return root


def test_provider_selection_defaults_to_local_haystack(monkeypatch) -> None:
    monkeypatch.delenv("PROJECT_COPILOT_KNOWLEDGE_PROVIDER", raising=False)

    provider, name = resolve_knowledge_provider(load_project_package(example_package()))

    assert name == "haystack-local"
    assert provider.__class__.__name__ == "LocalKnowledgeIndex"


def test_provider_selection_builds_bounded_anythingllm_adapter(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PROJECT_COPILOT_KNOWLEDGE_PROVIDER", "anythingllm")
    monkeypatch.setenv("ANYTHINGLLM_BASE_URL", "https://anythingllm.internal/api")
    monkeypatch.setenv("ANYTHINGLLM_API_KEY", "placeholder")
    monkeypatch.setenv("ANYTHINGLLM_WORKSPACE_SLUG", "synthetic-hvac")
    monkeypatch.setenv("PROJECT_COPILOT_ALLOWED_HOSTS", "anythingllm.internal")
    monkeypatch.setenv("PROJECT_COPILOT_ACK_DOWNSTREAM_APPROVED", "true")

    provider, name = resolve_knowledge_provider(
        load_project_package(approved_provider_package(tmp_path / "project"))
    )

    assert name == "anythingllm-query"
    assert isinstance(provider, AnythingLLMClient)
    assert provider.settings.workspace_slug == "synthetic-hvac"


def test_provider_selection_rejects_unknown_provider(monkeypatch) -> None:
    monkeypatch.setenv("PROJECT_COPILOT_KNOWLEDGE_PROVIDER", "unbounded-agent")

    with pytest.raises(ProviderConfigurationError, match="Unsupported"):
        resolve_knowledge_provider(load_project_package(example_package()))


def test_provider_selection_rejects_remote_provider_without_project_permission(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PROJECT_COPILOT_KNOWLEDGE_PROVIDER", "anythingllm")

    with pytest.raises(ProviderConfigurationError, match="Project Package"):
        resolve_knowledge_provider(load_project_package(example_package()))
