from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ProjectPackageError(ValueError):
    """Raised when a project package is invalid or unsafe to load."""


class RootConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: str


class SecurityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_network: bool = False
    allow_nl2sql: bool = False
    allow_approved_provider: bool = False


class ProjectManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(pattern=r"^0\.1$")
    project_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,63}$")
    display_name: str = Field(min_length=1, max_length=100)
    documents: RootConfig
    datasets: RootConfig
    security: SecurityPolicy = SecurityPolicy()


@dataclass(frozen=True)
class ProjectPackage:
    root: Path
    project_id: str
    display_name: str
    documents_root: Path
    datasets_root: Path
    security: SecurityPolicy


def _resolve_inside(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root):
        raise ProjectPackageError("Declared paths must stay inside the project package")
    return candidate


def load_project_package(root: str | Path) -> ProjectPackage:
    package_root = Path(root).resolve()
    manifest_path = package_root / "project.yaml"
    if not manifest_path.is_file():
        raise ProjectPackageError(f"Missing project manifest: {manifest_path}")

    try:
        payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        manifest = ProjectManifest.model_validate(payload)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ProjectPackageError(f"Invalid project manifest: {exc}") from exc

    return ProjectPackage(
        root=package_root,
        project_id=manifest.project_id,
        display_name=manifest.display_name,
        documents_root=_resolve_inside(package_root, manifest.documents.root),
        datasets_root=_resolve_inside(package_root, manifest.datasets.root),
        security=manifest.security,
    )
