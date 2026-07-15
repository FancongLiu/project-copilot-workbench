from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any


DEFAULT_TOKENIZER = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_TEXT_MODEL_COMMIT = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
DEFAULT_LAYOUT_REPO = "docling-project/docling-layout-old"
DEFAULT_LAYOUT_REVISION = "b5b4bd59ad2b69aab715e9b1f1dfd74394c45fd4"
DEFAULT_LAYOUT_FOLDER = "docling-project--docling-layout-old"


def prefetch_assets(
    *,
    artifacts_dir: Path,
    tokenizer_dir: Path,
    tokenizer_model: str = DEFAULT_TOKENIZER,
    tokenizer_revision: str = DEFAULT_TEXT_MODEL_COMMIT,
    layout_model_repo: str = DEFAULT_LAYOUT_REPO,
    layout_model_revision: str = DEFAULT_LAYOUT_REVISION,
    layout_downloader: Any | None = None,
    tokenizer_factory: Any | None = None,
) -> None:
    """Download the exact Docling assets used by the offline parser smoke."""
    if layout_downloader is None:
        from docling.models.utils.hf_model_download import download_hf_model

        layout_downloader = download_hf_model
    if tokenizer_factory is None:
        from transformers import AutoTokenizer

        tokenizer_factory = AutoTokenizer

    artifacts_dir = artifacts_dir.expanduser().resolve()
    tokenizer_dir = tokenizer_dir.expanduser().resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    tokenizer_dir.mkdir(parents=True, exist_ok=True)
    layout_downloader(
        repo_id=layout_model_repo,
        revision=layout_model_revision,
        local_dir=artifacts_dir / DEFAULT_LAYOUT_FOLDER,
        force=False,
        progress=False,
    )
    tokenizer_factory.from_pretrained(
        tokenizer_model,
        revision=tokenizer_revision,
    ).save_pretrained(tokenizer_dir)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prefetch immutable Docling and tokenizer assets on a connected builder."
    )
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--tokenizer-dir", type=Path, required=True)
    parser.add_argument("--tokenizer-model", default=DEFAULT_TOKENIZER)
    parser.add_argument("--tokenizer-revision", default=DEFAULT_TEXT_MODEL_COMMIT)
    parser.add_argument("--layout-model-repo", default=DEFAULT_LAYOUT_REPO)
    parser.add_argument("--layout-model-revision", default=DEFAULT_LAYOUT_REVISION)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    prefetch_assets(
        artifacts_dir=args.artifacts_dir,
        tokenizer_dir=args.tokenizer_dir,
        tokenizer_model=args.tokenizer_model,
        tokenizer_revision=args.tokenizer_revision,
        layout_model_repo=args.layout_model_repo,
        layout_model_revision=args.layout_model_revision,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
