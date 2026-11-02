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


def answering(*answers):
    """An `ask` that gives `answers` in order, raising any that are exceptions."""
    remaining = list(answers)

    def ask(_prompt):
        answer = remaining.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    return ask


def quiet(*_):
    """Swallow printing."""


def only_trajectory(repo):
    """The one trajectory a run wrote, as a dict."""
    [trajectory] = (repo / ".agent" / "trajectories").iterdir()
    return json.loads(trajectory.read_text())


def no_containers_left():
    left = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label={LABEL}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return left.stdout.strip() == ""


# --- review (D2-D5) ----------------------------------------------------------


def test_review_empty_diff_says_so_and_does_not_ask(repo):
    def never(_prompt):
        raise AssertionError("asked")

    assert review(Diff(b"", ""), repo, ask=never, out=quiet) == "no changes"


def test_review_apply_writes_the_change(repo):
    assert review(fix_patch(repo), repo, ask=answering("a"), out=quiet) == "applied"
    assert (repo / "app.py").read_text() == FIXED


@pytest.mark.parametrize("answer", ["d", "discard", " D ", EOFError(), KeyboardInterrupt()])
def test_review_discards_on_d_ctrl_c_or_ctrl_d(repo, answer):
    assert review(fix_patch(repo), repo, ask=answering(answer), out=quiet) == "discarded"
    assert (repo / "app.py").read_text() == BROKEN


def test_review_asks_again_on_anything_else(repo):
    # A typo or a natural "y" must not throw away the whole run's work.
    printed = []
    ask = answering("y", "", "yes", "a")
    assert review(fix_patch(repo), repo, ask=ask, out=printed.append) == "applied"
    assert printed.count("Type a to apply or d to discard.") == 3


def test_review_shows_the_stat_then_the_diff(repo):
    printed = []
    review(fix_patch(repo), repo, ask=answering("d"), out=printed.append)
    assert printed[0] == " app.py | 2 +-\n"
    assert "+    return a + b" in printed[1]


def test_review_shows_terminal_escapes_in_the_diff_visibly(repo):
    # An escape sequence in a changed file could erase lines of the diff on screen.
    printed = []
    patch = b"+evil()\x1b[1A\x1b[2K\n"
    review(Diff(patch, " x | 1 +\n"), repo, ask=answering("d"), out=printed.append)
    assert "\x1b" not in printed[1]
    assert "evil()\\x1b[1A\\x1b[2K" in printed[1]


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


@pytest.mark.parametrize("argv", [["--model"], ["--model", "claude-opus-5-5"], ["--verbose", "x"]])
def test_usage_error_for_a_bad_command_line(argv, capsys):
    assert main(argv) == 2
    assert "usage" in capsys.readouterr().err


def test_outside_a_repo_is_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["fix it"]) == 1
    assert "not inside a git repo" in capsys.readouterr().err


def test_missing_api_key_fails_before_docker_starts(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

    def no_docker(*_args):
        raise AssertionError("started a container")

    monkeypatch.setattr("coding_agent.__main__.Sandbox", no_docker)
    assert main(["fix it"]) == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
    assert not (repo / ".agent" / "trajectories").exists()  # nothing ran, nothing to save


def test_end_to_end_with_a_scripted_model(image, repo, monkeypatch):
    """The whole command, run from a subfolder, with a fake model playing Claude."""
    monkeypatch.chdir(repo / "sub")
    monkeypatch.setattr("builtins.input", answering("a"))
    model = FakeModel(reply("sed -i 's/a - b/a + b/' app.py"), reply(text="Fixed add()."))

    assert main(["fix add"], model=model, image=image) == 0

    assert (repo / "app.py").read_text() == FIXED
    data = only_trajectory(repo)
    assert (data["stop_reason"], data["outcome"]) == ("done", "applied")
    assert data["model"] == "claude-haiku-5-5"  # the default
    assert no_containers_left()


def test_ctrl_c_mid_task_still_reviews_and_cleans_up(image, repo, monkeypatch):
    monkeypatch.chdir(repo)
    monkeypatch.setattr("builtins.input", answering("d"))
    model = FakeModel(reply("sed -i 's/a - b/a + b/' app.py"), KeyboardInterrupt())

    assert main(["fix add"], model=model, image=image) == 0

    assert (repo / "app.py").read_text() == BROKEN  # discarded at the review
    data = only_trajectory(repo)
    assert (data["stop_reason"], data["outcome"]) == ("interrupted", "discarded")
    assert no_containers_left()


def test_crash_mid_task_still_cleans_up_and_keeps_the_history(image, repo, monkeypatch):
    monkeypatch.chdir(repo)
    model = FakeModel(reply("ls"), RuntimeError("bug in the harness"))

    with pytest.raises(RuntimeError):
        main(["fix add"], model=model, image=image)

    assert (repo / "app.py").read_text() == BROKEN
    data = only_trajectory(repo)
    assert (data["stop_reason"], data["outcome"]) == ("crashed", "not reviewed")
    assert len(data["messages"]) == 3  # task, the ls call, its result: what to debug from
    assert no_containers_left()


# --- run by hand -------------------------------------------------------------

if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--list"]:
        sys.exit(pytest.main([__file__, "--collect-only", "-q"]))
    selection = ["-k", " or ".join(args)] if args else []
    quiet_warning = ["-W", "ignore::pytest.PytestAssertRewriteWarning"]  # see test_agent.py
    sys.exit(pytest.main([__file__, "-v", *quiet_warning, *selection]))
