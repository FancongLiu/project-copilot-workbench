from __future__ import annotations

import ssl
from pathlib import Path


def build_tls_context(ca_bundle: str | Path | None = None) -> ssl.SSLContext:
    """Build the explicit TLS trust context shared by company HTTP clients."""
    if ca_bundle is None or not str(ca_bundle).strip():
        return ssl.create_default_context()
    path = Path(ca_bundle).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"CA bundle does not exist: {path}")
    return ssl.create_default_context(cafile=str(path))
