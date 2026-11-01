"""
test_tools.py - the run_command tool (SPEC.md § The run_command tool, L7).
Unit tests only: a fake sandbox, no Docker, no API.

Run them like any test:
  uv run pytest tests/test_tools.py -v

Or by hand:
  uv run python tests/test_tools.py             # every test, verbose
  uv run python tests/test_tools.py invalid     # only tests whose name matches
  uv run python tests/test_tools.py --list      # list the test names
"""

import sys

import pytest

from coding_agent.config import DEFAULT_TIMEOUT, MAX_TIMEOUT
from coding_agent.sandbox import CommandResult, SandboxError
from coding_agent.tools import INPUT_SCHEMA, TOOL, RunCommandInput, run_command

from fakes import FakeSandbox  # tests/fakes.py


def test_runs_the_command_with_the_default_timeout():
    sb = FakeSandbox(CommandResult(0, "hello\n"))
    out = run_command({"command": "echo hello"}, sb)
    assert sb.commands == [("echo hello", DEFAULT_TIMEOUT)]
    assert out.content == "exit_code: 0\nhello\n"
    assert not out.is_error


def test_nonzero_exit_is_a_normal_result():
    out = run_command({"command": "pytest"}, FakeSandbox(CommandResult(1, "1 failed")))
    assert out.content.startswith("exit_code: 1\n")
    assert not out.is_error


def test_timeout_is_an_error():
    timed_out = CommandResult(124, "\n[command timed out after 1s and was stopped]", True)
    out = run_command({"command": "sleep 99", "timeout": 1}, FakeSandbox(timed_out))
    assert out.is_error
    assert "timed out" in out.content


def test_sandbox_failure_is_an_error():
    out = run_command({"command": "ls"}, FakeSandbox(error=SandboxError("docker exec failed")))
    assert out.is_error
    assert "docker exec failed" in out.content


@pytest.mark.parametrize(
    "tool_input",
    [
        {},
        {"command": ""},
        {"command": "   "},
        {"command": "ls", "timeout": 0},
        {"command": "ls", "timeout": MAX_TIMEOUT + 1},
        {"command": "ls", "cwd": "/"},
    ],
)
def test_invalid_input_is_an_error_and_nothing_runs(tool_input):
    sb = FakeSandbox()
    out = run_command(tool_input, sb)
    assert out.is_error
    assert out.content.startswith("Invalid input: ")
    assert "errors.pydantic.dev" not in out.content  # short message, no docs links
    assert sb.commands == []


def test_ctrl_c_is_not_swallowed():
    with pytest.raises(KeyboardInterrupt):
        run_command({"command": "sleep 99"}, FakeSandbox(error=KeyboardInterrupt()))


def test_input_schema_agrees_with_the_model():
    generated = RunCommandInput.model_json_schema()
    assert INPUT_SCHEMA["required"] == generated["required"] == ["command"]
    assert INPUT_SCHEMA["properties"].keys() == generated["properties"].keys()
    timeout = generated["properties"]["timeout"]
    assert INPUT_SCHEMA["properties"]["timeout"]["minimum"] == timeout["minimum"]
    assert INPUT_SCHEMA["properties"]["timeout"]["maximum"] == timeout["maximum"]
    assert INPUT_SCHEMA["properties"]["timeout"]["default"] == timeout["default"]
    assert INPUT_SCHEMA["additionalProperties"] is generated["additionalProperties"] is False


def test_tool_definition_shape():
    assert TOOL["name"] == "run_command"
    assert TOOL["input_schema"] is INPUT_SCHEMA
    assert TOOL.get("description", "").strip()


# --- run by hand -------------------------------------------------------------

if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--list"]:
        sys.exit(pytest.main([__file__, "--collect-only", "-q"]))
    selection = ["-k", " or ".join(args)] if args else []
    sys.exit(pytest.main([__file__, "-v", *selection]))
