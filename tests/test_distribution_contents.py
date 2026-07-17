from __future__ import annotations

import io
import tarfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from scripts.verify_distribution_contents import (
    DistributionContentError,
    verify_distributions,
)


def _write_sdist(path: Path, names: list[str]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name in names:
            payload = b"fixture"
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def _write_wheel(path: Path, names: list[str]) -> None:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name in names:
            archive.writestr(name, "fixture")


def test_distribution_verifier_rejects_hidden_truth_in_sdist(tmp_path: Path) -> None:
    _write_sdist(
        tmp_path / "project-0.2.0.tar.gz",
        ["project-0.2.0/examples/agentic_hvac_bakeoff/hidden_truth/questions.json"],
    )
    _write_wheel(
        tmp_path / "project-0.2.0-py3-none-any.whl",
        ["project_copilot/direction_demo/datasets/hvac_bakeoff.duckdb"],
    )

    with pytest.raises(DistributionContentError, match="hidden_truth"):
        verify_distributions(tmp_path)


def test_distribution_verifier_accepts_safe_wheel_and_sdist(tmp_path: Path) -> None:
    _write_sdist(
        tmp_path / "project-0.2.0.tar.gz",
        ["project-0.2.0/src/project_copilot/direction.py"],
    )
    _write_wheel(
        tmp_path / "project-0.2.0-py3-none-any.whl",
        ["project_copilot/direction_demo/datasets/hvac_bakeoff.duckdb"],
    )

    report = verify_distributions(tmp_path)

    assert report["wheel_count"] == 1
    assert report["sdist_count"] == 1
    assert report["compact_duckdb_count"] == 1


@pytest.mark.parametrize(
    "member",
    [
        "project-0.2.0/.env.production",
        "project-0.2.0/.envrc",
        "project-0.2.0/.codex/config.toml",
        "project-0.2.0/ops/service-token.json",
        "project-0.2.0/project.local/private-runtime/index.json",
    ],
)
def test_distribution_verifier_rejects_private_config_and_runtime_members(
    tmp_path: Path, member: str
) -> None:
    _write_sdist(tmp_path / "project-0.2.0.tar.gz", [member])
    _write_wheel(
        tmp_path / "project-0.2.0-py3-none-any.whl",
        ["project_copilot/direction_demo/datasets/hvac_bakeoff.duckdb"],
    )

    with pytest.raises(DistributionContentError, match="private"):
        verify_distributions(tmp_path)
