from pathlib import Path

import pytest

from project_copilot.contract import ProjectPackageError, load_project_package


def write_package(root: Path, *, docs_root: str = "docs/source") -> None:
    (root / "docs" / "source").mkdir(parents=True)
    (root / "datasets" / "raw").mkdir(parents=True)
    (root / "project.yaml").write_text(
        "\n".join(
            [
                'schema_version: "0.1"',
                "project_id: synthetic-hvac-demo",
                "display_name: Synthetic HVAC Plant",
                "documents:",
                f"  root: {docs_root}",
                "datasets:",
                "  root: datasets/raw",
                "security:",
                "  allow_network: false",
                "  allow_nl2sql: false",
            ]
        ),
        encoding="utf-8",
    )


def test_load_project_package_resolves_declared_roots(tmp_path: Path) -> None:
    write_package(tmp_path)

    package = load_project_package(tmp_path)

    assert package.project_id == "synthetic-hvac-demo"
    assert package.display_name == "Synthetic HVAC Plant"
    assert package.documents_root == (tmp_path / "docs" / "source").resolve()
    assert package.datasets_root == (tmp_path / "datasets" / "raw").resolve()
    assert package.security.allow_network is False
    assert package.security.allow_nl2sql is False
    assert package.security.allow_approved_provider is False


def test_load_project_package_rejects_paths_outside_package(tmp_path: Path) -> None:
    write_package(tmp_path, docs_root="../private-documents")

    with pytest.raises(ProjectPackageError, match="inside the project package"):
        load_project_package(tmp_path)
