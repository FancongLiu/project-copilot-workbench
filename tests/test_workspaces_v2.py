from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from haystack import Document

from project_copilot import ingestion as ingestion_module
from project_copilot.ingestion import (
    DoclingOfficeParser,
    ImportedFile,
    IngestionError,
    ParsedDocumentChunk,
    ProjectIndexer,
)
from project_copilot.workspaces import WorkspaceError, WorkspaceManager


class DeterministicEmbedding:
    @staticmethod
    def _vector(text: str) -> list[float]:
        normalized = text.casefold()
        if any(token in normalized for token in ("setpoint", "供水温度", "d-014")):
            return [1.0, 0.0, 0.0]
        return [0.0, 1.0, 0.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class FailingEmbedding:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        del texts
        raise RuntimeError("simulated embedding failure")

    def embed_query(self, text: str) -> list[float]:
        del text
        return [1.0, 0.0, 0.0]


class FakeDoclingParser:
    def parse(self, path: Path) -> list[ParsedDocumentChunk]:
        assert path.suffix == ".docx"
        return [
            ParsedDocumentChunk(
                content="Decision D-020 approved redundant pump testing.",
                section="Imported meeting / Decisions",
                page=4,
                metadata={"dl_meta": {"headings": ["Imported meeting", "Decisions"]}},
            )
        ]


class ReverseReranker:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def rank(self, query: str, documents: list[Document], top_k: int) -> list[Document]:
        self.seen = [str(document.meta["source"]) for document in documents]
        return list(reversed(documents))[:top_k]


def test_workspace_creation_is_durable_and_activatable(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "runtime")

    created = manager.create_workspace(
        display_name="Synthetic Cooling Upgrade",
        project_id="synthetic-cooling-upgrade",
    )
    manager.activate(created.project_id)

    restarted = WorkspaceManager(tmp_path / "runtime")
    assert restarted.active_workspace().project_id == "synthetic-cooling-upgrade"
    assert restarted.list_workspaces() == [created]
    assert created.sources_path.is_dir()
    assert created.index_path.parent.is_dir()


def test_workspace_creation_preserves_an_existing_unregistered_directory(
    tmp_path: Path,
) -> None:
    orphan = tmp_path / "runtime" / "workspaces" / "orphan-project"
    orphan.mkdir(parents=True)
    valuable = orphan / "valuable.txt"
    valuable.write_text("must survive", encoding="utf-8")
    manager = WorkspaceManager(tmp_path / "runtime")

    with pytest.raises(WorkspaceError, match="already exists on disk"):
        manager.create_workspace(display_name="Orphan", project_id="orphan-project")

    assert valuable.read_text(encoding="utf-8") == "must survive"


def test_concurrent_workspace_manager_initialization_preserves_registry(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime-concurrent-init"

    def initialize(index: int) -> None:
        manager = WorkspaceManager(runtime)
        if index == 0:
            try:
                manager.create_workspace(
                    display_name="First", project_id="first-project"
                )
            except WorkspaceError:
                pass

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(initialize, range(8)))

    restarted = WorkspaceManager(runtime)
    assert [item.project_id for item in restarted.list_workspaces()] == [
        "first-project"
    ]


def test_failed_registry_commit_leaves_no_orphan_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = WorkspaceManager(tmp_path / "runtime-registry-failure")

    def fail_registry(payload: dict[str, object]) -> None:
        del payload
        raise OSError("simulated registry failure")

    monkeypatch.setattr(manager, "_write_registry", fail_registry)

    with pytest.raises(OSError, match="registry failure"):
        manager.create_workspace(display_name="Orphan", project_id="orphan-project")

    assert not (manager.workspaces_root / "orphan-project").exists()


def test_import_creates_inventory_and_durable_cited_index(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    manager = WorkspaceManager(runtime)
    workspace = manager.create_workspace(
        display_name="Synthetic Cooling Upgrade",
        project_id="synthetic-cooling-upgrade",
    )
    manager.activate(workspace.project_id)
    indexer = ProjectIndexer(manager)

    imported = indexer.import_files(
        workspace.project_id,
        [
            ImportedFile(
                filename="meeting-2026-07-10.md",
                content=(
                    "# Control meeting\n\n"
                    "Decision D-014: lower the chilled-water supply setpoint to 6 C."
                ).encode(),
                category="decision",
            )
        ],
    )

    assert len(imported) == 1
    assert imported[0].status == "indexed"
    assert imported[0].category == "decision"
    assert imported[0].sha256
    assert imported[0].parser == "plain-text"
    assert (
        ProjectIndexer(WorkspaceManager(runtime)).list_sources(workspace.project_id)
        == imported
    )

    result = ProjectIndexer(WorkspaceManager(runtime)).search(
        workspace.project_id, "What setpoint did decision D-014 approve?"
    )
    assert result.refused is False
    assert "6 C" in result.citations[0].excerpt
    assert result.citations[0].source == "meeting-2026-07-10.md"
    assert result.citations[0].category == "decision"
    assert result.citations[0].section == "Control meeting"


def test_hybrid_retrieval_recovers_semantic_match_without_lexical_overlap(
    tmp_path: Path,
) -> None:
    manager = WorkspaceManager(tmp_path / "runtime")
    workspace = manager.create_workspace(
        display_name="Synthetic Cooling Upgrade",
        project_id="synthetic-cooling-upgrade",
    )
    indexer = ProjectIndexer(manager, embedding_backend=DeterministicEmbedding())
    indexer.import_files(
        workspace.project_id,
        [
            ImportedFile(
                filename="decision.md",
                content=b"Decision D-014 approved a six-degree setpoint.",
                category="decision",
            )
        ],
    )

    result = ProjectIndexer(
        WorkspaceManager(tmp_path / "runtime"),
        embedding_backend=DeterministicEmbedding(),
    ).search(workspace.project_id, "当前供水温度是多少？")

    assert result.refused is False
    assert result.citations[0].source == "decision.md"
    assert result.citations[0].score > 0


def test_source_delete_updates_inventory_and_index(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "runtime")
    workspace = manager.create_workspace(display_name="Demo", project_id="demo-project")
    indexer = ProjectIndexer(manager)
    source = indexer.import_files(
        workspace.project_id,
        [ImportedFile("sop.md", b"Lock out pump P-101 before service.", "SOP")],
    )[0]
    previous_generation = json.loads(workspace.state_path.read_text())["generation"]

    indexer.delete_source(workspace.project_id, source.source_id)

    assert (
        json.loads(workspace.state_path.read_text())["generation"]
        != previous_generation
    )
    assert indexer.list_sources(workspace.project_id) == []
    assert (
        indexer.search(workspace.project_id, "How do I service P-101?").refused is True
    )
    with pytest.raises(IngestionError, match="Unknown source"):
        indexer.source_path(workspace.project_id, source.source_id)


def test_failed_import_restores_files_inventory_and_previous_index(
    tmp_path: Path,
) -> None:
    manager = WorkspaceManager(tmp_path / "runtime-import-rollback")
    workspace = manager.create_workspace(display_name="Demo", project_id="demo-project")
    healthy = ProjectIndexer(manager)
    original = healthy.import_files(
        workspace.project_id,
        [ImportedFile("existing.md", b"ORIGINAL-APPROVED evidence", "decision")],
    )[0]
    previous_generation = json.loads(workspace.state_path.read_text())["generation"]

    with pytest.raises(IngestionError, match="previous snapshot restored"):
        ProjectIndexer(manager, embedding_backend=FailingEmbedding()).import_files(
            workspace.project_id,
            [
                ImportedFile(
                    "existing.md",
                    b"REPLACEMENT-MUST-NOT-COMMIT",
                    "decision",
                ),
                ImportedFile("new.md", b"NEW-MUST-NOT-COMMIT", "meeting"),
            ],
        )

    restarted = ProjectIndexer(WorkspaceManager(tmp_path / "runtime-import-rollback"))
    assert (
        json.loads(workspace.state_path.read_text())["generation"]
        == previous_generation
    )
    assert restarted.list_sources(workspace.project_id) == [original]
    assert (
        restarted.source_path(workspace.project_id, original.source_id).read_bytes()
        == b"ORIGINAL-APPROVED evidence"
    )
    assert (
        "ORIGINAL-APPROVED"
        in restarted.search(workspace.project_id, "ORIGINAL-APPROVED")
        .citations[0]
        .excerpt
    )
    assert restarted.search(workspace.project_id, "REPLACEMENT-MUST-NOT-COMMIT").refused


def test_failed_delete_restores_source_inventory_and_search_index(
    tmp_path: Path,
) -> None:
    manager = WorkspaceManager(tmp_path / "runtime-delete-rollback")
    workspace = manager.create_workspace(display_name="Demo", project_id="demo-project")
    healthy = ProjectIndexer(manager)
    protected = healthy.import_files(
        workspace.project_id,
        [ImportedFile("protected.md", b"CONFIDENTIAL-ORION deletion target", "SOP")],
    )[0]
    healthy.import_files(
        workspace.project_id,
        [ImportedFile("remaining.md", b"Remaining searchable evidence", "SOP")],
    )
    previous_generation = json.loads(workspace.state_path.read_text())["generation"]

    with pytest.raises(IngestionError, match="previous snapshot restored"):
        ProjectIndexer(manager, embedding_backend=FailingEmbedding()).delete_source(
            workspace.project_id, protected.source_id
        )

    restarted = ProjectIndexer(WorkspaceManager(tmp_path / "runtime-delete-rollback"))
    assert (
        json.loads(workspace.state_path.read_text())["generation"]
        == previous_generation
    )
    assert {item.filename for item in restarted.list_sources(workspace.project_id)} == {
        "protected.md",
        "remaining.md",
    }
    assert restarted.source_path(workspace.project_id, protected.source_id).exists()
    result = restarted.search(workspace.project_id, "CONFIDENTIAL-ORION")
    assert result.refused is False
    assert result.citations[0].source == "protected.md"


def test_failed_snapshot_pointer_commit_exposes_no_staged_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = WorkspaceManager(tmp_path / "runtime-pointer-failure")
    workspace = manager.create_workspace(display_name="Demo", project_id="demo-project")
    indexer = ProjectIndexer(manager)
    original = indexer.import_files(
        workspace.project_id,
        [ImportedFile("original.md", b"ORIGINAL-SNAPSHOT evidence", "decision")],
    )[0]
    previous_generation = json.loads(workspace.state_path.read_text())["generation"]
    original_write = indexer._write_json_atomic

    def fail_pointer(path: Path, payload: dict[str, object]) -> None:
        if path == workspace.state_path:
            raise OSError("simulated pointer failure")
        original_write(path, payload)

    monkeypatch.setattr(indexer, "_write_json_atomic", fail_pointer)

    with pytest.raises(IngestionError, match="previous snapshot restored"):
        indexer.import_files(
            workspace.project_id,
            [ImportedFile("staged.md", b"STAGED-MUST-NOT-PUBLISH", "meeting")],
        )

    restarted = ProjectIndexer(WorkspaceManager(tmp_path / "runtime-pointer-failure"))
    assert (
        json.loads(workspace.state_path.read_text())["generation"]
        == previous_generation
    )
    assert restarted.list_sources(workspace.project_id) == [original]
    assert restarted.search(workspace.project_id, "STAGED-MUST-NOT-PUBLISH").refused


def test_project_package_archive_rejects_path_traversal(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "runtime")
    workspace = manager.create_workspace(display_name="Demo", project_id="demo-project")
    archive = BytesIO()
    with ZipFile(archive, "w", ZIP_DEFLATED) as package:
        package.writestr("../private.md", "must not escape")

    with pytest.raises(IngestionError, match="unsafe archive path"):
        ProjectIndexer(manager).import_archive(
            workspace.project_id, "project-package.zip", archive.getvalue()
        )

    assert list(workspace.sources_path.iterdir()) == []


def test_office_document_uses_docling_adapter_and_enters_same_index(
    tmp_path: Path,
) -> None:
    manager = WorkspaceManager(tmp_path / "runtime")
    workspace = manager.create_workspace(display_name="Demo", project_id="demo-project")
    indexer = ProjectIndexer(manager, office_parser=FakeDoclingParser())

    imported = indexer.import_files(
        workspace.project_id,
        [ImportedFile("meeting.docx", b"synthetic office bytes", "meeting")],
    )
    result = indexer.search(workspace.project_id, "What did D-020 approve?")

    assert imported[0].parser == "docling"
    assert imported[0].status == "indexed"
    assert result.citations[0].source == "meeting.docx"
    assert "redundant pump" in result.citations[0].excerpt
    assert result.citations[0].section == "Imported meeting / Decisions"
    assert result.citations[0].page == 4


def test_pdf_import_requires_approved_local_docling_artifacts(
    tmp_path: Path,
) -> None:
    tokenizer_path = tmp_path / "tokenizer"
    tokenizer_path.mkdir()
    pdf_path = tmp_path / "manual.pdf"
    pdf_path.write_bytes(b"synthetic pdf placeholder")

    with pytest.raises(IngestionError, match="Docling model artifacts"):
        DoclingOfficeParser(tokenizer_path=tokenizer_path).parse(pdf_path)


def test_retrieval_applies_configured_reranker_after_candidate_retrieval(
    tmp_path: Path,
) -> None:
    manager = WorkspaceManager(tmp_path / "runtime-rerank")
    workspace = manager.create_workspace(display_name="Demo", project_id="demo-project")
    reranker = ReverseReranker()
    indexer = ProjectIndexer(manager, reranker=reranker)
    indexer.import_files(
        workspace.project_id,
        [
            ImportedFile(
                "alpha.md", b"defrost setpoint alpha evidence", "configuration"
            ),
            ImportedFile("beta.md", b"defrost setpoint beta evidence", "configuration"),
        ],
    )

    result = indexer.search(workspace.project_id, "defrost setpoint", top_k=2)

    assert set(reranker.seen) == {"alpha.md", "beta.md"}
    assert result.citations[0].source == reranker.seen[-1]


def test_missing_optional_docling_is_visible_in_source_inventory(
    tmp_path: Path,
) -> None:
    manager = WorkspaceManager(tmp_path / "runtime")
    workspace = manager.create_workspace(display_name="Demo", project_id="demo-project")

    imported = ProjectIndexer(manager).import_files(
        workspace.project_id,
        [ImportedFile("manual.docx", b"synthetic office bytes", "SOP")],
    )

    assert imported[0].status == "error"
    assert "Docling" in (imported[0].error or "")
    assert "documents" in (imported[0].error or "")


def test_concurrent_imports_preserve_every_source_and_a_readable_index(
    tmp_path: Path,
) -> None:
    manager = WorkspaceManager(tmp_path / "runtime-concurrent")
    workspace = manager.create_workspace(
        display_name="Concurrent Demo", project_id="concurrent-demo"
    )
    indexer = ProjectIndexer(manager)
    filenames = [f"meeting-{index}.md" for index in range(8)]

    def import_one(filename: str) -> None:
        indexer.import_files(
            workspace.project_id,
            [
                ImportedFile(
                    filename,
                    (
                        f"# {filename}\n\nDecision {filename} approved defrost review. "
                        * 30
                    ).encode(),
                    "meeting",
                )
            ],
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(import_one, filenames))

    assert {
        source.filename for source in indexer.list_sources(workspace.project_id)
    } == set(filenames)
    for filename in filenames:
        result = indexer.search(workspace.project_id, filename, top_k=1)
        assert result.refused is False
        assert result.citations[0].source == filename


def test_windows_case_colliding_filenames_are_rejected_atomically(
    tmp_path: Path,
) -> None:
    manager = WorkspaceManager(tmp_path / "runtime-case-collision")
    workspace = manager.create_workspace(display_name="Demo", project_id="demo-project")
    indexer = ProjectIndexer(manager)
    generation = workspace.state_path.read_text(encoding="utf-8")

    with pytest.raises(IngestionError, match="Windows filename collision"):
        indexer.import_files(
            workspace.project_id,
            [
                ImportedFile("A.md", b"ALPHA evidence", "background"),
                ImportedFile("a.md", b"BETA evidence", "background"),
            ],
        )

    assert indexer.list_sources(workspace.project_id) == []
    assert workspace.state_path.read_text(encoding="utf-8") == generation


def test_windows_case_variant_cannot_replace_an_existing_source(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "runtime-case-replace")
    workspace = manager.create_workspace(display_name="Demo", project_id="demo-project")
    indexer = ProjectIndexer(manager)
    original = indexer.import_files(
        workspace.project_id,
        [ImportedFile("Evidence.md", b"ORIGINAL evidence", "background")],
    )[0]

    with pytest.raises(IngestionError, match="Windows filename collision"):
        indexer.import_files(
            workspace.project_id,
            [ImportedFile("evidence.md", b"REPLACEMENT evidence", "background")],
        )

    assert indexer.source_path(
        workspace.project_id, original.source_id
    ).read_bytes() == (b"ORIGINAL evidence")
    assert [item.filename for item in indexer.list_sources(workspace.project_id)] == [
        "Evidence.md"
    ]


@pytest.mark.parametrize("filename", ["report.md.", "report.md ", "CON.txt", "bad?.md"])
def test_windows_unsafe_filenames_are_rejected(tmp_path: Path, filename: str) -> None:
    manager = WorkspaceManager(tmp_path / "runtime-unsafe-name")
    workspace = manager.create_workspace(display_name="Demo", project_id="demo-project")

    with pytest.raises(IngestionError, match="Windows-safe"):
        ProjectIndexer(manager).import_files(
            workspace.project_id,
            [ImportedFile(filename, b"synthetic", "background")],
        )


def test_relevant_excerpt_selects_window_covering_most_query_terms() -> None:
    content = (
        ("prefix " * 15)
        + "alpha D-008 approved the earlier decision. "
        + ("middle " * 75)
        + "very-long-query-token marks the later meeting. "
        + ("suffix " * 80)
    )

    excerpt = ingestion_module._relevant_excerpt(
        content,
        "alpha very-long-query-token",
        max_chars=800,
    )

    assert "alpha D-008" in excerpt
    assert "very-long-query-token" in excerpt
