from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str


RUNTIME_DATABASE_SUFFIXES = {".duckdb", ".sqlite", ".sqlite3"}
FORBIDDEN_NAMES = {".env", "credentials.json", "secrets.yaml", "secrets.yml"}
MAX_PUBLIC_FILE_BYTES = 2_000_000
MAX_APPROVED_SYNTHETIC_DATABASE_BYTES = 10_000_000
APPROVED_SYNTHETIC_DATABASES = {
    "examples/agentic_hvac_bakeoff/datasets/hvac_bakeoff.duckdb"
}
UNAPPROVED_BINARY_SUFFIXES = {
    ".7z",
    ".doc",
    ".docx",
    ".gz",
    ".parquet",
    ".pdf",
    ".tar",
    ".xls",
    ".xlsx",
    ".zip",
}
CONTENT_RULES = {
    "secret-like-token": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "windows-user-path": re.compile(r"\bC:\\Users\\[^\\\s]+\\", re.IGNORECASE),
}


def _git_candidate_files(public_root: Path) -> list[Path] | None:
    try:
        top_level = subprocess.run(
            ["git", "-C", str(public_root), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError:
        return None
    if top_level.returncode != 0:
        return None
    if Path(top_level.stdout.strip()).resolve() != public_root:
        return None

    listed = subprocess.run(
        [
            "git",
            "-C",
            str(public_root),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
    )
    if listed.returncode != 0:
        return None
    return sorted(
        public_root / relative for relative in listed.stdout.split("\0") if relative
    )


def _candidate_files(public_root: Path) -> list[Path]:
    git_files = _git_candidate_files(public_root)
    if git_files is not None:
        return git_files
    return sorted(
        path
        for path in public_root.rglob("*")
        if ".git" not in path.relative_to(public_root).parts
    )


def _is_approved_synthetic_database(
    public_root: Path,
    path: Path,
    relative: str,
) -> bool:
    if relative not in APPROVED_SYNTHETIC_DATABASES:
        return False
    if path.stat().st_size > MAX_APPROVED_SYNTHETIC_DATABASE_BYTES:
        return False
    example_root = public_root / "examples" / "agentic_hvac_bakeoff"
    provenance = example_root / "SYNTHETIC_DATA_PROVENANCE.md"
    manifest_path = example_root / "manifest.json"
    if not provenance.is_file() or not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        with path.open("rb") as handle:
            header = handle.read(12)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return manifest.get("fully_synthetic") is True and header[8:12] == b"DUCK"


def scan_public_tree(root: str | Path) -> list[Finding]:
    public_root = Path(root).resolve()
    findings: list[Finding] = []
    for path in _candidate_files(public_root):
        if path.is_symlink():
            findings.append(
                Finding(path.relative_to(public_root).as_posix(), "symbolic-link")
            )
            continue
        if not path.is_file():
            continue

        relative = path.relative_to(public_root).as_posix()
        if path.name.casefold() in FORBIDDEN_NAMES:
            findings.append(Finding(relative, "forbidden-config-file"))
        approved_synthetic_database = _is_approved_synthetic_database(
            public_root,
            path,
            relative,
        )
        if (
            path.suffix.casefold() in RUNTIME_DATABASE_SUFFIXES
            and not approved_synthetic_database
        ):
            findings.append(Finding(relative, "runtime-database"))
        if approved_synthetic_database:
            continue

        if path.suffix.casefold() == ".csv":
            parts = Path(relative).parts
            approved_example = (
                len(parts) >= 3
                and parts[0].casefold() == "examples"
                and (
                    public_root / parts[0] / parts[1] / "SYNTHETIC_DATA_PROVENANCE.md"
                ).is_file()
            )
            if not approved_example:
                findings.append(
                    Finding(relative, "data-file-outside-synthetic-example")
                )

        if path.stat().st_size > MAX_PUBLIC_FILE_BYTES:
            findings.append(Finding(relative, "oversized-file"))
            continue
        try:
            raw_content = path.read_bytes()
        except OSError:
            continue

        normalized = relative.casefold()
        allowed_png = normalized.startswith("docs/assets/") and normalized.endswith(
            ".png"
        )
        if allowed_png:
            if not raw_content.startswith(b"\x89PNG\r\n\x1a\n"):
                findings.append(Finding(relative, "invalid-public-image"))
            continue

        try:
            content = raw_content.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(Finding(relative, "unapproved-binary"))
            continue
        if (
            path.suffix.casefold() in UNAPPROVED_BINARY_SUFFIXES
            or b"\x00" in raw_content
        ):
            findings.append(Finding(relative, "unapproved-binary"))
            continue
        for rule, pattern in CONTENT_RULES.items():
            if pattern.search(content):
                findings.append(Finding(relative, rule))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan a public release tree for unsafe content."
    )
    parser.add_argument("root", nargs="?", default=".")
    args = parser.parse_args(argv)
    findings = scan_public_tree(args.root)
    for finding in findings:
        print(f"{finding.rule}: {finding.path}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
