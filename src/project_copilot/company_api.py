from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx
from openai import OpenAI, OpenAIError


class CompanyAPIError(RuntimeError):
    """Raised when the approved company model endpoint is unavailable or unsafe."""


@dataclass(frozen=True)
class CompanyAPISettings:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    allowed_hosts: tuple[str, ...]
    wire_api: str = "chat_completions"

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
        if self.wire_api not in {"chat_completions", "responses"}:
            raise CompanyAPIError("Company API wire protocol is unsupported")


def load_codex_switch_settings(
    config_path: str | Path | None = None,
) -> CompanyAPISettings:
    """Load the active Codex provider only after explicit local-runtime approval."""

    if os.environ.get("PROJECT_COPILOT_ACK_CODEX_SWITCH", "").casefold() != "true":
        raise CompanyAPIError(
            "Codex Switch access requires PROJECT_COPILOT_ACK_CODEX_SWITCH=true"
        )
    selected = Path(
        config_path
        or os.environ.get("PROJECT_COPILOT_CODEX_CONFIG", "")
        or Path.home() / ".codex" / "config.toml"
    ).expanduser()
    try:
        config = tomllib.loads(selected.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CompanyAPIError("Codex Switch configuration is unavailable") from exc
    provider_name = str(config.get("model_provider", ""))
    provider = config.get("model_providers", {}).get(provider_name, {})
    if not isinstance(provider, dict):
        raise CompanyAPIError("Codex Switch active provider is invalid")
    base_url = str(provider.get("base_url", "")).rstrip("/")
    wire_api = str(provider.get("wire_api", ""))
    token = str(provider.get("experimental_bearer_token", ""))
    if not token:
        auth_path = selected.with_name("auth.json")
        try:
            auth = json.loads(auth_path.read_text(encoding="utf-8"))
            token = str(auth.get("OPENAI_API_KEY", ""))
        except (OSError, json.JSONDecodeError):
            token = ""
    if not token:
        raise CompanyAPIError("Codex Switch credential is unavailable")
    host = (urlparse(base_url).hostname or "").casefold()
    return CompanyAPISettings(
        base_url=base_url,
        api_key=token,
        model=str(config.get("model", "")),
        allowed_hosts=(host,),
        wire_api=wire_api,
    )


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
