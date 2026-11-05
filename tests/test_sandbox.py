"""
test_sandbox.py - the sandbox safety tests from SPEC.md § Acceptance tests.

Most tests start a real container from coding-agent:toy and are skipped if
Docker isn't running or the image isn't built. The first few need no Docker.

Run them like any test:
  uv run pytest tests/test_sandbox.py -v

Or by hand, like sandbox.py's demo:
  uv run python tests/test_sandbox.py              # every test, verbose
  uv run python tests/test_sandbox.py network ctrl # only tests whose name matches
  uv run python tests/test_sandbox.py --list       # list the test names
"""

import os
import signal
import subprocess
import sys
import threading

import pytest

from coding_agent.config import MAX_TIMEOUT
from coding_agent.sandbox import (
    Sandbox,
    SandboxError,
    apply_patch,
    demo,
    find_repo_root,
    is_denied,
)

# --- no Docker needed --------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "config/.env",
        "certs/server.pem",
        "id_rsa",
        "id_ed25519.pub",
        ".agent/logs/x.json",
        ".ENV",  # case doesn't matter: macOS treats these as the same names
        "certs/Server.PEM",
        "deploy.KEY",
    ],
)
def test_secret_and_agent_paths_are_denied(path):
    assert is_denied(path)


@pytest.mark.parametrize("path", ["app.py", "id_utils.py", ".env.py.txt/ok", "docs/key_notes.md"])
def test_ordinary_paths_are_not_denied(path):
    assert not is_denied(path)


def test_outside_a_repo_is_refused(tmp_path):
    with pytest.raises(SandboxError, match="not inside a git repo"):
        find_repo_root(tmp_path)


def test_repo_root_is_found_from_a_subfolder(repo):
    assert find_repo_root(repo / "sub") == repo.resolve()


def test_kill_all_survives_a_hung_docker(monkeypatch):
    sandbox = Sandbox("unused")
    sandbox.started = True

    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 15)

    monkeypatch.setattr(subprocess, "run", hang)
    sandbox.kill_all()  # must not raise: the caller still goes on to review


# --- real container ----------------------------------------------------------


@pytest.fixture
def sb(image, repo):
    """A running sandbox holding a copy of `repo`, with a baseline taken."""
    with Sandbox(image) as sandbox:
        sandbox.copy_in(repo)
        sandbox.baseline()
        yield sandbox


def _container_exists(name):
    r = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"name=^{name}$"],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(r.stdout.strip())


def test_no_network(sb):
    r = sb.run(
        "python -c \"import urllib.request; urllib.request.urlopen('https://example.com', timeout=5)\""
    )
    assert r.exit_code != 0


def test_command_never_reaches_a_host_shell(sb, tmp_path):
    marker = tmp_path / "pwned"
    sb.run(f'echo hi"; touch {marker}; echo "')
    assert not marker.exists()


def test_nonzero_exit_is_returned_as_is(sb):
    r = sb.run("exit 3")
    assert r.exit_code == 3
    assert str(r).startswith("exit_code: 3\n")


def test_timeout_stops_the_command_and_the_next_one_works(sb):
    r = sb.run("sleep 1000", timeout=1)
    assert r.exit_code == 124
    assert r.timed_out
    assert "timed out" in r.output
    assert "ok" in sb.run("echo ok").output


def test_timeout_is_clamped_to_the_maximum(sb):
    # Asking for more than MAX_TIMEOUT still runs; it's just clamped.
    assert sb.run("true", timeout=MAX_TIMEOUT * 10).exit_code == 0


def test_background_process_does_not_hang_the_command(sb):
    r = sb.run("sleep 30 & echo started", timeout=10)
    assert r.exit_code == 0
    assert "started" in r.output


def test_huge_output_is_capped_with_head_and_tail(sb):
    r = sb.run("seq 1 1000000")
    assert len(r.output) < 10_200
    assert r.output.startswith("1\n2\n3\n")
    assert r.output.rstrip().endswith("1000000")
    assert "[truncated " in r.output


def test_endless_output_is_cut_off_at_the_timeout_and_capped(sb):
    # The cap is applied while reading, so even output that never ends stays small.
    r = sb.run("yes", timeout=2)
    assert r.exit_code == 124
    assert r.timed_out
    assert len(r.output) < 10_200
    assert r.output.startswith("y\ny\n")
    assert "[truncated " in r.output


