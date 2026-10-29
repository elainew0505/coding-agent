"""The bash tool (`bash_20250124`): commands run in the sandbox's persistent shell."""

from typing import Protocol

from pydantic import BaseModel, ValidationError

from coding_agent.sandbox import COMMAND_TIMEOUT, CommandResult
from coding_agent.tools import ToolResult

MAX_OUTPUT = 30_000


class BashSession(Protocol):
    def bash(self, command: str, *, timeout: float) -> CommandResult: ...
    def restart_bash(self) -> None: ...


class BashInput(BaseModel):
    command: str | None = None
    restart: bool = False


class BashTool:
    def __init__(
        self,
        session: BashSession,
        *,
        timeout: float = COMMAND_TIMEOUT,
        max_output: int = MAX_OUTPUT,
    ):
        self.session = session
        self.timeout = timeout
        self.max_output = max_output

    def __call__(self, tool_input: dict[str, object]) -> ToolResult:
        try:
            request = BashInput.model_validate(tool_input)
        except ValidationError as exc:
            return ToolResult.error(f"Invalid bash input: {exc}")
        if request.restart:
            self.session.restart_bash()
            return ToolResult.ok("The shell was restarted.")
        if request.command is None:
            return ToolResult.error("Provide a command to run, or restart: true.")

        result = self.session.bash(request.command, timeout=self.timeout)
        output = truncate(result.output, self.max_output, already_cut=result.dropped)
        if result.timed_out:
            return ToolResult.error(
                f"{output}\nThe command timed out after {self.timeout:g} seconds and was killed. "
                "The shell was restarted: the working directory, variables and background "
                "processes were reset."
            )
        if result.exit_code < 0:
            return ToolResult.error(
                f"{output}\nThe command exited the shell. A new shell was started: the working "
                "directory and variables were reset."
            )
        # A non-zero exit code is an ordinary result the model can act on, not a tool failure.
        return ToolResult.ok(f"{output or '(no output)'}\n[exit code: {result.exit_code}]")


def truncate(text: str, limit: int, *, already_cut: int = 0) -> str:
    """Keep the start and end of `text` within `limit` characters, noting how much was cut.

    `already_cut` counts what was dropped from the middle of `text` before it reached
    us (`CommandResult.dropped`). That count is in bytes; it matches characters for
    ASCII output, which is what large outputs almost always are.
    """
    over = max(len(text) - limit, 0)
    if not over and not already_cut:
        return text
    keep = min(len(text), limit)
    head = text[: keep // 2]
    tail = text[len(text) - (keep - keep // 2) :]
    return f"{head}\n[... {over + already_cut} characters cut ...]\n{tail}"
