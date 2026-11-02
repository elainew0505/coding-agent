"""
sandbox.py - the complete MVP sandbox: a locked-down container holding a safe
copy of your repo, where commands run, and from which changes leave only as a
diff.

Spec rules covered (see SPEC.md):
  Step 1  start / run / remove                          S2 (name, label), S6, S8
          commands as an argument list, never a shell    run_command "Runs as"
          in-container timeout + host backstop          run_command "On timeout"
  Step 2  find the repo's top folder; refuse non-repos   How a run works 1, S8
          copy git's file list minus deleted + secrets   S4, S9
  Step 3  baseline snapshot in /harness.git              S5
          diff out (+ summary)                           D1, D2, D3
          apply on the host with a check first           D5 (saving the patch on
                                                         failure is agent.py's job)
  Step 4  output cap while reading (head + tail)         run_command "Output cap"
          kill every process on timeout or Ctrl+C        kill -KILL -1 paragraph
  Step 5  lockdown flags                                 S2
          remove leftover labelled containers            S7

Try it from inside a git repo (e.g. the toy repo):
  uv run --project ~/code/coding-agent python -m coding_agent.sandbox [image]
(the image defaults to config.IMAGE, coding-agent:toy)
"""

import atexit
import codecs
import fnmatch
import os
import subprocess
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from coding_agent.config import (  # timeouts, output cap and deny list (L9, S9)
    BACKSTOP_EXTRA,
    DEFAULT_TIMEOUT,
    DENY_DIRS,
    DENY_NAMES,
    HEAD_CHARS,
    IMAGE,
    MAX_TIMEOUT,
    TAIL_CHARS,
)

LABEL = "coding-agent=1"
WORKDIR = "/workspace"
HARNESS_GIT = "/harness.git"  # outside /workspace, so the model never sees it

# S2: the lockdown flags.
LOCKDOWN_FLAGS = [
    "--network",
    "none",
    "--memory",
    "2g",
    "--cpus",
    "2",
    "--pids-limit",
    "256",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges",
]


class SandboxError(Exception):
    """Something went wrong with Docker or the repo (not with a command you ran)."""


@dataclass
class CommandResult:
    exit_code: int
    output: str  # stdout and stderr combined, capped
    timed_out: bool = False  # stopped by the timeout (the tool marks this is_error)

    def __str__(self):
        return f"exit_code: {self.exit_code}\n{self.output}"


@dataclass
class CopyReport:
    copied: list = field(default_factory=list)
    skipped_secret: list = field(default_factory=list)
    skipped_missing: list = field(default_factory=list)


@dataclass
class Diff:
    patch: bytes  # bytes, not text: a patch can contain any file content
    stat: str  # the --stat summary for people to read

    @property
    def empty(self):
        return not self.patch.strip()


def _docker(*args, timeout=60, input=None, text=True):
    """Run a docker CLI command as an argument list (never through a shell)."""
    return subprocess.run(
        ["docker", *args],
        input=input,
        capture_output=True,
        check=False,
        text=text,
        errors="replace" if text else None,
        timeout=timeout,
    )


# --- step 2 helpers: which files go in -------------------------------------


def find_repo_root(start="."):
    """Top folder of the git repo containing `start`, or a clear error."""
    r = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        capture_output=True,
        check=False,
        text=True,
    )
    if r.returncode != 0:
        raise SandboxError(f"'{Path(start).resolve()}' is not inside a git repo.")
    return Path(r.stdout.strip())


def is_denied(path):
    """True for anything under .agent/ or a file whose name looks like a secret."""
    parts = Path(path).parts
    if parts and parts[0] in DENY_DIRS:
        return True
    # Case-insensitive: on macOS `Server.PEM` and `server.pem` are the same file.
    name = (parts[-1] if parts else "").lower()
    return any(fnmatch.fnmatchcase(name, pattern.lower()) for pattern in DENY_NAMES)


