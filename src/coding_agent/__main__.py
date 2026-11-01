"""
__main__.py - the coding-agent command (SPEC.md § How a run works).

  coding-agent "<task>"        (or: python -m coding_agent "<task>")

Run it anywhere inside a git repo. Steps: find the repo's top folder, start
the sandbox, copy the repo in, take a baseline, run the loop, review the
diff, save the trajectory, remove the container.
"""

import sys

from coding_agent.agent import review, run_task, write_trajectory
from coding_agent.config import IMAGE
from coding_agent.model import Model
from coding_agent.sandbox import Sandbox, SandboxError, find_repo_root

USAGE = 'usage: coding-agent "<task>"   (run inside a git repo)'


def main(argv=None, model=None, image=IMAGE):
    """Returns the exit code: 0 after a run, 1 on a Docker/repo error, 2 on bad usage.

    `model` and `image` are only swapped out by tests.
    """
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not args[0].strip():
        print(USAGE, file=sys.stderr)
        return 2
    task = args[0]

    try:
        root = find_repo_root()  # How a run works 1: always work from the top folder
    except SandboxError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    run, outcome, code = None, "not reviewed", 0
    try:
        with Sandbox(image) as sandbox:  # removes the container on exit, error or Ctrl+C (S6)
            report = sandbox.copy_in(root)
            sandbox.baseline()
            print(f"Copied {len(report.copied)} files into {sandbox.name}.")
            if report.skipped_secret:
                print(f"Not copied (secrets or .agent/): {', '.join(report.skipped_secret)}")

            run = run_task(task, model or Model(), sandbox)
            if run.detail:
                print(run.detail)
            outcome = review(sandbox.diff(), root)  # D6: every run ends in review
    except SandboxError as e:
        print(f"Error: {e}", file=sys.stderr)
        code = 1
    except KeyboardInterrupt:  # outside the loop (copy-in, diff): nothing to review
        print("\nStopped.")
    finally:
        path = write_trajectory(root, task, run, outcome)
        if run:
            print(
                f"Stop: {run.stop_reason} · {run.calls} model calls · "
                f"{run.usage['input_tokens']:,} input / {run.usage['output_tokens']:,} output tokens"
            )
        print(f"Trajectory: {path}")
    return code


if __name__ == "__main__":
    sys.exit(main())