def test_ctrl_c_kills_everything_but_pid_1(sb):
    # Send ourselves Ctrl+C (SIGINT) while a command with a background job runs.
    threading.Timer(2, os.kill, (os.getpid(), signal.SIGINT)).start()
    with pytest.raises(KeyboardInterrupt):
        sb.run("sleep 1000 & sleep 1000", timeout=60)

    # Every process's /proc/<pid>/stat is "pid (name) state ...". Zombies (Z)
    # are already dead; they only linger because PID 1 doesn't reap them.
    stats = sb.run("cat /proc/[0-9]*/stat 2>/dev/null").output.splitlines()
    alive_sleeps = [
        line
        for line in stats
        if "(sleep)" in line and not line.startswith("1 ") and line.split(") ")[1][0] != "Z"
    ]
    assert alive_sleeps == []


def test_secrets_ignored_files_and_agent_folder_are_not_copied(sb):
    files = sb.run("find . -type f | sort").output
    assert "./app.py" in files
    assert "./sub/notes.txt" in files
    assert ".env" not in files
    assert "ignored.txt" not in files
    assert ".agent" not in files


def test_workspace_has_no_git_folder(sb):
    assert sb.run("test -e .git").exit_code == 1


def test_diff_still_works_after_rm_rf_workspace(sb):
    sb.run("rm -rf /workspace")
    d = sb.diff()
    assert b"deleted file mode" in d.patch
    assert "app.py" in d.stat
    assert "notes.txt" in d.stat


def test_edits_to_a_tracked_but_ignored_file_are_in_the_diff(image, repo):
    # Force-added despite .gitignore: copied in, so its edits must show up too.
    (repo / "ignored.txt").write_text("tracked anyway\n")
    subprocess.run(["git", "-C", str(repo), "add", "-f", "ignored.txt"], check=True)
    with Sandbox(image) as sandbox:
        sandbox.copy_in(repo)
        sandbox.baseline()
        sandbox.run("echo edited >> ignored.txt")
        assert "ignored.txt" in sandbox.diff().stat


def test_copy_in_survives_lots_of_tar_warnings(image, repo, monkeypatch):
    # More than a pipe buffer (64 KB) of warnings used to deadlock copy-in.
    real_popen = subprocess.Popen

    def noisy_tar(argv, **kwargs):
        if argv[0] == "tar":
            script = 'yes "tar: warning" | head -c 200000 >&2; exec "$@"'
            argv = ["bash", "-c", script, "bash", *argv]
        return real_popen(argv, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", noisy_tar)
    with Sandbox(image) as sandbox:
        done = threading.Event()
        threading.Thread(target=lambda: (sandbox.copy_in(repo), done.set()), daemon=True).start()
        assert done.wait(30), "copy-in deadlocked"


def test_empty_diff_when_nothing_changed(sb):
    assert sb.diff().empty


def test_apply_from_a_subfolder_lands_at_the_top(image, repo):
    root = find_repo_root(repo / "sub")  # as if `coding-agent` were run from sub/
    with Sandbox(image) as sandbox:
        sandbox.copy_in(root)
        sandbox.baseline()
        sandbox.run("echo world >> sub/notes.txt")
        ok, msg = apply_patch(root, sandbox.diff().patch)
    assert ok, msg
    assert (repo / "sub" / "notes.txt").read_text() == "hello\nworld\n"


def test_failed_apply_check_leaves_the_repo_unchanged(sb, repo):
    sb.run("sed -i 's/a - b/a + b/' app.py")
    patch = sb.diff().patch
    (repo / "app.py").write_text("changed on the host meanwhile\n")
    ok, _ = apply_patch(repo, patch)
    assert not ok
    assert (repo / "app.py").read_text() == "changed on the host meanwhile\n"


@pytest.mark.parametrize("how", ["normal", "error", "ctrl_c"])
def test_no_container_is_left_behind(image, how):
    sandbox = Sandbox(image)
    raised = {"normal": None, "error": RuntimeError, "ctrl_c": KeyboardInterrupt}[how]
    try:
        with sandbox:
            assert _container_exists(sandbox.name)
            if raised:
                raise raised()
    except (RuntimeError, KeyboardInterrupt):
        pass
    assert not _container_exists(sandbox.name)


def test_demo_reports_a_failing_test_run_as_a_failure(image, repo, monkeypatch, capsys):
    # The image has python, but this repo has no tests, so pytest exits non-zero.
    # The demo must show that exit code, not claim "no python in this image".
    monkeypatch.chdir(repo)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "d")
    demo(image)
    out = capsys.readouterr().out
    assert "\n(no python in this image)\n" not in out  # the output line, not the echoed command
    assert "exit_code: 5" in out  # pytest's "no tests ran"


# --- run by hand -------------------------------------------------------------

if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--list"]:
        sys.exit(pytest.main([__file__, "--collect-only", "-q"]))
    selection = ["-k", " or ".join(args)] if args else []
    sys.exit(pytest.main([__file__, "-v", *selection]))
