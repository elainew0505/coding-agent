"""Shared test setup: Docker detection, the --live option, throwaway repos."""

import os
import subprocess
from pathlib import Path

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


# --- live tests (real API; only the user runs these) -------------------------

TOY_REPO = Path(os.environ.get("TOY_REPO", Path(__file__).resolve().parents[2] / "toy-repo"))


@pytest.fixture
def live(request, image):
    """Skips unless --live was passed and an API key is set. Needs Docker too."""
    if not request.config.getoption("--live"):
        pytest.skip("live test: pass --live to run (calls the real API, costs money)")
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        pytest.skip("live test: set ANTHROPIC_API_KEY")
    return image


@pytest.fixture
def toy_repo(tmp_path):
    """A fresh clone of the toy repo, so the real one is never changed."""
    if not (TOY_REPO / ".git").exists():
        pytest.skip(f"toy repo not found at {TOY_REPO} (set TOY_REPO)")
    clone = tmp_path / "toy-repo"
    subprocess.run(["git", "clone", "-q", str(TOY_REPO), str(clone)], check=True)
    return clone
