# 0003: Split a run into Setup, a shared middle and Finish, so eval reuses the MVP's loop

Status: proposed, 2026-11-02. Builds on [0002](0002-copy-in-diff-out.md): the MVP mode keeps its copy-in, diff-out design unchanged.

## Context

Phase 1 (`SPEC-Phase1.md`) runs the agent on SWE-bench Verified Mini. Most of the MVP carries over as it is: the loop, `run_command`, the output cap, kill-on-stop and the lockdown flags. What changes is at the two ends of a task:

- **Getting code in.** The repo is already in the task's image at `/testbed`, with a conda environment and its own git history. There is no host repo to copy in, but later commits have to be removed and the baseline must leave out ignored build files.
- **Getting results out.** Nobody is there to review 50 diffs. Each diff becomes a line in a predictions file, and the trajectory goes to a run folder, not the target repo.

`/workspace`, the image, the limits, the prompts and the review step are all fixed in the MVP code, and `__main__.main()` runs copy-in, baseline, loop and review in one sequence.

## Options

1. **Flags in the existing code.** Add `if eval_mode:` wherever `/workspace`, copy-in, the baseline, the prompts or review appear. Little code to start with, but the checks spread through `sandbox.py`, `agent.py` and `__main__.py`, and every MVP safety rule then has an eval branch next to it.
2. **Setup and Finish around a shared middle.** A Setup supplies the image, working folder, command prefix, limits, system prompt and tool description, gets the code in and takes the baseline. A Finish takes the diff and the stop reason. The MVP is CopyInSetup + ReviewAndApply, eval is ImageSetup + RecordPrediction. The loop and sandbox take their settings from the Setup and know nothing about modes.
3. **A separate eval agent.** Write a second loop for eval, or run mini-swe-agent itself. Then the eval no longer measures this agent, and the two loops drift apart.

## Decision

Option 2, as specified in `SPEC-Phase1.md` § Group 1 · The core (M1–M7, F1–F2; M3, M5 and M7 are listed under Group 2). Batch concerns (per-run labels, per-model context limits, cost, resume, Ctrl+C between tasks) live in `eval/runner.py`, not in a mode (R1–R5).

## Consequences

- The MVP's code is moved behind the two seams with no change in behaviour, and its tests must pass unchanged. That is the first step of Phase 1, before any eval code.
- The loop and sandbox lose their hard-coded `/workspace`, image and limits. Each mode's values are fixed for a whole run, so `system` and `tools` stay byte-identical across turns and prompt caching still hits.
- Each mode has its own tool description, so the schema-matches-`RunCommandInput` test runs for each one.
- ImageSetup skips copy-in and the deny list (S4, S9). That is safe because no host files enter the container: the only host data is the issue text from the dataset. The lockdown flags still apply to the third-party task images.
- A later mode slots in the same way, e.g. a Phase 3 Finish that commits to a branch instead of asking apply/discard.
- One more layer to read: a reader following a run goes through `modes.py` instead of one straight sequence in `__main__.py`.
