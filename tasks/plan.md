# Implementation Plan: coding-agent v1

Status: **approved 2026-10-29**. Implements [SPEC.md](../SPEC.md). Task details, acceptance criteria and verification steps are in [todo.md](todo.md).

## Overview

Build the one-shot CLI from the spec in thin slices. The two riskiest pieces, the Docker sandbox with a persistent bash session and the agent loop, are proven separately first, then joined into a walking skeleton: `coding-agent run` doing a real task end to end with only the bash tool. The text editor tool, interrupt handling, streaming output and the final success-criteria pass follow.

## Architecture Decisions

Each of these gets a short record in `docs/decisions/` in the task that implements it.

- **Docker through the `docker` CLI (argv lists via `subprocess`), not the Docker SDK for Python.** One fewer dependency, and the commands are easy to read and to copy into a terminal when debugging. (Task 3)
- **One long-lived container per run** started with `sleep infinity` as PID 1; tools run through `docker exec`. (Task 3)
- **Persistent bash session**: one `docker exec -i ... bash` process per run. Each command is followed by a unique marker line that carries the exit code, so we know where output ends. On timeout we kill every process in the container except PID 1 (`kill -9 -1`) and start a fresh shell; the tool result says the session was reset. (Task 4)
- **Editor operations run in the container using only `realpath`, `cat` and `sh`**, with paths passed as arguments, never interpolated into a command string. This keeps the "any image with bash" promise without needing Python in the image. Text manipulation (`str_replace`, `insert`, line numbering) happens in our Python code on the host. (Tasks 7 and 8)
- **Path checks are for correctness, not the host boundary.** Since file operations run inside the container, a symlink can only reach container files, which bash can already modify. The host boundary is the container itself: only the workspace is mounted, with `.git/` read-only. The workspace path check keeps edits where they belong and gives the model a clear error. (Task 7)
- **The model client is injected** (`messages.stream(...)` interface), so unit tests replay fixtures through a fake client. (Task 2)
- **Tools are declared only once implemented**: bash from Task 6, the editor from Task 8. `system` and `tools` stay constant within a run.

## Task List

### Phase 1: Prove the risky parts

- [x] Task 1: Project scaffold and `coding-agent --version` (S)
- [x] Task 2: Agent loop against a fake client (M)
- [x] Task 3: Sandbox container lifecycle with locked-down flags (M)
- [ ] Task 4: Persistent bash session and the bash tool (M)

### Checkpoint 1: Foundations

- [ ] `uv run pytest`, `uv run pytest -m docker`, `uv run pyright`, `uv run ruff check .` all pass
- [ ] Loop and sandbox each work on their own
- [ ] Review with human before wiring them together

### Phase 2: Walking skeleton

- [ ] Task 5: Settings, preflight checks and exit codes (M)
- [ ] Task 6: `coding-agent run` end to end with the bash tool (M)

### Checkpoint 2: First real run

- [ ] A live run of "create hello.py that prints hello" on a temp git repo succeeds (spec success criterion 1)
- [ ] README has install, configure and run instructions
- [ ] Review with human

### Phase 3: Text editor tool

- [ ] Task 7: Editor `view` and `create` with workspace path checks (M)
- [ ] Task 8: Editor `str_replace` and `insert`; declare the editor tool (M)

### Checkpoint 3: Both tools

- [ ] A live run that edits an existing file uses the editor tool and succeeds
- [ ] Review with human

### Phase 4: Hardening and polish

- [ ] Task 9: Ctrl-C handling and guaranteed container cleanup (S)
- [ ] Task 10: Streamed terminal output with rich (S)
- [ ] Task 11: Success-criteria pass and v1 docs (M)

### Checkpoint 4: v1 complete

- [ ] All 12 spec success criteria verified, each by a named test
- [ ] Definition of Done (`.claude/references/definition-of-done.md`) holds
- [ ] Ready for review

## Dependency Graph

```
1 scaffold
├── 2 loop ──────────────┐
├── 3 sandbox ── 4 bash ─┤
└── 5 settings/preflight ┴── 6 run end to end
                                 ├── 7 editor view/create ── 8 editor edit
                                 ├── 9 Ctrl-C
                                 └── 10 output
                                          all ── 11 success criteria
```

Tasks 2, 3 and 5 depend only on Task 1 and could run in parallel. Tasks 7, 9 and 10 are independent of each other once Task 6 is done.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Persistent bash session: marker parsing, partial output, a command that reads stdin or never ends | High | Done early (Task 4), with Docker tests for each of those cases |
| Read-only `.git/` mount nested inside the read-write workspace mount behaves differently on Docker Desktop for Mac | Medium | Verified first thing in Task 3. If it fails, fall back to not exposing `.git/` in the sandbox at all (the agent doesn't need git; `git diff --stat` runs on the host), and record that in the spec |
| Files the agent creates end up owned by the wrong user on the host | Medium | Run as host uid:gid; a Docker test checks ownership of a created file |
| Claude API parameters differ from what CLAUDE.md assumes (effort, tool versions, streaming helpers) | Medium | Check with the `claude-api` skill and current docs at the start of Tasks 2 and 6 |
| Live tests cost money and are slow | Low | Few of them, opt-in with `-m live`; unit tests replay recorded fixtures |
| A recorded fixture leaks an API key or header | Low | Save only `response.model_dump_json()`; a unit test greps `tests/fixtures/` for key patterns |

## Open Questions

None blocking. The sandbox image digest is pinned during Task 3.
