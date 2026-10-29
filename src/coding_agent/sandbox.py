"""The Docker sandbox: one locked-down container per run.

Docker is driven through the `docker` CLI with argv lists (never a shell
string), see docs/decisions/0002-docker-cli-not-sdk.md. Every model-issued
command runs in here, never on the host.
"""

import os
import re
import select
import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

DEFAULT_IMAGE = (
    "python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159"
)
WORKSPACE = "/workspace"
CONTAINER_LABEL = "coding-agent"
COMMAND_TIMEOUT = 120
"""Seconds a command may run before it is killed (SPEC.md § Limits)."""

# Bytes of bash output kept in memory per command: half from the start, half from the
# end. Bounds memory for commands like `yes`; the tool truncates far below this anyway.
CAPTURE_LIMIT = 2_000_000


@dataclass(frozen=True)
class SandboxConfig:
    image: str = DEFAULT_IMAGE
    allow_network: bool = False
    memory: str = "2g"
    cpus: str = "2"
    pids_limit: int = 512


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    output: str
    timed_out: bool = False
    dropped: int = 0
    """Bytes cut from the middle of `output` while capturing it."""


def docker_run_args(
    name: str, workspace: Path, config: SandboxConfig, *, uid: int, gid: int
) -> list[str]:
    """The `docker run` command that starts a run's container."""
    args = [
        "docker", "run", "--detach",
        "--name", name,
        "--label", CONTAINER_LABEL,
        "--user", f"{uid}:{gid}",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--memory", config.memory,
        "--cpus", config.cpus,
        "--pids-limit", str(config.pids_limit),
        # The host user has no home directory in the image.
        "--env", "HOME=/tmp",
        "--volume", f"{workspace}:{WORKSPACE}",
    ]  # fmt: skip
    git_dir = workspace / ".git"
    if git_dir.exists():
        # Mounted read-only on top of the workspace so the agent can't commit.
        args += ["--volume", f"{git_dir}:{WORKSPACE}/.git:ro"]
    if not config.allow_network:
        args += ["--network", "none"]
    args += ["--workdir", WORKSPACE, config.image, "sleep", "infinity"]
    return args


class Sandbox:
    """Starts the container on enter and always removes it on exit."""

    def __init__(self, workspace: Path, config: SandboxConfig):
        self.workspace = workspace.resolve()
        self.config = config
        self.name = f"coding-agent-{uuid.uuid4().hex[:12]}"
        self._shell: _Shell | None = None

    def __enter__(self) -> Self:
        args = docker_run_args(
            self.name, self.workspace, self.config, uid=os.getuid(), gid=os.getgid()
        )
        try:
            subprocess.run(args, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError:
            self._remove()
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._shell is not None:
            self._shell.close()
        self._remove()

    def exec(self, command: list[str], *, timeout: float = COMMAND_TIMEOUT) -> CommandResult:
        """Run one command in the container; stdout and stderr are combined."""
        try:
            completed = subprocess.run(
                ["docker", "exec", self.name, *command],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as expired:
            return CommandResult(-1, _decode(expired.output), timed_out=True)
        return CommandResult(completed.returncode, _decode(completed.stdout))

    def bash(self, command: str, *, timeout: float) -> CommandResult:
        """Run `command` in the run's persistent bash session.

        The working directory, variables and functions carry over between calls. On
        timeout, or if the command exits the shell, the session is reset (see
        `restart_bash`) and the result says so through `timed_out` or `exit_code`.
        """
        if self._shell is None:
            self._shell = _Shell(self.name)
        result = self._shell.run(command, timeout=timeout)
        if result.timed_out or result.exit_code < 0:
            self.restart_bash()
        return result

    def restart_bash(self) -> None:
        """Kill every process in the container except PID 1; the next call starts a new shell.

        Killing everything, not just the shell, also stops background jobs and
        anything still holding the old shell's output pipe.
        """
        if self._shell is not None:
            self._shell.close()
            self._shell = None
        subprocess.run(
            ["docker", "exec", self.name, "bash", "-c", "kill -9 -1"],
            capture_output=True,
            check=False,
            timeout=30,
        )

    def _remove(self) -> None:
        subprocess.run(["docker", "rm", "--force", self.name], capture_output=True, check=False)


def _decode(output: bytes | None) -> str:
    return (output or b"").decode(errors="replace")


SHELL_EXITED = -1
"""`CommandResult.exit_code` when the command ended the shell itself (e.g. `exit 3`)."""


class _Capture:
    """Keeps the first and last `limit // 2` bytes of a stream and counts the rest."""

    def __init__(self, limit: int):
        self._half = limit // 2
        self._head = bytearray()
        self._tail = bytearray()
        self.dropped = 0

    def feed(self, chunk: bytes) -> None:
        room = self._half - len(self._head)
        if room > 0:
            self._head += chunk[:room]
            chunk = chunk[room:]
        self._tail += chunk
        excess = len(self._tail) - self._half
        if excess > 0:
            del self._tail[:excess]
            self.dropped += excess

    def tail_window(self) -> bytes:
        """The end of the stream, where the marker line appears."""
        return bytes(self._head + self._tail) if not self.dropped else bytes(self._tail)

    def text(self, *, end: int | None = None) -> str:
        """Everything kept, or only what comes before offset `end` of `tail_window()`."""
        if end is None:
            return _decode(bytes(self._head + self._tail))
        if not self.dropped:
            return _decode(bytes((self._head + self._tail)[:end]))
        return _decode(bytes(self._head + self._tail[:end]))


class _Shell:
    """One `docker exec -i ... bash` process that commands are written to in turn.

    Each command is passed to `eval` as a single quoted word, so it can't break out of
    the wrapper, and a syntax error is an ordinary failed command rather than a shell
    waiting for more input. Its stdin is /dev/null so it can't swallow the commands
    that follow. After it, the shell prints a marker line with a random token and the
    exit code; output up to the marker belongs to the command.
    """

    def __init__(self, container: str):
        self._process = subprocess.Popen(
            ["docker", "exec", "--interactive", container, "bash", "--noprofile", "--norc"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )

    def run(self, command: str, *, timeout: float) -> CommandResult:
        assert self._process.stdin is not None and self._process.stdout is not None
        token = f"__coding_agent_done_{uuid.uuid4().hex}__"
        done = re.compile(rb"\n" + token.encode() + rb" (\d+)\n")
        # The marker starts with a newline in case the output doesn't end with one.
        script = f"eval {shlex.quote(command)} < /dev/null\nprintf '\\n%s %d\\n' {token} $?\n"
        capture = _Capture(CAPTURE_LIMIT)
        try:
            self._process.stdin.write(script.encode())
        except BrokenPipeError:
            return self._exited(capture)

        fd = self._process.stdout.fileno()
        deadline = time.monotonic() + timeout
        while True:
            match = done.search(capture.tail_window())
            if match:
                output = capture.text(end=match.start())
                return CommandResult(int(match.group(1)), output, dropped=capture.dropped)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return CommandResult(-1, capture.text(), timed_out=True, dropped=capture.dropped)
            readable, _, _ = select.select([fd], [], [], remaining)
            if not readable:
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                return self._exited(capture)
            capture.feed(chunk)

    def _exited(self, capture: _Capture) -> CommandResult:
        self._process.wait(timeout=10)
        return CommandResult(SHELL_EXITED, capture.text(), dropped=capture.dropped)

    def close(self) -> None:
        if self._process.stdin is not None:
            self._process.stdin.close()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        if self._process.stdout is not None:
            self._process.stdout.close()
