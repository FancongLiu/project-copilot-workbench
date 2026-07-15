from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from filelock import FileLock


class WorkspaceError(ValueError):
    """Raised when a workspace operation is invalid or unsafe."""


@dataclass(frozen=True)
class Workspace:
    project_id: str
    display_name: str
    root: Path
    sources_path: Path
    index_path: Path
    metadata_path: Path


class WorkspaceManager:
    _PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")

    def __init__(self, runtime_root: str | Path) -> None:
        self.runtime_root = Path(runtime_root).resolve()
        self.workspaces_root = self.runtime_root / "workspaces"
        self.registry_path = self.runtime_root / "workspace-registry.json"
        self.lock = FileLock(str(self.registry_path) + ".lock", timeout=30)
        self.workspaces_root.mkdir(parents=True, exist_ok=True)
        if not self.registry_path.exists():
            self._write_registry({"active": None, "workspaces": []})

    def create_workspace(self, *, display_name: str, project_id: str) -> Workspace:
        if not self._PROJECT_ID.fullmatch(project_id):
            raise WorkspaceError("Project ID must be a safe lowercase slug")
        if not display_name.strip():
            raise WorkspaceError("Workspace display name is required")
        with self.lock:
            registry = self._read_registry()
            if any(item["project_id"] == project_id for item in registry["workspaces"]):
                raise WorkspaceError(f"Workspace already exists: {project_id}")
            root = self.workspaces_root / project_id
            sources_path = root / "sources"
            index_path = root / "index" / "documents.json"
            metadata_path = root / "sources.json"
            sources_path.mkdir(parents=True)
            index_path.parent.mkdir(parents=True)
            metadata_path.write_text("[]\n", encoding="utf-8")
            registry["workspaces"].append(
                {"project_id": project_id, "display_name": display_name.strip()}
            )
            self._write_registry(registry)
        return self._workspace(project_id, display_name.strip())

    def activate(self, project_id: str) -> Workspace:
        with self.lock:
            registry = self._read_registry()
            item = next(
                (
                    entry
                    for entry in registry["workspaces"]
                    if entry["project_id"] == project_id
                ),
                None,
            )
            if item is None:
                raise WorkspaceError(f"Unknown workspace: {project_id}")
            registry["active"] = project_id
            self._write_registry(registry)
        return self._workspace(item["project_id"], item["display_name"])

    def active_workspace(self) -> Workspace:
        registry = self._read_registry()
        active = registry.get("active")
        if not active:
            raise WorkspaceError("No active workspace")
        item = next(
            entry for entry in registry["workspaces"] if entry["project_id"] == active
        )
        return self._workspace(item["project_id"], item["display_name"])

    def list_workspaces(self) -> list[Workspace]:
        registry = self._read_registry()
        return [
            self._workspace(item["project_id"], item["display_name"])
            for item in sorted(
                registry["workspaces"], key=lambda value: value["project_id"]
            )
        ]

    def _workspace(self, project_id: str, display_name: str) -> Workspace:
        root = self.workspaces_root / project_id
        return Workspace(
            project_id=project_id,
            display_name=display_name,
            root=root,
            sources_path=root / "sources",
            index_path=root / "index" / "documents.json",
            metadata_path=root / "sources.json",
        )

    def _read_registry(self) -> dict[str, object]:
        return json.loads(self.registry_path.read_text(encoding="utf-8"))

    def _write_registry(self, registry: dict[str, object]) -> None:
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.runtime_root,
            delete=False,
            newline="\n",
        ) as temporary:
            json.dump(registry, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, self.registry_path)