def list_repo_files(root):
    """Files git would track: tracked + untracked-but-not-ignored, filtered."""
    r = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        check=False,
    )
    if r.returncode != 0:
        raise SandboxError(f"git ls-files failed:\n{r.stderr.decode(errors='replace')}")
    report = CopyReport()
    seen = set()
    for raw in r.stdout.split(b"\0"):
        if not raw:
            continue
        path = os.fsdecode(raw)
        if path in seen:
            continue
        seen.add(path)
        if not os.path.lexists(root / path):  # deleted from disk, still in git's index
            report.skipped_missing.append(path)
        elif is_denied(path):
            report.skipped_secret.append(path)
        else:
            report.copied.append(path)
    return report


# --- step 3 helper: apply on the host ---------------------------------------


def apply_patch(root, patch):
    """Check, then apply a patch to the real repo. Returns (ok, message).

    Nothing is written unless the check passes. Saving the patch when it fails
    (D5) is left to the caller.
    """
    check = subprocess.run(
        ["git", "-C", str(root), "apply", "--check", "-"],
        input=patch,
        capture_output=True,
        check=False,
    )
    if check.returncode != 0:
        return False, check.stderr.decode(errors="replace").strip()
    done = subprocess.run(
        ["git", "-C", str(root), "apply", "-"], input=patch, capture_output=True, check=False
    )
    if done.returncode != 0:
        return False, done.stderr.decode(errors="replace").strip()
    return True, "applied"


# --- step 4 helper: capped output -------------------------------------------


class _CappedOutput:
    """Keeps the first HEAD_CHARS and a rolling last TAIL_CHARS of a stream.

    Memory stays flat however much a command prints (e.g. `yes`).
    """

    def __init__(self):
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.head = ""
        self.tail = ""
        self.total = 0

    def _add(self, text):
        self.total += len(text)
        room = HEAD_CHARS - len(self.head)
        if room > 0:
            self.head += text[:room]
            text = text[room:]
        if text:
            self.tail = (self.tail + text)[-TAIL_CHARS:]

    def read_from(self, stream):
        while True:
            chunk = stream.read1(65536)
            if not chunk:
                break
            self._add(self._decoder.decode(chunk))
        self._add(self._decoder.decode(b"", final=True))

    def text(self):
        dropped = self.total - len(self.head) - len(self.tail)
        if dropped <= 0:
            return self.head + self.tail
        return f"{self.head}\n[truncated {dropped} chars]\n{self.tail}"


