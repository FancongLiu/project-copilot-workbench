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
