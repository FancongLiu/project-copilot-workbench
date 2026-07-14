from __future__ import annotations

import argparse
from collections.abc import Sequence

import uvicorn

from project_copilot.web import create_app


LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Project Copilot Workbench.")
    parser.add_argument("--project", help="Path to a Project Package directory")
    parser.add_argument(
        "--runtime", help="Directory for generated local indexes and databases"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
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

    app = create_app(project_root=args.project, runtime_root=args.runtime)
    uvicorn.run(app, host=host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
