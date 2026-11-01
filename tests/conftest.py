"""Shared test setup: Docker detection, the --live option, a throwaway repo."""

import subprocess

import pytest

from coding_agent.config import IMAGE  # built with: docker build -t coding-agent:toy .


def pytest_addoption(parser):
    parser.addoption(
        "--live", action="store_true", help="run tests that call the real API (cost money)"
    )


def _docker_ready():
    """None if Docker is running and the image exists, else the reason to skip."""
    try:
        info = subprocess.run(["docker", "info"], capture_output=True, check=False, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "docker isn't installed or isn't responding"
    if info.returncode != 0:
        return "Docker isn't running"
    image = subprocess.run(
        ["docker", "image", "inspect", IMAGE], capture_output=True, check=False, timeout=15
    )
    if image.returncode != 0:
        return f"image {IMAGE} not built (docker build -t {IMAGE} .)"
    return None


@pytest.fixture(scope="session")
def image():
    """The agent image; skips the test when Docker or the image isn't available."""
    reason = _docker_ready()
    if reason:
        pytest.skip(reason)
    return IMAGE


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@local", *args],
        capture_output=True,
        check=True,
    )


@pytest.fixture
def repo(tmp_path):
    """A small git repo with a normal file, a subfolder, a tracked secret,
    an ignored file and an .agent/ folder."""
    root = tmp_path / "repo"
    (root / "sub").mkdir(parents=True)
    (root / "app.py").write_text("def add(a, b):\n    return a - b\n")
    (root / "sub" / "notes.txt").write_text("hello\n")
    (root / ".env").write_text("SECRET=1\n")  # tracked by mistake: still never copied (S9)
    (root / ".gitignore").write_text("ignored.txt\n")
    (root / "ignored.txt").write_text("local only\n")
    (root / ".agent" / "logs").mkdir(parents=True)
    (root / ".agent" / "logs" / "old.json").write_text("{}\n")
    _git(root, "init", "-q")
    _git(root, "add", "app.py", "sub", ".env", ".gitignore")
    _git(root, "commit", "-q", "-m", "init")
    return root
