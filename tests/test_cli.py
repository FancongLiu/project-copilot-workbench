import pytest
from pathlib import Path

from project_copilot.cli import build_parser, main, validate_bind_host
from project_copilot.ingestion import ProjectIndexer
from project_copilot.workspaces import WorkspaceManager


def test_cli_defaults_to_loopback_only() -> None:
    args = build_parser().parse_args([])

    assert args.host == "127.0.0.1"
    assert args.port == 8788


def test_cli_rejects_lan_binding() -> None:
    with pytest.raises(ValueError, match="loopback"):
        validate_bind_host("0.0.0.0")

    assert validate_bind_host("127.0.0.1") == "127.0.0.1"


def test_cli_creates_workspace_and_imports_source(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    source = tmp_path / "meeting.md"
    source.write_text(
        "Decision D-030 approved the commissioning checklist.", encoding="utf-8"
    )

    assert (
        main(
            [
                "--runtime",
                str(runtime),
                "--create-workspace",
                "commissioning-demo",
                "--display-name",
                "Commissioning Demo",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--runtime",
                str(runtime),
                "--workspace",
                "commissioning-demo",
                "--category",
                "decision",
                "--import-file",
                str(source),
            ]
        )
        == 0
    )

    records = ProjectIndexer(WorkspaceManager(runtime)).list_sources(
        "commissioning-demo"
    )
    assert records[0].filename == "meeting.md"
    assert records[0].status == "indexed"
