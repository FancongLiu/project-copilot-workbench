from pathlib import Path

import pytest

from project_copilot.ingestion import ProjectIndexer
from project_copilot.private_catalog import PrivateCatalogError, PrivateCatalogImporter
from project_copilot.workspaces import WorkspaceManager


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_private_catalog_indexes_business_files_and_excludes_runtime_secrets(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    public_worktree = tmp_path / "public-repo"
    runtime = tmp_path / "private-runtime"
    public_worktree.mkdir()
    _write(source / "meetings" / "startup-review.md", "OMEGA-401 start review")
    _write(source / "controls" / "sequence.py", "TRIP-992 interlock sequence")
    _write(source / "backtests" / "efficiency.csv", "unit,cop\nHP-03,4.01\n")
    _write(source / ".env", "SECRET_KEY=must-not-index")
    _write(source / ".git" / "config", "private remote")
    _write(source / ".venv" / "noise.md", "environment noise")
    _write(source / "build" / "noise.md", "build noise")
    _write(source / "company" / "runtime" / "context_state.json", "runtime state")
    _write(source / "company" / "chairman_inbox" / "message.md", "private inbox")
    _write(source / "company" / "chairman_outbox" / "message.md", "private outbox")

    importer = PrivateCatalogImporter(
        source_root=source,
        runtime_root=runtime,
        public_worktree=public_worktree,
    )
    discovered = importer.discover()

    assert {item.original_filename for item in discovered} == {
        "startup-review.md",
        "sequence.py",
        "efficiency.csv",
    }
    assert all(not item.source_path.is_symlink() for item in discovered)

    manager = WorkspaceManager(runtime)
    indexer = ProjectIndexer(manager)
    summary = importer.import_into(
        manager=manager,
        indexer=indexer,
        project_id="private-local-project",
        display_name="Private local project",
    )

    assert summary.imported_files == 3
    assert summary.indexed_files == 3
    assert summary.failed_files == 0
    records = indexer.list_sources("private-local-project")
    assert {record.original_filename for record in records} == {
        "startup-review.md",
        "sequence.py",
        "efficiency.csv",
    }
    result = indexer.search("private-local-project", "TRIP-992")
    assert result.citations[0].source == "sequence.py"
    assert "TRIP-992" in result.citations[0].excerpt
    assert not any(public_worktree.iterdir())


def test_private_catalog_rejects_runtime_inside_source_or_public_worktree(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    public_worktree = source / "public-repo"
    source.mkdir()
    public_worktree.mkdir()

    with pytest.raises(PrivateCatalogError, match="outside"):
        PrivateCatalogImporter(
            source_root=source,
            runtime_root=source / "runtime",
            public_worktree=public_worktree,
        )

    with pytest.raises(PrivateCatalogError, match="outside"):
        PrivateCatalogImporter(
            source_root=source,
            runtime_root=public_worktree / "runtime",
            public_worktree=public_worktree,
        )


def test_private_catalog_refresh_removes_deleted_and_newly_excluded_sources(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    public_worktree = tmp_path / "public-repo"
    runtime = tmp_path / "private-runtime"
    public_worktree.mkdir()
    retained = source / "retained.md"
    removed = source / "removed.md"
    newly_excluded = source / "ordinary-notes.md"
    _write(retained, "RETAIN-101 approved content")
    _write(removed, "REVOKE-202 content")
    _write(newly_excluded, "SECRET-NOTE-303 content")
    importer = PrivateCatalogImporter(
        source_root=source,
        runtime_root=runtime,
        public_worktree=public_worktree,
    )
    manager = WorkspaceManager(runtime)
    indexer = ProjectIndexer(manager)
    importer.import_into(
        manager=manager,
        indexer=indexer,
        project_id="private-local-project",
        display_name="Private local project",
    )

    removed.unlink()
    newly_excluded.rename(source / "credentials-notes.md")
    importer.import_into(
        manager=manager,
        indexer=indexer,
        project_id="private-local-project",
        display_name="Private local project",
    )

    records = indexer.list_sources("private-local-project")
    assert {record.original_filename for record in records} == {"retained.md"}
    assert indexer.search("private-local-project", "REVOKE-202").refused is True
    assert indexer.search("private-local-project", "SECRET-NOTE-303").refused is True


def test_private_catalog_excludes_environment_and_codex_config_patterns(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    public_worktree = tmp_path / "public-repo"
    public_worktree.mkdir()
    _write(source / "safe.md", "safe")
    _write(source / ".env.production", "PRIVATE_API_KEY=x")
    _write(source / ".envrc", "export PRIVATE_API_KEY=x")
    _write(source / ".codex" / "config.toml", "api_key='x'")
    _write(source / "service-token.json", "{}")

    discovered = PrivateCatalogImporter(
        source_root=source,
        runtime_root=tmp_path / "private-runtime",
        public_worktree=public_worktree,
    ).discover()

    assert [item.relative_path for item in discovered] == ["safe.md"]
