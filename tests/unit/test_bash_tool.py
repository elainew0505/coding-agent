from coding_agent.sandbox import CommandResult
from coding_agent.tools.bash import BashTool


class FakeSession:
    def __init__(self, result: CommandResult | None = None):
        self.result = result or CommandResult(0, "")
        self.commands: list[tuple[str, float]] = []
        self.restarts = 0

    def bash(self, command: str, *, timeout: float) -> CommandResult:
        self.commands.append((command, timeout))
        return self.result

    def restart_bash(self) -> None:
        self.restarts += 1


def test_runs_the_command_with_the_timeout_and_reports_the_exit_code():
    session = FakeSession(CommandResult(1, "boom\n"))

    result = BashTool(session, timeout=5)({"command": "false"})

    assert session.commands == [("false", 5)]
    assert result.content == "boom\n\n[exit code: 1]"
    assert not result.is_error


def test_says_when_there_was_no_output():
    result = BashTool(FakeSession())({"command": "true"})

    assert result.content == "(no output)\n[exit code: 0]"


def test_restart_restarts_the_session_without_running_anything():
    session = FakeSession()

    result = BashTool(session)({"restart": True})

    assert session.restarts == 1
    assert session.commands == []
    assert not result.is_error


def test_timeout_is_an_error_that_says_the_shell_was_reset():
    session = FakeSession(CommandResult(-1, "partial", timed_out=True))

    result = BashTool(session, timeout=2)({"command": "sleep infinity"})

    assert result.is_error
    assert result.content.startswith("partial\n")
    assert "timed out after 2 seconds" in result.content
    assert "restarted" in result.content


def test_exiting_the_shell_is_an_error():
    result = BashTool(FakeSession(CommandResult(-1, "")))({"command": "exit 3"})

    assert result.is_error
    assert "exited the shell" in result.content


def test_output_is_truncated_to_the_limit():
    session = FakeSession(CommandResult(0, "x" * 1000, dropped=50))

    result = BashTool(session, max_output=100)({"command": "big"})

    assert result.content == "x" * 50 + "\n[... 950 characters cut ...]\n" + "x" * 50 + (
        "\n[exit code: 0]"
    )


def test_missing_command_is_an_error():
    assert BashTool(FakeSession())({}).is_error


def test_invalid_input_is_an_error():
    session = FakeSession()

    result = BashTool(session)({"command": ["ls"]})

    assert result.is_error
    assert session.commands == []
