import json
from typing import Any

import pytest

from coding_agent.loop import Limits, StopReason, run_agent
from coding_agent.tools import ToolResult
from tests.fakes import FakeClient, RepeatingClient, message, text, tool_use

TOOLS: list[dict[str, Any]] = [{"type": "bash_20250124", "name": "bash"}]


class RecordingTool:
    """Records calls and returns a fixed result."""

    def __init__(self, result: ToolResult | None = None):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._result = result or ToolResult.ok("ok")

    def __call__(self, name: str, tool_input: dict[str, Any]) -> ToolResult:
        self.calls.append((name, tool_input))
        return self._result


def run(client: FakeClient, tool: Any = None, limits: Limits | None = None):
    return run_agent(
        client.stream,
        "Do the task",
        tool or RecordingTool(),
        tools=TOOLS,
        model="claude-opus-5-5",
        effort="medium",
        limits=limits or Limits(),
    )


def test_runs_a_tool_then_finishes_with_the_final_text():
    client = FakeClient(
        message(tool_use("tu_1", "bash", {"command": "ls"}), stop_reason="tool_use"),
        message(text("All done.")),
    )
    tool = RecordingTool(ToolResult.ok("file.txt"))

    result = run(client, tool)

    assert tool.calls == [("bash", {"command": "ls"})]
    assert result.stop is StopReason.DONE
    assert result.final_text == "All done."
    assert result.turns == 2


def test_history_is_append_only_with_full_assistant_content():
    first = message(
        text("Looking."), tool_use("tu_1", "bash", {"command": "ls"}), stop_reason="tool_use"
    )
    client = FakeClient(first, message(text("Done.")))

    run(client)

    req1, req2 = client.requests
    assert req2["messages"][: len(req1["messages"])] == req1["messages"]
    assistant = req2["messages"][1]
    assert assistant["role"] == "assistant"
    assert [block.type for block in assistant["content"]] == ["text", "tool_use"]


def test_all_tool_results_for_a_turn_go_back_in_one_user_message():
    client = FakeClient(
        message(
            tool_use("tu_1", "bash", {"command": "ls"}),
            tool_use("tu_2", "bash", {"command": "pwd"}),
            stop_reason="tool_use",
        ),
        message(text("Done.")),
    )

    run(client)

    last = client.requests[1]["messages"][-1]
    assert last["role"] == "user"
    assert [r["tool_use_id"] for r in last["content"]] == ["tu_1", "tu_2"]
    assert all(r["type"] == "tool_result" for r in last["content"])


def test_a_failed_tool_returns_is_error():
    client = FakeClient(
        message(tool_use("tu_1", "bash", {"command": "false"}), stop_reason="tool_use"),
        message(text("Done.")),
    )

    run(client, RecordingTool(ToolResult.error("exit code 1")))

    result_block = client.requests[1]["messages"][-1]["content"][0]
    assert result_block["is_error"] is True
    assert result_block["content"] == "exit code 1"


def test_several_calls_run_in_issue_order_and_a_failure_does_not_stop_the_rest():
    calls: list[str] = []

    def tool(name: str, tool_input: dict[str, Any]) -> ToolResult:
        calls.append(tool_input["command"])
        if tool_input["command"] == "false":
            return ToolResult.error("exit code 1")
        return ToolResult.ok(tool_input["command"])

    client = FakeClient(
        message(
            tool_use("tu_1", "bash", {"command": "false"}),
            tool_use("tu_2", "bash", {"command": "ls"}),
            tool_use("tu_3", "bash", {"command": "pwd"}),
            stop_reason="tool_use",
        ),
        message(text("Done.")),
    )

    run(client, tool)

    results = client.requests[1]["messages"][-1]["content"]
    assert calls == ["false", "ls", "pwd"]
    assert [(r["tool_use_id"], r["is_error"]) for r in results] == [
        ("tu_1", True),
        ("tu_2", False),
        ("tu_3", False),
    ]


def test_a_tool_that_raises_becomes_an_error_result_and_the_loop_continues():
    def broken_tool(name: str, tool_input: dict[str, Any]) -> ToolResult:
        raise RuntimeError("sandbox went away")

    client = FakeClient(
        message(tool_use("tu_1", "bash", {"command": "ls"}), stop_reason="tool_use"),
        message(text("Done.")),
    )

    result = run(client, broken_tool)

    result_block = client.requests[1]["messages"][-1]["content"][0]
    assert result_block["is_error"] is True
    assert "sandbox went away" in result_block["content"]
    assert result.stop is StopReason.DONE


def test_system_and_tools_are_byte_identical_across_requests():
    client = FakeClient(
        message(tool_use("tu_1", "bash", {"command": "ls"}), stop_reason="tool_use"),
        message(tool_use("tu_2", "bash", {"command": "pwd"}), stop_reason="tool_use"),
        message(text("Done.")),
    )

    run(client)

    systems = {json.dumps(r["system"], sort_keys=False) for r in client.requests}
    tools = {json.dumps(r["tools"], sort_keys=False) for r in client.requests}
    assert len(client.requests) == 3
    assert len(systems) == 1
    assert len(tools) == 1


def test_request_uses_current_api_conventions():
    client = FakeClient(message(text("Done.")))

    run(client)

    request = client.requests[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["output_config"] == {"effort": "medium"}
    assert request["cache_control"] == {"type": "ephemeral"}
    assert request["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in request["betas"]
    for forbidden in ("temperature", "top_p", "top_k", "tool_choice", "thinking"):
        assert forbidden not in request
    assert request["messages"] == [{"role": "user", "content": "Do the task"}]


def test_turn_limit_stops_the_loop():
    client = RepeatingClient(
        message(tool_use("tu_1", "bash", {"command": "ls"}), stop_reason="tool_use")
    )

    result = run(client, limits=Limits(max_turns=3))

    assert result.stop is StopReason.TURN_LIMIT
    assert len(client.requests) == 3


def test_token_limit_stops_the_loop():
    client = RepeatingClient(
        message(
            tool_use("tu_1", "bash", {"command": "ls"}),
            stop_reason="tool_use",
            input_tokens=600,
            output_tokens=100,
        )
    )

    result = run(client, limits=Limits(max_total_tokens=1_000))

    assert result.stop is StopReason.TOKEN_LIMIT
    assert len(client.requests) == 2
    assert result.total_tokens == 1_400


@pytest.mark.parametrize(
    ("api_stop_reason", "expected"),
    [
        ("max_tokens", StopReason.MAX_TOKENS),
        ("model_context_window_exceeded", StopReason.CONTEXT_WINDOW),
        ("refusal", StopReason.REFUSAL),
    ],
)
def test_cut_off_responses_stop_without_running_tools(api_stop_reason: str, expected: StopReason):
    client = FakeClient(
        message(tool_use("tu_1", "bash", {"command": "ls"}), stop_reason=api_stop_reason)
    )
    tool = RecordingTool()

    result = run(client, tool)

    assert result.stop is expected
    assert tool.calls == []


def test_pause_turn_is_sent_back_to_continue():
    client = FakeClient(
        message(text("Working..."), stop_reason="pause_turn"),
        message(text("Done.")),
    )

    result = run(client)

    assert result.stop is StopReason.DONE
    assert client.requests[1]["messages"][-1]["role"] == "assistant"
