from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from dataclasses import asdict, dataclass, replace
from io import BytesIO
from pathlib import Path
from pathlib import PurePosixPath
from pathlib import PureWindowsPath
from tempfile import NamedTemporaryFile
from typing import Any, Protocol
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

import jieba
from filelock import FileLock
from haystack import Document
from haystack.components.joiners import DocumentJoiner
from haystack.components.preprocessors import DocumentSplitter
from haystack.components.retrievers.in_memory import (
    InMemoryBM25Retriever,
    InMemoryEmbeddingRetriever,
)
from haystack.document_stores.in_memory import InMemoryDocumentStore

from project_copilot.workspaces import Workspace, WorkspaceManager


jieba.setLogLevel(logging.WARNING)


class IngestionError(ValueError):
    """Raised when an imported source is unsupported, unsafe, or too large."""


class EmbeddingBackend(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class DocumentReranker(Protocol):
    def rank(
        self, query: str, documents: list[Document], top_k: int
    ) -> list[Document]: ...


@dataclass(frozen=True)
class ParsedDocumentChunk:
    content: str
    section: str | None = None
    page: int | None = None
    metadata: dict[str, Any] | None = None


class OfficeParser(Protocol):
    def parse(self, path: Path) -> list[ParsedDocumentChunk]: ...


class DoclingOfficeParser:
    def __init__(
        self,
        *,
        tokenizer_path: str | Path | None = None,
        artifacts_path: str | Path | None = None,
        max_tokens: int = 512,
    ) -> None:
        self.tokenizer_path = tokenizer_path
        self.artifacts_path = artifacts_path
        self.max_tokens = max_tokens

    def parse(self, path: Path) -> list[ParsedDocumentChunk]:
        configured_tokenizer = self.tokenizer_path or os.getenv(
            "PROJECT_COPILOT_DOCLING_TOKENIZER_PATH", ""
        )
        if not str(configured_tokenizer).strip():
            raise IngestionError(
                "Docling structured chunking requires the installed 'documents' extra "
                "and an approved local tokenizer path"
            )
        tokenizer_path = Path(configured_tokenizer).expanduser().resolve()
        if not tokenizer_path.is_dir():
            raise IngestionError(
                f"Docling tokenizer directory does not exist: {tokenizer_path}"
            )
        configured_artifacts = (
            self.artifacts_path
            or os.getenv("PROJECT_COPILOT_DOCLING_ARTIFACTS_PATH", "")
            or os.getenv("DOCLING_ARTIFACTS_PATH", "")
        )
        artifacts_path: Path | None = None
        if str(configured_artifacts).strip():
            artifacts_path = Path(configured_artifacts).expanduser().resolve()
            if not artifacts_path.is_dir():
                raise IngestionError(
                    f"Docling model artifacts directory does not exist: {artifacts_path}"
                )
        if path.suffix.casefold() == ".pdf" and artifacts_path is None:
            raise IngestionError(
                "PDF parsing requires an approved local Docling model artifacts directory"
            )
        try:
            from docling.chunking import HybridChunker
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling_core.transforms.chunker.tokenizer.huggingface import (
                HuggingFaceTokenizer,
            )
            from haystack_integrations.components.converters.docling import (
                DoclingConverter,
                ExportType,
            )
        except ImportError as exc:
            raise IngestionError(
                "Docling Haystack integration is required for Office/PDF sources; "
                "install the 'documents' extra"
            ) from exc
        tokenizer = HuggingFaceTokenizer.from_pretrained(
            model_name=tokenizer_path,
            max_tokens=self.max_tokens,
            local_files_only=True,
        )
        document_converter = None
        if path.suffix.casefold() == ".pdf":
            pipeline_options = PdfPipelineOptions(artifacts_path=artifacts_path)
            pipeline_options.do_ocr = False
            pipeline_options.do_table_structure = False
            pipeline_options.enable_remote_services = False
            document_converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                }
            )
        converter = DoclingConverter(
            converter=document_converter,
            convert_kwargs={"max_num_pages": 250, "max_file_size": 5_000_000},
            export_type=ExportType.DOC_CHUNKS,
            chunker=HybridChunker(tokenizer=tokenizer),
        )
        documents = converter.run(sources=[path])["documents"]
        parsed: list[ParsedDocumentChunk] = []
        for document in documents:
            metadata = dict(document.meta)
            docling_metadata = metadata.get("dl_meta") or {}
            chunk_metadata = (
                docling_metadata.get("meta", {})
                if isinstance(docling_metadata, dict)
                else {}
            )
            headings = chunk_metadata.get("headings") or []
            section = " / ".join(str(item) for item in headings if item) or None
            parsed.append(
                ParsedDocumentChunk(
                    content=document.content or "",
                    section=section,
                    page=(
                        int(metadata["page_number"])
                        if metadata.get("page_number") is not None
                        else None
                    ),
                    metadata=metadata,
                )
            )
        return parsed


