# Implementation Plan: Phase 1, evaluation

Implements [SPEC-Phase1.md](../SPEC-Phase1.md). Tasks, acceptance criteria and verification are in [todo.md](todo.md). Phase 0's plan is in [archive/](archive/). One commit per task; the user approves each before the next starts.

## Overview

`coding-agent eval` runs the agent on the 50 tasks of SWE-bench Verified Mini, each in its own SWE-bench image, records one patch per task, grades the patches with the official harness and writes a report of resolved count, cost and calls. The agent is the MVP's, behind two seams: ImageSetup gets the task in, RecordPrediction gets the patch out ([0003](../docs/decisions/0003-setup-and-finish-modes.md)).

The spec tags every rule **First** or **Later**. Phases A–C build everything First and end with the first trustworthy numbers; Phase D is the Later work, done after the first real run.

## Architecture decisions

- Setup → shared loop and sandbox → Finish: [0003](../docs/decisions/0003-setup-and-finish-modes.md). The MVP is moved behind the seams first, with no change in behaviour
- Grading only by the official SWE-bench harness, never our own grader (`SPEC-Phase1.md` § Grading)
- `swebench` and `datasets` in an optional `eval` extra; the `coding-agent` command alone keeps `anthropic` and `pydantic` only
- Copy-in, diff-out stays the MVP's design ([0002](../docs/decisions/0002-copy-in-diff-out.md)); ImageSetup only skips copy-in because no host files enter the container

## Dependency graph

```
eval extra + dataset loader
    │
    └── gold check (harness on gold patches)        ← fail fast: images, grading, exclusions
            │
MVP behind Setup/Finish seams (no behaviour change)
    │
    ├── ImageSetup: image, /testbed, conda, limits, clean history, baseline
    │       │
    │       ├── eval prompt + tool description
    │       │
    │       └── RecordPrediction ── fake model writes the gold fix → harness resolves it
    │                   │
    │                   └── runner: run folder, resume, Ctrl+C, `coding-agent eval`
    │                           │
    │                           ├── cost: price table, per-model context limit, --max-cost
    │                           └── grade + report
    │                                   │
    │                                   └── live runs (user) ── Phase D (Later)
```

## Task list

### Before Phase A: File tools (`SPEC.md` § File tools, [0004](../docs/decisions/0004-file-tools.md))

`read_file`, `write_file` and `edit_file` next to `run_command`, on by default, `--bash-only` to turn them off. They come first so the eval measures the agent with them, and `eval-compare` against `--bash-only` shows whether they help. Each slice is approved by the user before it starts (CLAUDE.md § Working style).

- [ ] Task F1: Sandbox reads and writes file bytes
- [ ] Task F2: `read_file` and `write_file`
- [ ] Task F3: `edit_file`
- [ ] Task F4: Tool sets and `--bash-only`

### Checkpoint F
- [ ] The File tools acceptance tests in `SPEC.md` are checked off
- [ ] Review with the user, then open the PR

### Phase A: Foundation (fail fast)
- [ ] Task 1: The `eval` extra and the dataset loader
- [ ] Task 2: The gold check
- [ ] Task 3: Sandbox and model take their settings from the caller
- [ ] Task 4: `modes.py`: the MVP as CopyInSetup and ReviewAndApply

### Checkpoint A
- [ ] The gold check has run, and `evals/exclusions.txt` lists every task that failed, with reasons
- [ ] Every MVP test passes unchanged
- [ ] Review with the user

### Phase B: One task end to end
- [ ] Task 5: ImageSetup on one real task image
- [ ] Task 6: Eval prompt and tool description
- [ ] Task 7: RecordPrediction, and the gold fix through `run_command`

### Checkpoint B
- [ ] On one task, a fake model writes the gold fix through `run_command`, and the official harness resolves the recorded patch
- [ ] Review with the user

### Phase C: A full run (completes First)
- [ ] Task 8: The eval runner and `coding-agent eval`
- [ ] Task 9: Cost: price table, per-model context limit, `--max-cost`
- [ ] Task 10: Grading and the report
- [ ] Task 11: Fake-model runs over every task
- [ ] Task 12: Live runs and the README (run by the user)

### Checkpoint C
- [ ] Every **First** acceptance test in `SPEC-Phase1.md` is checked off (live ones run by the user)
- [ ] Review with the user, then open the PR

### Phase D: Later (after the first real run)
- [ ] Task 13: Pre-send context estimate (A2)
- [ ] Task 14: Per-run labels (R1)
- [ ] Task 15: Trials (A6)
- [ ] Task 16: `eval-compare`
- [ ] Task 17: mini-swe-agent baseline (run by the user)
- [ ] Task 18: A full run on AWS Spot (run by the user)

### Checkpoint D
- [ ] Every acceptance test in `SPEC-Phase1.md` is checked off
- [ ] Review with the user

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| SWE-bench images don't build or run well on Apple Silicon | High | Task 2 runs the gold check before any agent code; failures go to the exclusion list (E4); full runs move to x86 if too many fail |
| The official fixes don't resolve inside our lockdown flags (`--network none`, memory, pids) | High | Task 7 runs the gold fix through our own sandbox on one task; Task 11 on all of them; limits are Setup values (M4) |
| Moving the MVP behind the seams breaks it quietly | High | Tasks 3–4 change no behaviour, and every MVP test must pass unchanged |
| The conda environment isn't active in `docker exec … bash -lc` | Med | Task 5 checks how the images activate it, with a Docker test that imports the repo's package |
| `swebench` or `datasets` brings in a risky dependency tree | Med | Supply-chain check in Task 1 before they're added; both stay in the optional extra |
| Haiku 5.5 prices or its 100,000-token price step differ from the spec | Med | Task 9 takes prices from the current docs, not memory |
| A model-chosen path or file content reaches a shell (file tools) | High | Paths are separate arguments after `--` or `"$1"`, content goes on stdin; Docker tests in F1 try `"; touch /tmp/pwned"`, `$(…)` and backticks |
| F4 and Task 3 both change what `Model` takes | Low | F4 adds the tool set; Task 3 builds on it rather than redoing it |
| A full run costs more than `--max-cost` expects | Med | Task 12 runs 3 tasks first; the guard stops new tasks at the limit and the run resumes |

## Open questions

- Which small, fast instance is the Docker tests' task image (Task 5)?
- How do SWE-bench images activate the `testbed` environment: `~/.bashrc`, `/etc/profile.d`, or something else (Task 5)?
- Which dataset revision and harness version to pin (Task 1)?
