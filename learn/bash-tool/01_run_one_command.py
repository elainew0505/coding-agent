"""Step 1: run one bash command from Python and collect what it printed.

Runs on the host with hard-coded, harmless commands only.
Run: python3 learn/bash-tool/01_run_one_command.py
"""

import subprocess


def run(command: str, timeout_seconds: float = 5) -> tuple[str, int]:
    """Run `command` in a fresh bash process. Return (output, exit_code)."""
    try:
        completed = subprocess.run(
            ["bash", "-c", command],  # start a new bash process that runs one command, then exits
            stdout=subprocess.PIPE,  # capture normal output instead of printing it to our terminal
            stderr=subprocess.STDOUT,  # send error output into the same stream, so order is kept
            text=True,  # give us str instead of raw bytes
            timeout=timeout_seconds,  # kill the process if it runs longer than this
        )
    except subprocess.TimeoutExpired:
        # subprocess.run has already killed the process at this point.
        return f"Command timed out after {timeout_seconds}s", -1
    return completed.stdout, completed.returncode


examples = [
    "echo hello",  # succeeds: exit code 0
    "ls /does-not-exist",  # fails: error text on stderr, non-zero exit code
    "cd /tmp && pwd",  # works, but the cd is forgotten when this bash process exits...
    "pwd",  # ...so this new process starts back in the original directory
    "sleep 10",  # never finishes in time: killed by the timeout
]

for command in examples:
    output, exit_code = run(command, timeout_seconds=2)
    print(f"$ {command}")
    print(f"  exit code: {exit_code}")
    print(f"  output:    {output.strip()!r}")
