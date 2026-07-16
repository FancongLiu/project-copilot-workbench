import subprocess
import json
from pathlib import Path

from project_copilot.release_guard import scan_public_tree


def test_release_guard_finds_secret_like_content_and_runtime_databases(
    tmp_path: Path,
) -> None:
    (tmp_path / "safe.txt").write_text("synthetic example", encoding="utf-8")
    (tmp_path / "credentials.txt").write_text(
        "token=" + "sk" + "-" + "A" * 28,
        encoding="utf-8",
    )
    (tmp_path / "runtime.duckdb").write_bytes(b"not a real database")

    findings = scan_public_tree(tmp_path)

    assert {finding.rule for finding in findings} == {
        "secret-like-token",
        "runtime-database",
    }


def test_release_guard_accepts_synthetic_public_tree(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "Public synthetic HVAC demonstration.",
        encoding="utf-8",
    )

    assert scan_public_tree(tmp_path) == []


def test_release_guard_rejects_oversized_and_unapproved_binary_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "large.txt").write_bytes(b"x" * 2_000_001)
    (tmp_path / "company-export.pdf").write_bytes(b"%PDF-1.7\x00private")

    findings = scan_public_tree(tmp_path)

    assert {(finding.path, finding.rule) for finding in findings} == {
        ("company-export.pdf", "unapproved-binary"),
        ("large.txt", "oversized-file"),
    }


def test_release_guard_limits_csv_to_documented_synthetic_examples(
    tmp_path: Path,
) -> None:
    (tmp_path / "data.csv").write_text("name,value\nreal,1\n", encoding="utf-8")
    example = tmp_path / "examples" / "demo"
    example.mkdir(parents=True)
    (example / "SYNTHETIC_DATA_PROVENANCE.md").write_text(
        "This dataset is fully synthetic.",
        encoding="utf-8",
    )
    (example / "sample.csv").write_text(
        "name,value\nsynthetic,1\n",
        encoding="utf-8",
    )

    findings = scan_public_tree(tmp_path)

    assert [(finding.path, finding.rule) for finding in findings] == [
        ("data.csv", "data-file-outside-synthetic-example")
    ]


def test_release_guard_allows_documentation_png(tmp_path: Path) -> None:
    assets = tmp_path / "docs" / "assets"
    assets.mkdir(parents=True)
    (assets / "workbench.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    assert scan_public_tree(tmp_path) == []


def test_release_guard_rejects_arbitrary_binary_content(tmp_path: Path) -> None:
    (tmp_path / "payload.bin").write_bytes(bytes([0xFF, 0xD8, 0xFF, 0xE0, 0x01]))

    findings = scan_public_tree(tmp_path)

    assert [(finding.path, finding.rule) for finding in findings] == [
        ("payload.bin", "unapproved-binary")
    ]


def test_release_guard_allows_only_the_reviewed_synthetic_direction_database(
    tmp_path: Path,
) -> None:
    example = tmp_path / "examples" / "agentic_hvac_bakeoff"
    datasets = example / "datasets"
    datasets.mkdir(parents=True)
    (example / "SYNTHETIC_DATA_PROVENANCE.md").write_text(
        "This dataset is fully synthetic and not engineering guidance.",
        encoding="utf-8",
    )
    (example / "manifest.json").write_text(
        json.dumps({"fully_synthetic": True}),
        encoding="utf-8",
    )
    duckdb_header = b"12345678DUCK" + b"\0" * 32
    (datasets / "hvac_bakeoff.duckdb").write_bytes(duckdb_header)
    (datasets / "unreviewed.duckdb").write_bytes(b"not a real database")

    findings = scan_public_tree(tmp_path)

    assert [(finding.path, finding.rule) for finding in findings] == [
        (
            "examples/agentic_hvac_bakeoff/datasets/unreviewed.duckdb",
            "runtime-database",
        )
    ]


def test_release_guard_scans_force_added_ignored_files(tmp_path: Path) -> None:
    subprocess.run(
        ["git", "init", "-q"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / ".gitignore").write_text("dist/\n", encoding="utf-8")
    generated = tmp_path / "dist"
    generated.mkdir()
    sensitive = generated / "private-key.pem"
    sensitive.write_text(
        "-----BEGIN " + "PRIVATE KEY-----",
        encoding="utf-8",
    )

    assert scan_public_tree(tmp_path) == []

    subprocess.run(
        ["git", "add", "-f", "dist/private-key.pem"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    assert [(finding.path, finding.rule) for finding in scan_public_tree(tmp_path)] == [
        ("dist/private-key.pem", "private-key")
    ]