class SentenceTransformersReranker:
    """Narrow adapter over Haystack's maintained cross-encoder ranker."""

    def __init__(self, model_path: str | Path, *, top_k: int = 5) -> None:
        approved_path = Path(model_path).expanduser().resolve()
        if not approved_path.is_dir():
            raise IngestionError(
                f"Approved local reranker model directory does not exist: {approved_path}"
            )
        try:
            from haystack_integrations.components.rankers.sentence_transformers import (
                SentenceTransformersSimilarityRanker,
            )
        except ImportError as exc:
            raise IngestionError(
                "Sentence Transformers Haystack integration is required; "
                "install the 'reranking' extra"
            ) from exc
        self.ranker = SentenceTransformersSimilarityRanker(
            model=str(approved_path),
            top_k=top_k,
            trust_remote_code=False,
            backend="torch",
        )

    def rank(self, query: str, documents: list[Document], top_k: int) -> list[Document]:
        return self.ranker.run(
            query=query,
            documents=documents,
            top_k=top_k,
        )["documents"]


@dataclass(frozen=True)
class ImportedFile:
    filename: str
    content: bytes
    category: str


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    filename: str
    category: str
    status: str
    sha256: str
    parser: str
    size_bytes: int
    error: str | None = None


@dataclass(frozen=True)
class SourceCitation:
    source_id: str
    source: str
    category: str
    excerpt: str
    section: str | None
    page: int | None
    score: float


@dataclass(frozen=True)
class SearchResult:
    answer: str
    citations: tuple[SourceCitation, ...]
    refused: bool


@dataclass(frozen=True)
class WorkspaceSnapshot:
    generation: str
    root: Path
    sources_path: Path
    index_path: Path
    metadata_path: Path


def _search_tokens(text: str) -> str:
    return " ".join(token for token in jieba.cut_for_search(text) if token.strip())


