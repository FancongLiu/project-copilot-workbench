from __future__ import annotations

import argparse
import json
import tarfile
from pathlib import Path
from zipfile import ZipFile

from project_copilot.release_guard import is_forbidden_private_path


class DistributionContentError(RuntimeError):
    """Raised when a release archive violates the public package contract."""


def _contains_hidden_truth(name: str) -> bool:
    parts = [part.casefold() for part in name.replace("\\", "/").split("/")]
    return "hidden_truth" in parts


def verify_distributions(distribution_dir: str | Path) -> dict[str, int]:
    root = Path(distribution_dir).resolve()
    wheels = sorted(root.glob("*.whl"))
    sdists = sorted(root.glob("*.tar.gz"))
    if not wheels or not sdists:
        raise DistributionContentError(
            f"Expected at least one wheel and one sdist in {root}"
        )

    hidden_members: list[str] = []
    private_members: list[str] = []
    compact_duckdb_count = 0
    for wheel in wheels:
        with ZipFile(wheel) as archive:
            names = archive.namelist()
        hidden_members.extend(
            f"{wheel.name}:{name}" for name in names if _contains_hidden_truth(name)
        )
        private_members.extend(
            f"{wheel.name}:{name}" for name in names if is_forbidden_private_path(name)
        )
        compact_duckdb_count += sum(
            name.endswith("direction_demo/datasets/hvac_bakeoff.duckdb")
            for name in names
        )
    for sdist in sdists:
        with tarfile.open(sdist) as archive:
            names = archive.getnames()
        hidden_members.extend(
            f"{sdist.name}:{name}" for name in names if _contains_hidden_truth(name)
        )
        private_members.extend(
            f"{sdist.name}:{name}" for name in names if is_forbidden_private_path(name)
        )

    if hidden_members:
        raise DistributionContentError(
            "Release archives contain hidden_truth: " + ", ".join(hidden_members)
        )
    if private_members:
        raise DistributionContentError(
            "Release archives contain private configuration/runtime paths: "
            + ", ".join(private_members)
        )
    if compact_duckdb_count < 1:
        raise DistributionContentError(
            "No release wheel contains the compact Agentic HVAC DuckDB corpus"
        )
    return {
        "wheel_count": len(wheels),
        "sdist_count": len(sdists),
        "compact_duckdb_count": compact_duckdb_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify public wheel and sdist contents after a release build."
    )
    parser.add_argument("distribution_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify_distributions(args.distribution_dir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
