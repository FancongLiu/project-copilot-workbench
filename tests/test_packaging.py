import tomllib
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_wheel_declares_complete_license_files() -> None:
    pyproject = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    project = pyproject["project"]

    assert project["license"] == "Apache-2.0"
    assert set(project["license-files"]) == {
        "LICENSE",
        "NOTICE",
        "THIRD_PARTY_NOTICES.md",
    }

    notices = (REPOSITORY_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "Copyright (c) 2026 Lucide Icons and Contributors" in notices
    assert "Permission to use, copy, modify" in notices


def test_wheel_includes_compact_agentic_hvac_direction_corpus() -> None:
    pyproject = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    included = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]

    assert (
        included["examples/agentic_hvac_bakeoff/datasets/hvac_bakeoff.duckdb"]
        == "project_copilot/direction_demo/datasets/hvac_bakeoff.duckdb"
    )
    assert (
        included["examples/agentic_hvac_bakeoff/docs/source"]
        == "project_copilot/direction_demo/docs/source"
    )
    assert (
        REPOSITORY_ROOT
        / "examples"
        / "agentic_hvac_bakeoff"
        / "datasets"
        / "hvac_bakeoff.duckdb"
    ).is_file()