class Sandbox:
    def __init__(self, image):
        self.image = image
        self.name = f"agent-{uuid.uuid4().hex[:8]}"
        self.started = False
        self.baseline_sha = None

    # --- lifecycle ---------------------------------------------------------

    def start(self):
        self._check_docker()
        self._remove_leftovers()  # S7
        r = _docker(
            "run",
            "-d",
            "--name",
            self.name,
            "--label",
            LABEL,
            *LOCKDOWN_FLAGS,  # S2
            "-w",
            WORKDIR,
            self.image,
            "sleep",
            "infinity",
        )
        if r.returncode != 0:
            raise SandboxError(f"Could not start the container:\n{r.stderr.strip()}")
        self.started = True
        atexit.register(self.close)  # backstop if the program exits without close()
        self._exec("mkdir", "-p", WORKDIR)
        return self

    def close(self):
        """Remove the container. Safe to call more than once."""
        if self.started:
            _docker("rm", "-f", self.name, timeout=30)
            self.started = False

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()  # runs on normal exit, errors and Ctrl+C

    # --- step 2: copy the repo in -----------------------------------------

    def copy_in(self, root):
        """Stream the repo's files into /workspace. Returns what was copied/skipped."""
        root = Path(root)
        report = list_repo_files(root)
        if not report.copied:
            return report

        # The file list goes to tar through a temp file (NUL-separated), so odd
        # names (spaces, newlines) are safe. tar's warnings go to a temp file
        # too: a pipe nobody reads until the end fills up and deadlocks.
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"\0".join(os.fsencode(p) for p in report.copied) + b"\0")
            list_file = f.name
        try:
            env = {**os.environ, "COPYFILE_DISABLE": "1"}  # macOS: no ._* files
            with tempfile.TemporaryFile() as errors:
                pack = subprocess.Popen(
                    ["tar", "-c", "--no-xattrs", "--null", "--no-recursion", "-T", list_file],
                    cwd=root,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=errors,
                )
                assert pack.stdout  # it's a PIPE, so never None
                unpack = subprocess.run(
                    [
                        "docker",
                        "exec",
                        "-i",
                        self.name,
                        "tar",
                        "-x",
                        "--no-same-owner",
                        "-C",
                        WORKDIR,
                    ],
                    stdin=pack.stdout,
                    capture_output=True,
                    check=False,
                )
                pack.stdout.close()
                pack.wait()
                errors.seek(0)
                pack_err = errors.read()
        finally:
            os.unlink(list_file)

        if pack.returncode != 0:
            raise SandboxError(f"Packing files failed:\n{pack_err.decode(errors='replace')}")
        if unpack.returncode != 0:
            raise SandboxError(
                f"Unpacking in the container failed:\n{unpack.stderr.decode(errors='replace')}"
            )
        return report

    # --- step 3: baseline and diff ----------------------------------------

    def baseline(self):
        """Snapshot /workspace in /harness.git. Call once, right after copy_in."""
        self._git("init", "-q")
        # --force: everything here was chosen by copy_in, including files the
        # repo tracks despite .gitignore. Once in the baseline git diffs them
        # like any tracked file; new ignored files still stay out of the diff.
        self._git("add", "-A", "--force")
        self._git("commit", "-q", "--allow-empty", "-m", "baseline")
        self.baseline_sha = self._git("rev-parse", "HEAD").stdout.decode().strip()
        return self.baseline_sha

    def diff(self):
        """Everything that changed in /workspace since the baseline."""
        if not self.baseline_sha:
            raise SandboxError("No baseline yet; call baseline() after copy_in().")
        # If the model deleted /workspace itself, recreate the empty folder so
        # git can still compare: the diff then shows every file deleted.
        self._exec("mkdir", "-p", WORKDIR)
        self._git("add", "-A")
        patch = self._git("diff", "--cached", "--binary", self.baseline_sha).stdout
        stat = self._git("diff", "--cached", "--stat", self.baseline_sha).stdout
        return Diff(patch=patch, stat=stat.decode(errors="replace"))

    # --- running commands (step 1 + step 4) -------------------------------

    def run(self, command, timeout=DEFAULT_TIMEOUT):
        """Run one command in a fresh bash shell inside the container.

        Output is capped while it is read. On timeout or Ctrl+C every process
        in the container (except PID 1) is killed. Ctrl+C is re-raised so the
        caller can go to review.
        """
        if not self.started:
            raise SandboxError("Sandbox is not running; call start() first.")
        timeout = max(1, min(int(timeout), MAX_TIMEOUT))
        args = [
            "docker",
            "exec",
            "-w",
            WORKDIR,
            self.name,
            "timeout",
            str(timeout),  # in-container timeout: really stops the command
            "bash",
            "-lc",
            command,  # command is ONE list item: no host shell involved
        ]
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out = _CappedOutput()
        reader = threading.Thread(target=out.read_from, args=(proc.stdout,), daemon=True)
        reader.start()
        note = f"\n[command timed out after {timeout}s and was stopped]"
        try:
            proc.wait(timeout=timeout + BACKSTOP_EXTRA)  # host-side backstop
        except subprocess.TimeoutExpired:
            self.kill_all()
            proc.kill()
            proc.wait()
            reader.join(5)
            return CommandResult(124, out.text() + note, timed_out=True)
        except KeyboardInterrupt:
            self.kill_all()
            proc.kill()
            proc.wait()
            raise
        reader.join(5)
        output = out.text()
        timed_out = proc.returncode == 124  # what `timeout` exits with when it stops a command
        if timed_out:
            output += note
        return CommandResult(proc.returncode, output, timed_out=timed_out)

    def kill_all(self):
        """Kill every process in the container except PID 1 (`sleep infinity`).

        Uses bash's built-in kill (the slim image has no kill program).
        """
        if self.started:
            try:
                subprocess.run(
                    ["docker", "exec", "-w", "/", self.name, "bash", "-c", "kill -KILL -1"],
                    capture_output=True,
                    check=False,
                    timeout=15,
                )
            except subprocess.TimeoutExpired:
                pass  # Docker is hung; removing the container at exit still stops everything

    # --- internals ---------------------------------------------------------

    def _exec(self, *argv):
        """Run a harness command (argument list, no shell) in the container."""
        # -w /: the container's default folder is /workspace, which the model
        # may have deleted; docker exec would then fail before running anything.
        r = subprocess.run(
            ["docker", "exec", "-w", "/", self.name, *argv], capture_output=True, check=False
        )
        if r.returncode != 0:
            raise SandboxError(f"{' '.join(argv)} failed:\n{r.stderr.decode(errors='replace')}")
        return r

    def _git(self, *args):
        """Harness git: separate git dir, so /workspace has no .git (S5)."""
        return self._exec(
            "git",
            f"--git-dir={HARNESS_GIT}",
            f"--work-tree={WORKDIR}",
            "-c",
            "user.name=agent",
            "-c",
            "user.email=agent@local",
            *args,
        )

    def _check_docker(self):
        try:
            r = _docker("info", "--format", "{{.ServerVersion}}", timeout=15)
        except FileNotFoundError:
            raise SandboxError("The docker command isn't installed.")
        if r.returncode != 0:
            raise SandboxError("Docker isn't running. Start Docker Desktop and try again.")
        r = _docker("image", "inspect", self.image, timeout=15)
        if r.returncode != 0:
            raise SandboxError(f"Image '{self.image}' not found. Build or pull it first.")

    def _remove_leftovers(self):
        """S7: remove containers from earlier runs - only ones carrying our label."""
        r = _docker("ps", "-aq", "--filter", f"label={LABEL}", timeout=30)
        ids = r.stdout.split()
        if ids:
            _docker("rm", "-f", *ids, timeout=60)


