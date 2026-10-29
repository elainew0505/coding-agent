import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from coding_agent.sandbox import CAPTURE_LIMIT, Sandbox, SandboxConfig
from coding_agent.tools.bash import BashTool

pytestmark = pytest.mark.docker


@pytest.fixture
def sandbox(tmp_path: Path) -> Iterator[Sandbox]:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    with Sandbox(tmp_path, SandboxConfig()) as sandbox:
        yield sandbox


@pytest.fixture
def bash(sandbox: Sandbox) -> BashTool:
    return BashTool(sandbox, timeout=3)


def run(bash: BashTool, command: str) -> str:
    result = bash({"command": command})
    assert not result.is_error, result.content
    return result.content


def test_starts_in_the_workspace(bash: BashTool):
    assert run(bash, "pwd") == "/workspace\n\n[exit code: 0]"


def test_directory_and_variables_persist_between_calls(bash: BashTool):
    run(bash, "mkdir -p sub && cd sub")
    run(bash, "export GREETING=hi")

    assert run(bash, 'echo "$PWD $GREETING"').startswith("/workspace/sub hi\n")


def test_restart_resets_directory_and_variables(bash: BashTool):
    run(bash, "cd /tmp && export GREETING=hi")

    bash({"restart": True})

    assert run(bash, 'echo "$PWD [$GREETING]"').startswith("/workspace []\n")


def test_stderr_and_exit_code_are_reported(bash: BashTool):
    assert run(bash, "echo out; echo err >&2; exit_code() { return 7; }; exit_code") == (
        "out\nerr\n\n[exit code: 7]"
    )


def test_output_without_a_trailing_newline(bash: BashTool):
    assert run(bash, "printf abc") == "abc\n[exit code: 0]"


def test_multiline_commands_and_quotes(bash: BashTool):
    command = "cat <<'END'\nit's \"$HOME\"\nEND\necho second"

    assert run(bash, command).startswith('it\'s "$HOME"\nsecond\n')


def test_syntax_error_is_an_ordinary_failure(bash: BashTool):
    content = run(bash, "echo 'unterminated")

    assert "[exit code: 2]" in content
    assert run(bash, "echo still here").startswith("still here\n")


def test_command_reading_stdin_gets_eof_instead_of_the_next_command(bash: BashTool):
    assert run(bash, "cat") == "(no output)\n[exit code: 0]"
    assert run(bash, "echo next").startswith("next\n")


def test_command_that_never_exits_times_out_and_the_next_command_works(bash: BashTool):
    run(bash, "cd /tmp")

    result = bash({"command": "echo started; sleep infinity"})

    assert result.is_error
    assert result.content.startswith("started\n")
    assert "timed out after 3 seconds" in result.content
    assert run(bash, "pwd").startswith("/workspace\n")


def test_timeout_kills_background_processes(bash: BashTool):
    run(bash, "sleep 1000 > /dev/null 2>&1 &")
    bash({"command": "sleep infinity"})

    assert "sleep" not in run(bash, "cat /proc/[0-9]*/cmdline | tr '\\0' ' '").replace(
        "sleep infinity", ""
    )


def test_exiting_the_shell_starts_a_new_one(bash: BashTool):
    result = bash({"command": "echo bye; exit 3"})

    assert result.is_error
    assert result.content.startswith("bye\n")
    assert run(bash, "echo back").startswith("back\n")


def test_large_output_is_truncated_with_a_count(bash: BashTool):
    content = run(bash, "head -c 100000 /dev/zero | tr '\\0' a")

    head, note, tail = content.split("\n", 2)
    assert note == "[... 70000 characters cut ...]"
    assert head == "a" * 15_000
    assert tail == "a" * 15_000 + "\n[exit code: 0]"


def test_output_beyond_the_capture_limit_is_counted_exactly(bash: BashTool):
    size = CAPTURE_LIMIT * 3

    content = run(bash, f"head -c {size} /dev/zero | tr '\\0' a")

    assert f"[... {size - 30_000} characters cut ...]" in content
    assert content.endswith("\n[exit code: 0]")


def test_endless_output_times_out(bash: BashTool):
    result = bash({"command": "yes"})

    assert result.is_error
    assert "timed out" in result.content
    assert run(bash, "echo ok").startswith("ok\n")
