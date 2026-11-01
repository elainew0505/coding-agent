"""
test_main.py - review, apply, trajectory and the coding-agent command
(SPEC.md § How a run works, D1-D6, § Trajectory).

The review and trajectory tests use a real git repo in a temp folder but no
Docker. The end-to-end test runs the whole command in a real container with
a scripted model in place of Claude, so it costs nothing.

Run them like any test:
  uv run pytest tests/test_main.py -v

Or by hand:
  uv run python tests/test_main.py              # every test, verbose
  uv run python tests/test_main.py review       # only tests whose name matches
  uv run python tests/test_main.py --list       # list the test names
"""

import json
import subprocess
import sys

import pytest

from coding_agent.__main__ import main
from coding_agent.agent import Run, review, write_trajectory
from coding_agent.sandbox import LABEL, Diff

from fakes import FakeModel, reply

FIXED = "def add(a, b):\n    return a + b\n"
BROKEN = "def add(a, b):\n    return a - b\n"


def fix_patch(repo):
    """The patch that fixes app.py, made with git on the host; app.py is left as it was."""
    (repo / "app.py").write_text(FIXED)
    patch = subprocess.run(
        ["git", "-C", str(repo), "diff", "--binary"], capture_output=True, check=True
    ).stdout
    (repo / "app.py").write_text(BROKEN)
    return Diff(patch=patch, stat=" app.py | 2 +-\n")


def answering(answer):
    """An `ask` that returns `answer`, or raises it if it's an exception."""

    def ask(_prompt):
        if isinstance(answer, BaseException):
            raise answer
        return answer

    return ask


def quiet(*_):
    """Swallow printing."""


# --- review (D2-D5) ----------------------------------------------------------


def test_review_empty_diff_says_so_and_does_not_ask(repo):
    def never(_prompt):
        raise AssertionError("asked")

    assert review(Diff(b"", ""), repo, ask=never, out=quiet) == "no changes"


def test_review_apply_writes_the_change(repo):
    assert review(fix_patch(repo), repo, ask=answering("a"), out=quiet) == "applied"
    assert (repo / "app.py").read_text() == FIXED


@pytest.mark.parametrize("answer", ["d", "", "yes", EOFError(), KeyboardInterrupt()])
def test_review_anything_but_apply_discards(repo, answer):
    assert review(fix_patch(repo), repo, ask=answering(answer), out=quiet) == "discarded"
    assert (repo / "app.py").read_text() == BROKEN


def test_review_shows_the_stat_then_the_diff(repo):
    printed = []
    review(fix_patch(repo), repo, ask=answering("d"), out=printed.append)
    assert printed[0] == " app.py | 2 +-\n"
    assert "+    return a + b" in printed[1]


def test_failed_apply_saves_the_patch(repo):
    diff = fix_patch(repo)
    (repo / "app.py").write_text("changed on the host meanwhile\n")
    assert review(diff, repo, ask=answering("a"), out=quiet) == "saved"
    assert (repo / "app.py").read_text() == "changed on the host meanwhile\n"
    [saved] = (repo / ".agent" / "patches").iterdir()
    assert saved.read_bytes() == diff.patch


# --- trajectory --------------------------------------------------------------


def test_trajectory_holds_the_whole_run_and_no_key(repo, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    first = reply("ls", text="Looking.")
    run = Run(
        messages=[
            {"role": "user", "content": "fix it"},
            {"role": "assistant", "content": first.content},
        ],
        stop_reason="done",
        calls=1,
    )
    path = write_trajectory(repo, "fix it", run, "applied")

    assert path.parent == repo / ".agent" / "trajectories"
    text = path.read_text()
    assert "sk-ant-" not in text
    data = json.loads(text)
    assert data["task"] == "fix it"
    assert data["outcome"] == "applied"
    assert data["messages"][1]["content"][1]["input"] == {"command": "ls"}


def test_trajectory_is_written_even_if_the_run_never_started(repo):
    data = json.loads(write_trajectory(repo, "t", None, "not reviewed").read_text())
    assert data["stop_reason"] == "not started"
    assert data["messages"] == []


# --- the command -------------------------------------------------------------


def test_usage_error_without_a_task(capsys):
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err


def test_outside_a_repo_is_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["fix it"]) == 1
    assert "not inside a git repo" in capsys.readouterr().err


def test_end_to_end_with_a_scripted_model(image, repo, monkeypatch):
    """The whole command, run from a subfolder, with a fake model playing Claude."""
    monkeypatch.chdir(repo / "sub")
    monkeypatch.setattr("builtins.input", answering("a"))
    model = FakeModel(reply("sed -i 's/a - b/a + b/' app.py"), reply(text="Fixed add()."))

    assert main(["fix add"], model=model, image=image) == 0

    assert (repo / "app.py").read_text() == FIXED
    [trajectory] = (repo / ".agent" / "trajectories").iterdir()
    data = json.loads(trajectory.read_text())
    assert (data["stop_reason"], data["outcome"]) == ("done", "applied")
    left = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label={LABEL}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert left.stdout.strip() == ""


# --- run by hand -------------------------------------------------------------

if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--list"]:
        sys.exit(pytest.main([__file__, "--collect-only", "-q"]))
    selection = ["-k", " or ".join(args)] if args else []
    quiet_warning = ["-W", "ignore::pytest.PytestAssertRewriteWarning"]  # see test_agent.py
    sys.exit(pytest.main([__file__, "-v", *quiet_warning, *selection]))
