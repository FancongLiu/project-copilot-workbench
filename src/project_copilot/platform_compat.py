from __future__ import annotations

import os
import sys


def ensure_windows_architecture_env() -> None:
    """Restore the native architecture hint required by Windows Polars wheels."""
    if sys.platform == "win32" and not os.environ.get("PROCESSOR_ARCHITECTURE"):
        os.environ["PROCESSOR_ARCHITECTURE"] = "AMD64" if sys.maxsize > 2**32 else "x86"
