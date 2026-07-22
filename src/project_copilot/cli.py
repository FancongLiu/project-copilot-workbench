from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

import uvicorn

from project_copilot.web import create_app
from project_copilot.ingestion import ImportedFile, ProjectIndexer
from project_copilot.private_catalog import PrivateCatalogImporter
from project_copilot.workspaces import WorkspaceManager


LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Project Copilot Workbench.")
    parser.add_argument("--project", help="Path to a Project Package directory")
    parser.add_argument(
        "--runtime", help="Directory for generated local indexes and databases"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--create-workspace", metavar="PROJECT_ID")
    parser.add_argument("--display-name")
    parser.add_argument("--workspace", help="Workspace ID for import operations")
    parser.add_argument("--category", default="background")
    parser.add_argument("--import-file", action="append", default=[])
    parser.add_argument("--reindex-workspace", metavar="PROJECT_ID")
    parser.add_argument("--list-workspaces", action="store_true")
    parser.add_argument("--catalog-root", help="Read-only local project tree to index")
    parser.add_argument(
        "--public-worktree",
        help="Public Git worktree that private catalog runtime must remain outside",
    )
    parser.add_argument("--catalog-project-id", default="private-local-project")
    return parser


def validate_bind_host(host: str) -> str:
    if host not in LOOPBACK_HOSTS:
        raise ValueError("The workbench must bind to a loopback host")
    return host


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        host = validate_bind_host(args.host)
    except ValueError as exc:
        parser.error(str(exc))

    if any(
        (
            args.create_workspace,
            args.import_file,
            args.reindex_workspace,
            args.list_workspaces,
            args.catalog_root,
        )
    ):
        if not args.runtime:
            parser.error("Workspace operations require --runtime")
        manager = WorkspaceManager(args.runtime)
        indexer = ProjectIndexer(manager)
        if args.catalog_root:
            if not args.public_worktree:
                parser.error("--catalog-root requires --public-worktree")
            summary = PrivateCatalogImporter(
                source_root=args.catalog_root,
                runtime_root=args.runtime,
                public_worktree=args.public_worktree,
            ).import_into(
                manager=manager,
                indexer=indexer,
                project_id=args.catalog_project_id,
                display_name=args.display_name or "Private local project",
            )
            print(json.dumps(asdict(summary), ensure_ascii=False))
        if args.create_workspace:
            if not args.display_name:
                parser.error("--create-workspace requires --display-name")
            workspace = manager.create_workspace(
                project_id=args.create_workspace,
                display_name=args.display_name,
            )
            manager.activate(workspace.project_id)
            print(json.dumps({"workspace": workspace.project_id, "active": True}))
        if args.import_file:
            if not args.workspace:
                parser.error("--import-file requires --workspace")
            imported = []
            for raw_path in args.import_file:
                path = Path(raw_path).resolve()
                content = path.read_bytes()
                if path.suffix.casefold() == ".zip":
                    imported.extend(
                        indexer.import_archive(args.workspace, path.name, content)
                    )
                else:
                    imported.extend(
                        indexer.import_files(
                            args.workspace,
                            [ImportedFile(path.name, content, args.category)],
                        )
                    )
            print(json.dumps([asdict(item) for item in imported], ensure_ascii=False))
        if args.reindex_workspace:
            print(
                json.dumps({"indexed_chunks": indexer.reindex(args.reindex_workspace)})
            )
        if args.list_workspaces:
            active = manager.active_workspace().project_id
            print(
                json.dumps(
                    [
                        {
                            "project_id": item.project_id,
                            "display_name": item.display_name,
                            "active": item.project_id == active,
                        }
                        for item in manager.list_workspaces()
                    ],
                    ensure_ascii=False,
                )
            )
        return 0

    app = create_app(project_root=args.project, runtime_root=args.runtime)
    uvicorn.run(app, host=host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