# --- demo: the whole sandbox flow, with you playing the agent --------------


def demo(image):
    root = find_repo_root()  # fails before any container starts if not in a repo
    print(f"Repo: {root}")
    with Sandbox(image) as sb:
        report = sb.copy_in(root)
        sha = sb.baseline()
        print(f"Copied {len(report.copied)} files into {sb.name}; baseline {sha[:8]}")
        if report.skipped_secret:
            print(f"Skipped (secrets / .agent): {', '.join(report.skipped_secret)}")

        def show(cmd, timeout=DEFAULT_TIMEOUT):
            print(f"\n$ {cmd}")
            print(sb.run(cmd, timeout))

        show(
            "command -v python >/dev/null && python -m pytest -q || echo '(no python in this image)'"
        )
        if (root / "calc" / "ranges.py").exists():
            show("sed -i 's/range(start, end))/range(start, end + 1))/' calc/ranges.py")
        else:
            show("echo 'demo change' > SANDBOX_DEMO.txt")
        show(
            "command -v python >/dev/null && python -m pytest -q || echo '(no python in this image)'"
        )

        d = sb.diff()
        if d.empty:
            print("\nNo file changes.")
            return
        print("\n=== Changes ===")
        print(d.stat)
        print(d.patch.decode(errors="replace"))
        try:
            answer = input("[a]pply / [d]iscard? ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            answer = "d"  # D4: Ctrl+C / Ctrl+D = discard
        if answer == "a":
            ok, msg = apply_patch(root, d.patch)
            print("Applied to your repo." if ok else f"Could not apply: {msg}")
        else:
            print("Discarded. Your repo is unchanged.")
    print(f"Removed {sb.name}")


if __name__ == "__main__":
    try:
        demo(sys.argv[1] if len(sys.argv) > 1 else IMAGE)
    except SandboxError as e:
        sys.exit(f"Error: {e}")
