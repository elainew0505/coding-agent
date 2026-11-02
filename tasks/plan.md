# Implementation Plan: coding-agent MVP (Phase 0)

Implements [SPEC.md](../SPEC.md). Tasks, acceptance criteria and verification are in [todo.md](todo.md). One commit per task on `feature/mvp`; the user approves each before the next starts.

## Overview

`coding-agent "<task>"` copies your repo into a locked-down container, lets Claude work through one `run_command` tool until it's done or hits a limit, then shows the diff and applies it to your repo only if you say so. Modelled on mini-swe-agent: one task per run, bash only, a fresh shell per command, a linear history saved as a trajectory.

## Architecture decisions

- Copy in, diff out, no mounts: [0002](../docs/decisions/0002-copy-in-diff-out.md)
- The sandbox, not per-command approval, is the safety boundary: [0001](../docs/decisions/0001-sandbox-instead-of-approvals.md)
- Native tool calling with a custom stateless `run_command`, not the Anthropic-defined `bash` tool (`SPEC.md` intro)
- Minimal stack: `anthropic` and `pydantic` only; config is constants (`CLAUDE.md` § Stack)
- Code style follows `src/sandbox.py`: plain functions, small dataclasses, constants at the top, short comments naming the spec rule

## Dependency graph

```
sandbox.py (S, D helpers)       ← exists; tested first
    │
    ├── tools.py + config.py    (run_command: validation, result text)
    │       │
    │       └── agent.py loop   (L rules, fake client)
    │               │
    │               └── review, apply, trajectory, __main__.py (D rules, CLI)
    │                       │
    │                       └── live tests on ../toy-repo, README
```

## Task list

### Phase A: Foundation
- [x] Task 1: uv project and the sandbox module
- [x] Task 2: Docker safety tests for the sandbox

### Checkpoint A
- [x] `uv run pytest` passes with Docker running; every S-rule safety test is green
- [ ] Review with the user

### Phase B: The loop
- [x] Task 3: `run_command` tool and config
- [x] Task 4: Agent loop and model wrapper

### Checkpoint B
- [x] Unit tests cover L1–L9 with the fake model and fake client
- [ ] Review with the user

### Phase C: End to end
- [x] Task 5: Review, apply, trajectory and the `coding-agent` command
- [ ] Task 6: Live task tests (against `../toy-repo`) and README (code done; waiting on the user's first `--live` run)

### Checkpoint C
- [ ] Every acceptance test in `SPEC.md` is checked off (live ones run by the user)
- [ ] Review with the user, then open the PR

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Docker behaviour differs between Docker Desktop on macOS and Linux (tar flavour, process handling) | High | Task 2 runs every sandbox rule against a real container before anything builds on it |
| `docker exec` doesn't return while a background process holds its output open | Med | A Docker test with `sleep 1000 &`; if it hangs, redirect or kill in `run` |
| Claude edits files poorly through bash alone | Med | Live task tests in Task 6; a structured `edit_file` tool is Phase 1 if needed |
| API details (effort, caching, streaming) differ from what the spec assumes | Med | Checked against the current docs in Task 4; the first live run (Task 6) confirms them for real |

## Open questions

- The three safety additions (flag deletions and auto-run files, strip control characters, cap the diff) are not yet in the spec