class ProjectIndexer:
    MAX_FILE_BYTES = 5_000_000
    MAX_ARCHIVE_BYTES = 50_000_000
    MAX_FILES = 500
    MIN_BM25_SCORE = 0.1
    CATEGORIES = {
        "background",
        "configuration",
        "meeting",
        "SOP",
        "decision",
        "dataset",
    }
    TEXT_EXTENSIONS = {".md", ".txt", ".json"}
    OFFICE_EXTENSIONS = {
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
        ".html",
        ".htm",
        ".odt",
        ".ods",
        ".odp",
    }

    def __init__(
        self,
        manager: WorkspaceManager,
        *,
        embedding_backend: EmbeddingBackend | None = None,
        office_parser: OfficeParser | None = None,
        reranker: DocumentReranker | None = None,
    ) -> None:
        self.manager = manager
        self.embedding_backend = embedding_backend
        self.office_parser = office_parser or DoclingOfficeParser()
        self.reranker = reranker

    def import_files(
        self, project_id: str, files: list[ImportedFile]
    ) -> list[SourceRecord]:
        workspace = self._get_workspace(project_id)
        with self._workspace_lock(workspace):
            return self._import_files_unlocked(workspace, files)

    def _import_files_unlocked(
        self, workspace: Workspace, files: list[ImportedFile]
    ) -> list[SourceRecord]:
        if not files or len(files) > self.MAX_FILES:
            raise IngestionError("Import must contain between 1 and 500 files")
        prepared: list[tuple[ImportedFile, str, str, str]] = []
        batch_names: dict[str, str] = {}
        for item in files:
            filename = self._safe_filename(item.filename)
            canonical_name = self._canonical_filename(filename)
            if canonical_name in batch_names:
                raise IngestionError(
                    "Windows filename collision in import batch: "
                    f"{batch_names[canonical_name]} and {filename}"
                )
            batch_names[canonical_name] = filename
            if item.category not in self.CATEGORIES:
                raise IngestionError(f"Unsupported source category: {item.category}")
            if len(item.content) > self.MAX_FILE_BYTES:
                raise IngestionError(
                    f"Source exceeds {self.MAX_FILE_BYTES} bytes: {filename}"
                )
            extension = Path(filename).suffix.casefold()
            if extension in self.TEXT_EXTENSIONS:
                parser = "plain-text"
            elif extension in self.OFFICE_EXTENSIONS:
                parser = "docling"
            else:
                parser = "dataset"
            if extension not in self.TEXT_EXTENSIONS | self.OFFICE_EXTENSIONS | {
                ".csv"
            }:
                raise IngestionError(
                    f"Unsupported source format: {extension or 'none'}"
                )
            digest = hashlib.sha256(item.content).hexdigest()
            prepared.append((item, filename, parser, digest))

        current = self._current_snapshot_unlocked(workspace)
        records = {
            self._canonical_filename(item.filename): item
            for item in self._read_snapshot_sources(current)
        }
        overrides: dict[str, bytes] = {}
        for item, filename, parser, digest in prepared:
            canonical_name = self._canonical_filename(filename)
            existing = records.get(canonical_name)
            if existing is not None and existing.filename != filename:
                raise IngestionError(
                    "Windows filename collision with an existing source: "
                    f"{existing.filename} and {filename}"
                )
            overrides[filename] = item.content
            records[canonical_name] = SourceRecord(
                source_id=hashlib.sha256(f"{filename}:{digest}".encode()).hexdigest()[
                    :16
                ],
                filename=filename,
                category=item.category,
                status="indexed",
                sha256=digest,
                parser=parser,
                size_bytes=len(item.content),
            )
        updated, _ = self._publish_candidate_snapshot(
            workspace,
            current,
            sorted(records.values(), key=lambda value: value.filename),
            overrides=overrides,
        )
        refreshed = {item.filename: item for item in updated}
        return [refreshed[filename] for _, filename, _, _ in prepared]

    def list_sources(self, project_id: str) -> list[SourceRecord]:
        workspace = self._get_workspace(project_id)
        with self._workspace_lock(workspace):
            return self._read_snapshot_sources(
                self._current_snapshot_unlocked(workspace)
            )

    def import_archive(
        self, project_id: str, archive_name: str, content: bytes
    ) -> list[SourceRecord]:
        if Path(archive_name).suffix.casefold() != ".zip":
            raise IngestionError("Project Package archives must use .zip")
        try:
            with ZipFile(BytesIO(content)) as archive:
                members = [item for item in archive.infolist() if not item.is_dir()]
                if not members or len(members) > self.MAX_FILES:
                    raise IngestionError("Project Package has an invalid file count")
                if sum(item.file_size for item in members) > self.MAX_ARCHIVE_BYTES:
                    raise IngestionError(
                        "Project Package is too large after extraction"
                    )
                pending: list[ImportedFile] = []
                names: set[str] = set()
                for member in members:
                    path = PurePosixPath(member.filename.replace("\\", "/"))
                    if path.is_absolute() or ".." in path.parts:
                        raise IngestionError(
                            "Project Package contains an unsafe archive path"
                        )
                    if member.external_attr >> 16 & 0o170000 == 0o120000:
                        raise IngestionError(
                            "Project Package cannot contain symbolic links"
                        )
                    if path.name == "project.yaml":
                        continue
                    filename = path.name
                    if filename in names:
                        raise IngestionError(
                            f"Project Package contains duplicate filename: {filename}"
                        )
                    names.add(filename)
                    payload = archive.read(member)
                    pending.append(
                        ImportedFile(
                            filename=filename,
                            content=payload,
                            category=self._category_for_archive_path(path),
                        )
                    )
        except BadZipFile as exc:
            raise IngestionError("Project Package archive is invalid") from exc
        if not pending:
            raise IngestionError("Project Package contains no importable sources")
        return self.import_files(project_id, pending)

    def delete_source(self, project_id: str, source_id: str) -> None:
        workspace = self._get_workspace(project_id)
        with self._workspace_lock(workspace):
            current = self._current_snapshot_unlocked(workspace)
            records = self._read_snapshot_sources(current)
            selected = next(
                (item for item in records if item.source_id == source_id), None
            )
            if selected is None:
                raise IngestionError(f"Unknown source: {source_id}")
            self._publish_candidate_snapshot(
                workspace,
                current,
                [item for item in records if item.source_id != source_id],
            )

    def inspect_source(self, project_id: str, source_id: str) -> dict[str, object]:
        workspace = self._get_workspace(project_id)
        with self._workspace_lock(workspace):
            snapshot = self._current_snapshot_unlocked(workspace)
            source = next(
                (
                    item
                    for item in self._read_snapshot_sources(snapshot)
                    if item.source_id == source_id
                ),
                None,
            )
            if source is None:
                raise IngestionError(f"Unknown source: {source_id}")
            path = snapshot.sources_path / source.filename
            preview = (
                path.read_text(encoding="utf-8")[:4_000]
                if source.parser == "plain-text"
                else "Dataset source; content preview is not exposed through the Agent."
            )
            return {"source": asdict(source), "preview": preview}

    def reindex(self, project_id: str) -> int:
        workspace = self._get_workspace(project_id)
        with self._workspace_lock(workspace):
            return self._reindex_unlocked(workspace)

    def _reindex_unlocked(self, workspace: Workspace) -> int:
        current = self._current_snapshot_unlocked(workspace)
        _, count = self._publish_candidate_snapshot(
            workspace,
            current,
            self._read_snapshot_sources(current),
        )
        return count

    def _build_snapshot_index(
        self,
        workspace: Workspace,
        snapshot: WorkspaceSnapshot,
        source_records: list[SourceRecord],
    ) -> tuple[list[SourceRecord], int]:
        project_id = workspace.project_id
        documents: list[Document] = []
        splitter = DocumentSplitter(split_by="passage", split_length=1, split_overlap=0)
        updated_records: list[SourceRecord] = []
        for source in source_records:
            if source.parser == "dataset":
                error = self._dataset_validation_error(
                    snapshot.sources_path / source.filename,
                    source.filename,
                    workspace=workspace,
                    source=source,
                )
                updated_records.append(
                    replace(
                        source,
                        status="error" if error else "indexed",
                        error=error,
                    )
                )
                continue
            path = snapshot.sources_path / source.filename
            try:
                parsed_chunks = self._source_chunks(path, source.parser)
            except (OSError, UnicodeDecodeError, IngestionError, RuntimeError) as exc:
                updated_records.append(
                    replace(source, status="error", error=str(exc)[:500])
                )
                continue
            updated_records.append(replace(source, status="indexed", error=None))
            chunks: list[Document] = []
            for parsed in parsed_chunks:
                content = parsed.content.strip()
                if not content:
                    continue
                base_document = Document(
                    content=content,
                    meta={
                        "project_source_id": source.source_id,
                        "source": source.filename,
                        "category": source.category,
                        "section": parsed.section or self._first_heading(content),
                        "page": parsed.page,
                        "parser_meta": parsed.metadata or {},
                    },
                )
                if source.parser == "plain-text":
                    chunks.extend(splitter.run(documents=[base_document])["documents"])
                else:
                    chunks.append(base_document)
            for chunk in chunks:
                original = (chunk.content or "").strip()
                if not original:
                    continue
                documents.append(
                    Document(
                        content=_search_tokens(original),
                        meta={**chunk.meta, "original_content": original},
                    )
                )
        if self.embedding_backend and documents:
            embeddings = self.embedding_backend.embed_documents(
                [str(document.meta["original_content"]) for document in documents]
            )
            documents = [
                Document(
                    id=document.id,
                    content=document.content,
                    meta=document.meta,
                    embedding=embedding,
                )
                for document, embedding in zip(documents, embeddings, strict=True)
            ]
        store = InMemoryDocumentStore(
            index=f"workspace-{project_id}-{uuid4().hex}",
            embedding_similarity_function="cosine",
        )
        if documents:
            store.write_documents(documents)
        snapshot.index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_index: Path | None = None
        try:
            with NamedTemporaryFile(
                dir=snapshot.index_path.parent,
                suffix=".index.json",
                delete=False,
            ) as temporary:
                temporary_index = Path(temporary.name)
            store.save_to_disk(str(temporary_index))
            os.replace(temporary_index, snapshot.index_path)
        finally:
            if temporary_index is not None:
                temporary_index.unlink(missing_ok=True)
        self._write_records(snapshot.metadata_path, updated_records)
        return updated_records, len(documents)

    def search(
        self,
        project_id: str,
        question: str,
        *,
        categories: set[str] | None = None,
        top_k: int = 5,
    ) -> SearchResult:
        workspace = self._get_workspace(project_id)
        if not question.strip():
            return SearchResult("Please ask a specific project question.", (), True)
        with self._workspace_lock(workspace):
            snapshot = self._current_snapshot_unlocked(workspace)
            if not snapshot.index_path.exists():
                self._reindex_unlocked(workspace)
                snapshot = self._current_snapshot_unlocked(workspace)
            store = InMemoryDocumentStore.load_from_disk(str(snapshot.index_path))
        filters = None
        if categories:
            filters = {
                "field": "meta.category",
                "operator": "in",
                "value": sorted(categories),
            }
        candidate_top_k = max(top_k * 3, 10) if self.reranker else top_k
        bm25_documents = InMemoryBM25Retriever(store, top_k=candidate_top_k).run(
            query=_search_tokens(question), filters=filters
        )["documents"]
        if self.embedding_backend:
            dense_documents = InMemoryEmbeddingRetriever(
                store, top_k=candidate_top_k
            ).run(
                query_embedding=self.embedding_backend.embed_query(question),
                filters=filters,
            )["documents"]
            matched = DocumentJoiner(
                join_mode="reciprocal_rank_fusion", top_k=candidate_top_k
            ).run(documents=[bm25_documents, dense_documents])["documents"]
        else:
            matched = [
                document
                for document in bm25_documents
                if (document.score or 0.0) >= self.MIN_BM25_SCORE
            ]
        if self.reranker and matched:
            matched = self.reranker.rank(question, matched, top_k)
        else:
            matched = matched[:top_k]
        if not matched:
            return SearchResult(
                "The imported project sources do not contain enough evidence to answer reliably.",
                (),
                True,
            )
        citations = tuple(
            SourceCitation(
                source_id=str(document.meta["project_source_id"]),
                source=str(document.meta["source"]),
                category=str(document.meta["category"]),
                excerpt=str(document.meta["original_content"])[:800],
                section=(
                    str(document.meta["section"])
                    if document.meta.get("section")
                    else None
                ),
                page=(
                    int(document.meta["page"]) if document.meta.get("page") else None
                ),
                score=float(document.score or 0.0),
            )
            for document in matched
        )
        return SearchResult(citations[0].excerpt, citations, False)

    def source_path(self, project_id: str, source_id: str) -> Path:
        workspace = self._get_workspace(project_id)
        with self._workspace_lock(workspace):
            snapshot = self._current_snapshot_unlocked(workspace)
            source = next(
                (
                    item
                    for item in self._read_snapshot_sources(snapshot)
                    if item.source_id == source_id
                ),
                None,
            )
            if source is None:
                raise IngestionError(f"Unknown source: {source_id}")
            return snapshot.sources_path / source.filename

    def _current_snapshot_unlocked(self, workspace: Workspace) -> WorkspaceSnapshot:
        if not workspace.state_path.exists():
            self._migrate_legacy_snapshot(workspace)
        try:
            state = json.loads(workspace.state_path.read_text(encoding="utf-8"))
            generation = str(state["generation"])
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise IngestionError("Workspace snapshot pointer is invalid") from exc
        snapshot = self._snapshot_paths(workspace, generation)
        if not snapshot.metadata_path.is_file() or not snapshot.sources_path.is_dir():
            raise IngestionError("Workspace snapshot is incomplete")
        return snapshot

    @staticmethod
    def _snapshot_paths(workspace: Workspace, generation: str) -> WorkspaceSnapshot:
        root = workspace.snapshots_path / generation
        return WorkspaceSnapshot(
            generation=generation,
            root=root,
            sources_path=root / "sources",
            index_path=root / "documents.json",
            metadata_path=root / "sources.json",
        )

    def _migrate_legacy_snapshot(self, workspace: Workspace) -> None:
        generation = f"migrated-{uuid4().hex}"
        snapshot = self._snapshot_paths(workspace, generation)
        snapshot.sources_path.mkdir(parents=True, exist_ok=False)
        try:
            if workspace.sources_path.is_dir():
                for source in workspace.sources_path.iterdir():
                    if source.is_file():
                        self._link_or_copy(source, snapshot.sources_path / source.name)
            if workspace.metadata_path.is_file():
                self._link_or_copy(workspace.metadata_path, snapshot.metadata_path)
            else:
                self._write_records(snapshot.metadata_path, [])
            if workspace.index_path.is_file():
                self._link_or_copy(workspace.index_path, snapshot.index_path)
            self._write_json_atomic(
                workspace.state_path,
                {"schema_version": "1", "generation": generation},
            )
        except Exception:
            shutil.rmtree(snapshot.root, ignore_errors=True)
            raise

    @classmethod
    def _read_snapshot_sources(cls, snapshot: WorkspaceSnapshot) -> list[SourceRecord]:
        payload = json.loads(snapshot.metadata_path.read_text(encoding="utf-8"))
        records = [SourceRecord(**item) for item in payload]
        names: dict[str, str] = {}
        for record in records:
            cls._safe_filename(record.filename)
            canonical_name = cls._canonical_filename(record.filename)
            if canonical_name in names:
                raise IngestionError(
                    "Workspace inventory contains a Windows filename collision: "
                    f"{names[canonical_name]} and {record.filename}"
                )
            names[canonical_name] = record.filename
        return records

    def _publish_candidate_snapshot(
        self,
        workspace: Workspace,
        current: WorkspaceSnapshot,
        records: list[SourceRecord],
        *,
        overrides: dict[str, bytes] | None = None,
    ) -> tuple[list[SourceRecord], int]:
        generation = uuid4().hex
        candidate = self._snapshot_paths(workspace, generation)
        candidate.sources_path.mkdir(parents=True, exist_ok=False)
        overrides = overrides or {}
        try:
            for source in records:
                destination = candidate.sources_path / source.filename
                if source.filename in overrides:
                    self._atomic_write_bytes(destination, overrides[source.filename])
                else:
                    self._link_or_copy(
                        current.sources_path / source.filename,
                        destination,
                    )
            updated_records, count = self._build_snapshot_index(
                workspace,
                candidate,
                records,
            )
            self._write_json_atomic(
                workspace.state_path,
                {"schema_version": "1", "generation": generation},
            )
            return updated_records, count
        except Exception as exc:
            shutil.rmtree(candidate.root, ignore_errors=True)
            if isinstance(exc, IngestionError):
                raise
            raise IngestionError(
                "Source snapshot build failed; previous snapshot restored"
            ) from exc

    @staticmethod
    def _link_or_copy(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, destination)
        except OSError:
            shutil.copy2(source, destination)

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
            newline="\n",
        ) as temporary:
            json.dump(payload, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)

    def _dataset_validation_error(
        self,
        path: Path,
        filename: str,
        *,
        workspace: Workspace,
        source: SourceRecord,
    ) -> str | None:
        try:
            if filename.casefold() == "telemetry.csv":
                from project_copilot.analytics import AnalyticsWorkspace

                AnalyticsWorkspace.build(
                    csv_path=path,
                    database_path=(
                        workspace.root / "analytics" / f"{source.sha256}.duckdb"
                    ),
                )
            elif filename.casefold() == "defrost_telemetry.csv":
                from project_copilot.defrost_diagnostics import (
                    DEFROST_TELEMETRY_SCHEMA,
                )
                from project_copilot.platform_compat import (
                    ensure_windows_architecture_env,
                )

                ensure_windows_architecture_env()
                import pandera.polars as pa
                import polars as pl

                try:
                    DEFROST_TELEMETRY_SCHEMA.validate(
                        pl.read_csv(path, try_parse_dates=True)
                    )
                except (
                    OSError,
                    pl.exceptions.PolarsError,
                    pa.errors.SchemaError,
                ) as exc:
                    raise IngestionError(
                        f"Defrost telemetry schema validation failed: {exc}"
                    ) from exc
        except (IngestionError, ValueError) as exc:
            return str(exc).replace(str(path), path.name)[:500]
        return None

    def _get_workspace(self, project_id: str) -> Workspace:
        workspace = next(
            (
                item
                for item in self.manager.list_workspaces()
                if item.project_id == project_id
            ),
            None,
        )
        if workspace is None:
            raise IngestionError(f"Unknown workspace: {project_id}")
        return workspace

    @staticmethod
    def _workspace_lock(workspace: Workspace) -> FileLock:
        return FileLock(str(workspace.root / ".workspace.lock"), timeout=30)

    @classmethod
    def _safe_filename(cls, filename: str) -> str:
        candidate = PureWindowsPath(filename)
        reserved_stems = {
            "con",
            "prn",
            "aux",
            "nul",
            *(f"com{index}" for index in range(1, 10)),
            *(f"lpt{index}" for index in range(1, 10)),
        }
        stem = filename.split(".", 1)[0].casefold()
        has_invalid_character = any(
            ord(character) < 32 or character in '<>:"/\\|?*' for character in filename
        )
        if (
            candidate.name != filename
            or len(candidate.parts) != 1
            or filename in {"", ".", ".."}
            or filename != filename.rstrip(" .")
            or has_invalid_character
            or stem in reserved_stems
        ):
            raise IngestionError(
                "Imported filenames must be Windows-safe single filenames"
            )
        return filename

    @staticmethod
    def _canonical_filename(filename: str) -> str:
        return filename.casefold()

    @staticmethod
    def _first_heading(content: str) -> str | None:
        match = re.search(r"(?m)^#{1,6}\s+(.+?)\s*$", content)
        return match.group(1).strip() if match else None

    def _source_chunks(self, path: Path, parser: str) -> list[ParsedDocumentChunk]:
        if parser == "plain-text":
            return [ParsedDocumentChunk(content=path.read_text(encoding="utf-8"))]
        if parser == "docling":
            parsed = self.office_parser.parse(path)
            if isinstance(parsed, str):
                return [ParsedDocumentChunk(content=parsed)]
            return parsed
        raise IngestionError(f"Unsupported parser: {parser}")

    @staticmethod
    def _category_for_archive_path(path: PurePosixPath) -> str:
        normalized = "/".join(path.parts).casefold()
        if "decision" in normalized:
            return "decision"
        if "meeting" in normalized:
            return "meeting"
        if "sop" in normalized or "procedure" in normalized:
            return "SOP"
        if "config" in normalized:
            return "configuration"
        if "dataset" in normalized or path.suffix.casefold() == ".csv":
            return "dataset"
        return "background"

    @staticmethod
    def _atomic_write_bytes(destination: Path, content: bytes) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
            temporary.write(content)
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, destination)

    @staticmethod
    def _write_records(path: Path, records: list[SourceRecord]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
            newline="\n",
        ) as temporary:
            json.dump(
                [asdict(item) for item in records],
                temporary,
                ensure_ascii=False,
                indent=2,
            )
            temporary.write("\n")
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
