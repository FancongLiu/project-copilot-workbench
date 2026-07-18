from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Any


def _plain_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_value(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _plain_value(model_dump(mode="json", by_alias=True, exclude_none=True))
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, (str, int, float, bool)):
        return enum_value
    return str(value)


def _normalize_item(raw_item: Any) -> dict[str, Any]:
    item = _plain_value(raw_item)
    if not isinstance(item, dict):
        return {"type": "unknown", "value": item}
    item_type = item.get("type")
    item["type"] = {
        "agentMessage": "agent_message",
        "commandExecution": "command_execution",
        "fileChange": "file_change",
        "mcpToolCall": "mcp_tool_call",
        "webSearch": "web_search",
    }.get(str(item_type), item_type)
    result = item.get("result")
    if isinstance(result, dict) and "structuredContent" in result:
        result["structured_content"] = result.pop("structuredContent")
    return item


def _status_value(value: Any) -> str:
    plain = _plain_value(value)
    return str(plain or "").casefold()


def run_sdk_turn(
    request: dict[str, Any],
    *,
    sdk_module: Any | None = None,
) -> str:
    if sdk_module is None:
        import openai_codex as sdk_module

    approval_mode = sdk_module.ApprovalMode.deny_all
    config = sdk_module.CodexConfig(
        codex_bin=str(request["codex_bin"]),
        launch_args_override=(
            str(request["codex_bin"]),
            "app-server",
            "--listen",
            "stdio://",
            "--strict-config",
        ),
        cwd=str(request["cwd"]),
        experimental_api=False,
    )
    with sdk_module.Codex(config) as codex:
        thread = codex.thread_start(
            approval_mode=approval_mode,
            cwd=str(request["cwd"]),
            ephemeral=True,
            model=str(request["model"]),
            model_provider=str(request["model_provider"]),
        )
        turn = thread.turn(
            str(request["prompt"]),
            approval_mode=approval_mode,
            effort=str(request["effort"]),
            output_schema=request["output_schema"],
        )
        result = turn.run()

    events: list[dict[str, Any]] = []
    for raw_item in getattr(result, "items", ()) or ():
        events.append(
            {
                "type": "item.completed",
                "item": _normalize_item(raw_item),
            }
        )
    final_response = getattr(result, "final_response", None)
    if final_response:
        events.append(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": str(final_response),
                },
            }
        )
    status = _status_value(getattr(result, "status", ""))
    if status == "completed":
        events.append(
            {
                "type": "turn.completed",
                "usage": _plain_value(getattr(result, "usage", None)) or {},
            }
        )
    else:
        events.append(
            {
                "type": "turn.failed",
                "error": {"message": "Codex SDK turn did not complete"},
            }
        )
    return "".join(
        json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        for event in events
    )


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
        if not isinstance(request, dict):
            raise TypeError("request must be an object")
        sys.stdout.write(run_sdk_turn(request))
        return 0
    except Exception as exc:
        sys.stdout.write(
            json.dumps(
                {
                    "type": "turn.failed",
                    "error": {
                        "message": "Codex SDK worker failed",
                        "kind": type(exc).__name__,
                    },
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
