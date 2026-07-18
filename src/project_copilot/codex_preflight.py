from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from project_copilot.company_api import load_codex_switch_settings
from project_copilot.codex_runtime import (
    PACKAGE_DIR,
    REPOSITORY_ROOT,
    CodexRuntimeError,
    CodexRuntimeSettings,
    verify_elevated_sandbox_preflight,
)


def _default_corpus_root() -> Path:
    source = REPOSITORY_ROOT / "examples" / "agentic_hvac_bakeoff"
    return source if source.is_dir() else PACKAGE_DIR / "direction_demo"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify that the official Codex elevated Windows sandbox can read "
            "the bounded workspace and cannot read the private DuckDB evidence."
        )
    )
    parser.add_argument("--codex-bin", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--corpus-root", type=Path, default=_default_corpus_root())
    args = parser.parse_args()

    try:
        provider = load_codex_switch_settings()
        settings = CodexRuntimeSettings(
            codex_bin=args.codex_bin,
            runtime_root=args.runtime_root,
            base_url=provider.base_url,
            api_key=provider.api_key,
            model=provider.model,
            python_executable=Path(sys.executable),
            reasoning_effort=os.environ.get(
                "PROJECT_COPILOT_CODEX_REASONING_EFFORT", "high"
            ),
            enforce_windows_acl=True,
        )
        marker = verify_elevated_sandbox_preflight(
            settings,
            args.corpus_root,
        )
    except (CodexRuntimeError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Codex elevated sandbox preflight passed: {marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
