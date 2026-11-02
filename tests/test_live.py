"""
test_live.py - the task tests from SPEC.md § Acceptance tests, against the
real API.

THESE COST MONEY. ONLY THE USER RUNS THEM; AN AI AGENT NEVER DOES.

They are skipped unless you pass --live and ANTHROPIC_API_KEY is set. Each
test clones ~/code/toy-repo (or $TOY_REPO) into a temp folder first, so the
real toy repo is never changed. They use the default model (config.MODEL,
Claude Haiku 5.5) with the call cap lowered to 20.

  uv run pytest --live tests/test_live.py -v
  uv run python tests/test_live.py --live            # same, by hand
  uv run python tests/test_live.py --live fixes      # only tests whose name matches

The first live run also records one real reply to tests/fixtures/, which
test_recorded_reply_still_loads (not a live test) then checks on every run.
Commit that file.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from anthropic.types.beta import BetaMessage

from coding_agent.__main__ import main
from coding_agent.model import Model

FIXTURE = Path(__file__).parent / "fixtures" / "reply.json"


class RecordingModel(Model):
    """The real model, saving the first reply it gets as the fixture."""

    def query(self, messages):
        message = super().query(messages)
        if not FIXTURE.exists():
            FIXTURE.parent.mkdir(exist_ok=True)
            FIXTURE.write_text(message.model_dump_json(indent=2))
        return message


def run_agent(task, repo, image, monkeypatch):
    """Run `coding-agent "<task>"` in `repo`, answer "apply", return the trajectory."""
    monkeypatch.chdir(repo)
    monkeypatch.setattr("builtins.input", lambda _prompt: "a")
    monkeypatch.setattr("coding_agent.agent.MAX_CALLS", 20)  # keep a runaway run cheap
    assert main([task], model=RecordingModel(), image=image) == 0
    [trajectory] = (repo / ".agent" / "trajectories").iterdir()
    return json.loads(trajectory.read_text())


def toy_tests_pass(repo, *pytest_args):
    """Run the toy repo's own tests on the host, against the applied change.

    No bytecode caches: a fix the same size as the code it replaces, made in
    the same second, would otherwise run the stale cached version.
    """
    r = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", *pytest_args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode == 0


def commands(data):
    """Every command the model ran, in order."""
    return [
        block["input"].get("command", "")
        for message in data["messages"]
        if message["role"] == "assistant"
        for block in message["content"]
        if block.get("type") == "tool_use"
    ]


# --- the four task tests -----------------------------------------------------


def test_explains_the_project_without_changing_it(live, toy_repo, monkeypatch):
    data = run_agent(
        "What does this project do? Don't change any files.", toy_repo, live, monkeypatch
    )
    assert data["stop_reason"] == "done"
    assert data["outcome"] == "no changes"
    assert commands(data)  # it read files rather than guessing


def test_fixes_the_planted_off_by_one_bug(live, toy_repo, monkeypatch):
    assert not toy_tests_pass(toy_repo)
    data = run_agent("Some tests fail. Find the bug and fix it.", toy_repo, live, monkeypatch)
    assert data["outcome"] == "applied"
    assert toy_tests_pass(toy_repo)


def test_adds_a_function_with_a_test_and_runs_the_tests(live, toy_repo, monkeypatch):
    task = (
        "Add a function clamp(value, low, high) to calc/ranges.py that limits value to the "
        "range low..high, export it from the calc package, add a test for it in "
        "tests/test_ranges.py, and run the tests."
    )
    data = run_agent(task, toy_repo, live, monkeypatch)
    assert data["outcome"] == "applied"
    assert "def clamp" in (toy_repo / "calc" / "ranges.py").read_text()
    assert "clamp" in (toy_repo / "tests" / "test_ranges.py").read_text()
    assert any("pytest" in c for c in commands(data))
    # Only clamp's tests are expected to pass here: the planted bug is still there.
    assert toy_tests_pass(toy_repo, "-k", "clamp")


def test_recovers_from_a_wrong_fix(live, toy_repo, monkeypatch):
    # A wrong first attempt, already made (uncommitted): it overshoots by one.
    ranges = toy_repo / "calc" / "ranges.py"
    wrong = ranges.read_text().replace(
        "return list(range(start, end))", "return list(range(start, end + 2))"
    )
    assert "end + 2" in wrong  # the toy repo still has the original bug to build on
    ranges.write_text(wrong)
    assert not toy_tests_pass(toy_repo)
    task = "Someone already tried to fix inclusive_range, but the tests still fail. Make them pass."
    data = run_agent(task, toy_repo, live, monkeypatch)
    assert data["outcome"] == "applied"
    assert toy_tests_pass(toy_repo)
    assert "end + 2" not in ranges.read_text()


# --- the recorded reply (not live: runs every time once recorded) ------------


def test_recorded_reply_still_loads():
    """Our fake replies are BetaMessages; this checks a real one still parses
    the same way with the installed SDK."""
    if not FIXTURE.exists():
        pytest.skip("no recorded reply yet: run `uv run pytest --live` once, then commit it")
    text = FIXTURE.read_text()
    assert "sk-ant-" not in text
    message = BetaMessage.model_validate_json(text)
    assert message.role == "assistant"
    assert message.content


# --- run by hand -------------------------------------------------------------

if __name__ == "__main__":
    args = sys.argv[1:]
    flags = [a for a in args if a.startswith("--")]
    names = [a for a in args if not a.startswith("--")]
    if flags == ["--list"]:
        sys.exit(pytest.main([__file__, "--collect-only", "-q"]))
    selection = ["-k", " or ".join(names)] if names else []
    quiet_warning = ["-W", "ignore::pytest.PytestAssertRewriteWarning"]  # see test_agent.py
    sys.exit(pytest.main([__file__, "-v", *flags, *quiet_warning, *selection]))
