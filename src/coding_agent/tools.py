"""
tools.py - the one tool, run_command (SPEC.md § The run_command tool).

The model sends {"command": ..., "timeout": ...}; we validate it, run it in the
sandbox, and hand back text for the tool_result block.
"""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from coding_agent.config import DEFAULT_TIMEOUT, MAX_TIMEOUT
from coding_agent.prompts import TOOL_DESCRIPTION
from coding_agent.sandbox import SandboxError


class RunCommandInput(BaseModel):
    """What the model may send. Checked before anything runs."""

    model_config = ConfigDict(extra="forbid")

    command: str
    timeout: int = Field(default=DEFAULT_TIMEOUT, ge=1, le=MAX_TIMEOUT)

    @field_validator("command")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("command must not be empty")
        return value


# Written out by hand (not generated) so it is byte-identical on every request,
# which prompt caching needs. test_tools.py checks it agrees with RunCommandInput.
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "The bash command to run, e.g. `python -m pytest -q`.",
        },
        "timeout": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_TIMEOUT,
            "default": DEFAULT_TIMEOUT,
            "description": f"Seconds before the command is stopped (default {DEFAULT_TIMEOUT}).",
        },
    },
    "required": ["command"],
    "additionalProperties": False,
}

TOOL = {"name": "run_command", "description": TOOL_DESCRIPTION, "input_schema": INPUT_SCHEMA}


@dataclass
class ToolResult:
    content: str
    is_error: bool = False


def run_command(tool_input, sandbox):
    """Validate, run, and describe the outcome as a ToolResult.

    is_error is only for things that went wrong around the command: bad input,
    a timeout, a sandbox failure. A non-zero exit code is a normal result (L7):
    a failing test run is information for the model.
    Ctrl+C (KeyboardInterrupt) is not caught; the loop handles it (L8).
    """
    try:
        args = RunCommandInput.model_validate(tool_input)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in err['loc']) or 'input'}: {err['msg']}"
            for err in e.errors(include_url=False)
        )
        return ToolResult(f"Invalid input: {problems}", is_error=True)

    try:
        result = sandbox.run(args.command, args.timeout)
    except SandboxError as e:
        return ToolResult(f"Sandbox error: {e}", is_error=True)

    return ToolResult(str(result), is_error=result.timed_out)
