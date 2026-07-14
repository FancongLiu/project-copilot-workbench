import socket
from pathlib import Path

from fastapi.testclient import TestClient

from project_copilot.web import create_app


def test_default_demo_health_check_needs_no_outbound_network(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_connect = socket.socket.connect

    def block_external_connect(sock, address):  # type: ignore[no-untyped-def]
        host = address[0] if isinstance(address, tuple) else address
        if host in {"127.0.0.1", "::1", "localhost"}:
            return original_connect(sock, address)
        raise AssertionError(f"unexpected outbound socket: {address}")

    monkeypatch.setattr(socket.socket, "connect", block_external_connect)

    client = TestClient(create_app(runtime_root=tmp_path / "runtime"))

    assert client.get("/api/health").status_code == 200
    assert (
        client.post(
            "/api/knowledge/query",
            json={"question": "供水温度设定值是多少？"},
            headers={"X-Project-Copilot": "1"},
        ).status_code
        == 200
    )
