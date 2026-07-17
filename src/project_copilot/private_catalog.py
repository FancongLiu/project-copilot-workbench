from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from project_copilot.ingestion import ImportedFile, ProjectIndexer
from project_copilot.workspaces import WorkspaceManager


class PrivateCatalogError(ValueError):
    """Raised when a private catalog boundary is unsafe or unusable."""


@dataclass(frozen=True)
class CatalogFile:
    source_path: Path
    relative_path: str
    storage_filename: str
    original_filename: str
    category: str


@dataclass(frozen=True)
class CatalogImportSummary:
    project_id: str
    imported_files: int
    indexed_files: int
    failed_files: int


class PrivateCatalogImporter:
    """Read approved project files and publish their index only to private runtime."""

    EXCLUDED_DIRECTORY_NAMES = {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "runtime",
        "indexes",
        "logs",
        "audit",
        "artifacts",
        "hidden_truth",
        ".mypy_cache",
        ".idea",
        ".vscode",
        ".codex",
    }
    PROTECTED_PREFIXES = {
        ("company", "runtime"),
        ("company", "chairman_inbox"),
        ("company", "chairman_outbox"),
        ("company", "task_claims"),
        ("company", "departments", "execution"),
    }
    SENSITIVE_FILENAMES = {
        ".env",
        ".env.local",
        "credentials.json",
        "secrets.yaml",
        "secrets.yml",
        "config.toml",
    }
    SENSITIVE_SUFFIXES = {".key", ".pem", ".pfx", ".jks", ".keystore"}
    SENSITIVE_NAME_PATTERN = re.compile(
        r"(?:^|[-_.])(credential|credentials|secret|secrets|token|tokens)(?:[-_.]|$)",
        re.IGNORECASE,
    )

    def __init__(
        self,
        *,
        source_root: str | Path,
        runtime_root: str | Path,
        public_worktree: str | Path,
    ) -> None:
        self.source_root = Path(source_root).resolve()
        self.runtime_root = Path(runtime_root).resolve()
        self.public_worktree = Path(public_worktree).resolve()
        if not self.source_root.is_dir():
            raise PrivateCatalogError("Private catalog source root does not exist")
        if self._inside(self.runtime_root, self.source_root) or self._inside(
            self.runtime_root, self.public_worktree
        ):
            raise PrivateCatalogError(
                "Private catalog runtime must remain outside the source and public worktree"
            )

    @staticmethod
    def _inside(candidate: Path, parent: Path) -> bool:
        return candidate == parent or candidate.is_relative_to(parent)

    @staticmethod
    def _category(relative_path: str, extension: str) -> str:
        normalized = relative_path.casefold()
        if "decision" in normalized or "change" in normalized:
            return "decision"
        if "meeting" in normalized or "chat" in normalized:
            return "meeting"
        if any(token in normalized for token in ("sop", "safety", "procedure")):
            return "SOP"
        if any(token in normalized for token in ("config", "control", "sequence")):
            return "configuration"
        if extension == ".csv" or any(
            token in normalized for token in ("dataset", "backtest", "telemetry")
        ):
            return "dataset"
        return "background"

    def _excluded(self, relative: Path) -> bool:
        parts = tuple(part.casefold() for part in relative.parts)
        if any(part in self.EXCLUDED_DIRECTORY_NAMES for part in parts[:-1]):
            return True
        if any(parts[: len(prefix)] == prefix for prefix in self.PROTECTED_PREFIXES):
            return True
        name = relative.name.casefold()
        if (
            name.startswith(".env")
            or name in self.SENSITIVE_FILENAMES
            or self.SENSITIVE_NAME_PATTERN.search(name)
        ):
            return True
        return relative.suffix.casefold() in self.SENSITIVE_SUFFIXES

    def discover(self) -> list[CatalogFile]:
        supported = (
            ProjectIndexer.TEXT_EXTENSIONS | ProjectIndexer.OFFICE_EXTENSIONS | {".csv"}
        )
        selected: list[CatalogFile] = []
        for path in sorted(self.source_root.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(self.source_root)
            if self._excluded(relative):
                continue
            extension = path.suffix.casefold()
            if extension not in supported:
                continue
            if path.stat().st_size > ProjectIndexer.MAX_FILE_BYTES:
                continue
            relative_path = relative.as_posix()
            digest = hashlib.sha256(
                relative_path.casefold().encode("utf-8")
            ).hexdigest()[:16]
            selected.append(
                CatalogFile(
                    source_path=path,
                    relative_path=relative_path,
                    storage_filename=f"{digest}__{path.name}",
                    original_filename=path.name,
                    category=self._category(relative_path, extension),
                )
            )
        return selected

    def import_into(
        self,
        *,
        manager: WorkspaceManager,
        indexer: ProjectIndexer,
        project_id: str,
        display_name: str,
    ) -> CatalogImportSummary:
        if manager.runtime_root != self.runtime_root:
            raise PrivateCatalogError("Workspace manager must use the private runtime")
        existing = next(
            (
                workspace
                for workspace in manager.list_workspaces()
                if workspace.project_id == project_id
            ),
            None,
        )
        if existing is None:
            manager.create_workspace(display_name=display_name, project_id=project_id)
        manager.activate(project_id)
        discovered = self.discover()
        for start in range(0, len(discovered), ProjectIndexer.MAX_FILES):
            batch = discovered[start : start + ProjectIndexer.MAX_FILES]
            import_method = (
                indexer.replace_files if start == 0 else indexer.import_files
            )
            import_method(
                project_id,
                [
                    ImportedFile(
                        filename=item.storage_filename,
                        content=item.source_path.read_bytes(),
                        category=item.category,
                        original_filename=item.original_filename,
                        source_location=item.relative_path,
                    )
                    for item in batch
                ],
            )
        if not discovered:
            indexer.replace_files(project_id, [])
        selected_storage_names = {item.storage_filename for item in discovered}
        records = [
            record
            for record in indexer.list_sources(project_id)
            if record.filename in selected_storage_names
        ]
        return CatalogImportSummary(
            project_id=project_id,
            imported_files=len(discovered),
            indexed_files=sum(record.status == "indexed" for record in records),
            failed_files=sum(record.status == "error" for record in records),
        )
