from __future__ import annotations

import logging
import os
import warnings
from dataclasses import dataclass
from pathlib import Path

os.environ["HAYSTACK_TELEMETRY_ENABLED"] = "False"
warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API.*",
    category=UserWarning,
)

import jieba  # noqa: E402
from haystack import Document  # noqa: E402
from haystack.components.preprocessors import DocumentSplitter  # noqa: E402
from haystack.components.retrievers.in_memory import InMemoryBM25Retriever  # noqa: E402
from haystack.document_stores.in_memory import InMemoryDocumentStore  # noqa: E402

jieba.setLogLevel(logging.WARNING)


class KnowledgeIndexError(ValueError):
    """Raised when a document source violates local indexing limits."""


@dataclass(frozen=True)
class Citation:
    source: str
    excerpt: str
    score: float


@dataclass(frozen=True)
class KnowledgeResult:
    answer: str
    citations: tuple[Citation, ...]
    refused: bool


def _search_tokens(text: str) -> str:
    return " ".join(token for token in jieba.cut_for_search(text) if token.strip())


class LocalKnowledgeIndex:
    MAX_FILES = 200
    MAX_FILE_BYTES = 2_000_000
    MAX_TOTAL_BYTES = 20_000_000

    def __init__(self, documents: list[Document], *, min_score: float = 1.0) -> None:
        self.document_store = InMemoryDocumentStore()
        if documents:
            self.document_store.write_documents(documents)
        self.retriever = InMemoryBM25Retriever(self.document_store, top_k=4)
        self.min_score = min_score

    @classmethod
    def from_directory(cls, directory: str | Path) -> "LocalKnowledgeIndex":
        requested_root = Path(directory)
        if requested_root.is_symlink():
            raise KnowledgeIndexError("The document root cannot be a symbolic link")
        try:
            root = requested_root.resolve(strict=True)
        except OSError as exc:
            raise KnowledgeIndexError(f"Document root is unavailable: {exc}") from exc
        if not root.is_dir():
            raise KnowledgeIndexError("The document root must be a directory")

        documents: list[Document] = []
        source_count = 0
        total_bytes = 0
        splitter = DocumentSplitter(split_by="passage", split_length=1, split_overlap=0)
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise KnowledgeIndexError(
                    f"Document source is a symbolic link: {path.name}"
                )
            if not path.is_file() or path.suffix.casefold() not in {".md", ".txt"}:
                continue
            try:
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(root):
                    raise KnowledgeIndexError(
                        f"Document source resolves outside the project root: {path.name}"
                    )
                source_size = resolved.stat().st_size
                if source_size > cls.MAX_FILE_BYTES:
                    raise KnowledgeIndexError(
                        f"Document source is larger than {cls.MAX_FILE_BYTES} bytes: {path.name}"
                    )
                source_count += 1
                total_bytes += source_size
                if source_count > cls.MAX_FILES:
                    raise KnowledgeIndexError(
                        f"Document source count exceeds {cls.MAX_FILES} files"
                    )
                if total_bytes > cls.MAX_TOTAL_BYTES:
                    raise KnowledgeIndexError(
                        f"Document sources exceed {cls.MAX_TOTAL_BYTES} total bytes"
                    )
                content = resolved.read_text(encoding="utf-8").strip()
            except UnicodeDecodeError as exc:
                raise KnowledgeIndexError(
                    f"Document source is not valid UTF-8: {path.name}"
                ) from exc
            except OSError as exc:
                raise KnowledgeIndexError(
                    f"Document source is unavailable: {path.name}"
                ) from exc
            if not content:
                continue
            source = path.relative_to(root).as_posix()
            chunks = splitter.run(
                documents=[Document(content=content, meta={"source": source})]
            )["documents"]
            for chunk in chunks:
                original_content = (chunk.content or "").strip()
                if not original_content:
                    continue
                documents.append(
                    Document(
                        content=_search_tokens(original_content),
                        meta={
                            "source": source,
                            "original_content": original_content,
                        },
                    )
                )
        return cls(documents)

    def query(self, question: str) -> KnowledgeResult:
        if not question.strip():
            return KnowledgeResult("请输入一个具体问题。", (), True)

        documents = self.retriever.run(query=_search_tokens(question))["documents"]
        matched = [
            document
            for document in documents
            if (document.score or 0.0) >= self.min_score
        ]
        if not matched:
            return KnowledgeResult(
                "当前项目资料中没有找到足够证据，无法可靠回答。", (), True
            )

        citations = tuple(
            Citation(
                source=str(document.meta["source"]),
                excerpt=str(document.meta["original_content"])[:500],
                score=float(document.score or 0.0),
            )
            for document in matched
        )
        return KnowledgeResult(
            answer=f"根据项目资料：{citations[0].excerpt}",
            citations=citations,
            refused=False,
        )
