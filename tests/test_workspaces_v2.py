from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from haystack import Document

from project_copilot.ingestion import (
    DoclingOfficeParser,
    ImportedFile,
    IngestionError,
    ParsedDocumentChunk,
    ProjectIndexer,
)
from project_copilot.workspaces import WorkspaceManager


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

    indexer.delete_source(workspace.project_id, source.source_id)

    assert indexer.list_sources(workspace.project_id) == []
    assert (
        indexer.search(workspace.project_id, "How do I service P-101?").refused is True
    )
    assert not (workspace.sources_path / "sop.md").exists()


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
