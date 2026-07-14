import pytest

from project_copilot.cli import build_parser, validate_bind_host


def test_cli_defaults_to_loopback_only() -> None:
    args = build_parser().parse_args([])

    assert args.host == "127.0.0.1"
    assert args.port == 8788


def test_cli_rejects_lan_binding() -> None:
    with pytest.raises(ValueError, match="loopback"):
        validate_bind_host("0.0.0.0")

    assert validate_bind_host("127.0.0.1") == "127.0.0.1"
