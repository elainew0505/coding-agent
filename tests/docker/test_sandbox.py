import os
import subprocess
from pathlib import Path

import pytest

from coding_agent.sandbox import Sandbox, SandboxConfig

pytestmark = pytest.mark.docker


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.txt").write_text("hi\n")
    return tmp_path


def container_exists(name: str) -> bool:
    out = subprocess.run(
        ["docker", "ps", "--all", "--quiet", "--filter", f"name=^{name}$"],
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(out.stdout.strip())


def has_default_route(sandbox: Sandbox) -> bool:
    # A default route (destination 00000000) exists only when the container has a network.
    routes = sandbox.exec(["cat", "/proc/net/route"]).output.splitlines()[1:]
    return any(line.split()[1] == "00000000" for line in routes)


def test_network_is_off_by_default(repo: Path):
    with Sandbox(repo, SandboxConfig()) as sandbox:
        assert not has_default_route(sandbox)


def test_network_is_on_when_allowed(repo: Path):
    with Sandbox(repo, SandboxConfig(allow_network=True)) as sandbox:
        assert has_default_route(sandbox)


def test_git_directory_is_read_only(repo: Path):
    with Sandbox(repo, SandboxConfig()) as sandbox:
        result = sandbox.exec(["sh", "-c", "echo x > /workspace/.git/evil"])

    assert result.exit_code != 0
    assert "Read-only file system" in result.output
    assert not (repo / ".git" / "evil").exists()


def test_workspace_files_are_written_as_the_host_user(repo: Path):
    with Sandbox(repo, SandboxConfig()) as sandbox:
        result = sandbox.exec(["sh", "-c", "echo new > /workspace/b.txt"])

    assert result.exit_code == 0
    assert (repo / "b.txt").read_text() == "new\n"
    assert (repo / "b.txt").stat().st_uid == os.getuid()


def test_api_key_is_not_in_the_container_environment(repo: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")

    with Sandbox(repo, SandboxConfig()) as sandbox:
        env = sandbox.exec(["env"]).output

    assert "ANTHROPIC" not in env
    assert "sk-ant-" not in env


def test_container_is_removed_on_exit(repo: Path):
    with Sandbox(repo, SandboxConfig()) as sandbox:
        name = sandbox.name
        assert container_exists(name)

    assert not container_exists(name)


def test_container_is_removed_when_the_body_raises(repo: Path):
    name = ""
    with pytest.raises(RuntimeError), Sandbox(repo, SandboxConfig()) as sandbox:
        name = sandbox.name
        raise RuntimeError("boom")

    assert name
    assert not container_exists(name)
