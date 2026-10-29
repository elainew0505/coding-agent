"""The Docker sandbox: one locked-down container per run.

Docker is driven through the `docker` CLI with argv lists (never a shell
string), see docs/decisions/0001-docker-cli-not-sdk.md. Every model-issued
command runs in here, never on the host.
"""

import os
import subprocess
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
        self._remove()

    def exec(
        self, command: list[str], *, timeout: float = 120, stdin: str | None = None
    ) -> CommandResult:
        """Run one command in the container; stdout and stderr are combined."""
        args = ["docker", "exec"]
        if stdin is not None:
            args.append("--interactive")
        args += [self.name, *command]
        try:
            completed = subprocess.run(
                args,
                input=stdin.encode() if stdin is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as expired:
            return CommandResult(-1, _decode(expired.output), timed_out=True)
        return CommandResult(completed.returncode, _decode(completed.stdout))

    def _remove(self) -> None:
        subprocess.run(["docker", "rm", "--force", self.name], capture_output=True, check=False)


def _decode(output: bytes | None) -> str:
    return (output or b"").decode(errors="replace")
