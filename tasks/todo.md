# Tasks: coding-agent v1

Plan and decisions: [plan.md](plan.md). Spec: [SPEC.md](../SPEC.md). Every task also meets the project Definition of Done (`.claude/references/definition-of-done.md`) and updates docs in the same commit (`CLAUDE.md` § Documentation).

Standard checks, referred to below as **all checks**:

```bash
uv run pytest && uv run pyright && uv run ruff check . && uv run ruff format --check .
```

---

## Phase 1: Prove the risky parts

## Task 1: Project scaffold and `coding-agent --version`

**Description:** Create the `uv` project with the stack from `CLAUDE.md`, a `src/coding_agent` package, a typer app exposing `--version`, pytest configured with `docker` and `live` markers excluded by default, and ruff/pyright configuration.

**Acceptance criteria:**
- [ ] `uv sync` installs from a committed `uv.lock`
- [ ] `uv run coding-agent --version` prints the package version
- [ ] `uv run pytest` runs unit tests only (`addopts` excludes `docker` and `live`)

**Verification:**
- [ ] Tests pass: `uv run pytest tests/unit/test_cli.py`
- [ ] All checks pass (pyright strict on `src/`)
- [ ] Manual check: fresh clone, `uv sync`, `uv run coding-agent --version`

**Dependencies:** None

**Files likely touched:**
- `pyproject.toml`, `uv.lock`
- `src/coding_agent/__init__.py`, `src/coding_agent/cli.py`
- `tests/unit/test_cli.py`

**Estimated scope:** Small

---

## Task 2: Agent loop against a fake client

**Description:** Implement `loop.py`: send `system`, `tools` and `messages` through an injected client's `messages.stream(...)`; append the full `response.content`; dispatch every `tool_use` to a handler; return all results in one user message; stop on a turn without tool use. Enforce the turn and token limits and stop on `max_tokens` or context-window stop reasons. Tool handlers are injected, so this task uses a fake tool. Fixtures are built from SDK `Message` types in tests until Task 6 records real ones. Check request parameters against the `claude-api` skill first.

**Acceptance criteria:**
- [ ] History is append-only and every `tool_use` gets a matching `tool_result` in a single user message; a handler error yields `is_error: true`
- [ ] `system` and `tools` are byte-identical across all requests in a run
- [ ] Turn limit, token limit and `max_tokens` each stop the loop with a distinct, named stop reason

**Verification:**
- [ ] Tests pass: `uv run pytest tests/unit/test_loop.py`
- [ ] All checks pass

**Dependencies:** Task 1

**Files likely touched:**
- `src/coding_agent/loop.py`
- `src/coding_agent/prompts.py`
- `src/coding_agent/tools/__init__.py` (`ToolResult`, handler protocol)
- `tests/fakes.py`, `tests/unit/test_loop.py`

**Estimated scope:** Medium

---

## Task 3: Sandbox container lifecycle with locked-down flags

**Description:** Implement `Sandbox` as a context manager that starts one container (`sleep infinity`) with the spec's flags (host uid:gid, `--cap-drop ALL`, `no-new-privileges`, `--network none` unless allowed, memory/CPU/pid limits, workspace mounted read-write at `/workspace` and its `.git/` read-only, no host env), runs one-off commands with a timeout, and always removes the container. Pin `python:3.12-slim` by digest. First verify the nested read-only `.git` mount on Docker Desktop. Write the decision record for using the Docker CLI.

**Acceptance criteria:**
- [ ] Network access fails by default and succeeds with networking allowed
- [ ] Writing under `/workspace/.git` fails; writing elsewhere in `/workspace` succeeds and the file is owned by the host user
- [ ] `env` in the container has no `ANTHROPIC_API_KEY`, and the container is gone after the context exits, including when the body raises

**Verification:**
- [ ] Tests pass: `uv run pytest -m docker tests/docker/test_sandbox.py`
- [ ] Unit test for the exact `docker run` argv: `uv run pytest tests/unit/test_sandbox_args.py`
- [ ] All checks pass

**Dependencies:** Task 1

**Files likely touched:**
- `src/coding_agent/sandbox.py`
- `tests/docker/test_sandbox.py`, `tests/unit/test_sandbox_args.py`
- `docs/decisions/0001-docker-cli-not-sdk.md`

**Estimated scope:** Medium

---

## Task 4: Persistent bash session and the bash tool

**Description:** Add a persistent shell to `Sandbox` (one `docker exec -i ... bash` process; marker line with exit code after each command) and the bash tool handler: run a command, combine stdout/stderr, truncate head and tail with a note of how much was cut, report the exit code, support `restart`. On timeout, kill all container processes except PID 1, restart the shell, and return an error. Write the decision record for the session design.

**Acceptance criteria:**
- [ ] `cd` and exported variables persist across calls; `restart` resets them
- [ ] `sleep infinity` returns a timeout error at the configured limit, and the next command works
- [ ] Output larger than the limit is truncated with head, tail and a count of characters cut

**Verification:**
- [ ] Tests pass: `uv run pytest -m docker tests/docker/test_bash.py` and `uv run pytest tests/unit/test_truncate.py`
- [ ] All checks pass

**Dependencies:** Task 3

**Files likely touched:**
- `src/coding_agent/sandbox.py`
- `src/coding_agent/tools/bash.py`
- `tests/docker/test_bash.py`, `tests/unit/test_truncate.py`
- `docs/decisions/0002-persistent-bash-session.md`

**Estimated scope:** Medium

---

## Checkpoint 1: Foundations
- [ ] `uv run pytest`, `uv run pytest -m docker`, pyright and ruff all pass
- [ ] Loop and sandbox each work on their own
- [ ] Review with human before wiring them together

---

## Phase 2: Walking skeleton

## Task 5: Settings, preflight checks and exit codes

**Description:** Implement `config.py` with `pydantic-settings`: defaults, then `~/.config/coding-agent/config.toml`, then `CODING_AGENT_*` env vars, then CLI flags; API key as `SecretStr`; never read config from the workspace. Add the `run` command's arguments and preflight checks (Docker reachable, key set, workspace is a git repo, warning on a dirty tree) and map outcomes to exit codes.

**Acceptance criteria:**
- [ ] Precedence flags > env > user config file > defaults is tested, and a config file inside the workspace is ignored
- [ ] Not a git repo, missing key, or Docker unavailable each exit `2` with a one-line message; a dirty tree warns and continues
- [ ] The key never appears in `repr`, logs or error output

**Verification:**
- [ ] Tests pass: `uv run pytest tests/unit/test_config.py tests/unit/test_cli.py`
- [ ] All checks pass

**Dependencies:** Task 1

**Files likely touched:**
- `src/coding_agent/config.py`
- `src/coding_agent/cli.py`
- `tests/unit/test_config.py`, `tests/unit/test_cli.py`

**Estimated scope:** Medium

---

## Task 6: `coding-agent run` end to end with the bash tool

**Description:** Wire CLI → Sandbox → loop with the real `anthropic` client and the bash tool declared. Print model text, tool calls and truncated results plainly (rich streaming comes in Task 10), then the final message, stop reason, turns/tokens used and `git diff --stat` (run on the host). Record two real API responses as fixtures (body only) and switch the loop tests to them. Write the README's install/configure/run section.

**Acceptance criteria:**
- [ ] Live: `coding-agent run "create hello.py that prints hello" --workspace <temp repo>` creates the file, exits `0`, and leaves it uncommitted (spec criterion 1)
- [ ] Hitting the turn limit exits `3` and names the limit
- [ ] `tests/fixtures/` contains recorded responses and a unit test proves none match `sk-ant-|x-api-key|authorization`

**Verification:**
- [ ] Tests pass: `uv run pytest` and `uv run pytest -m live tests/live/test_run.py`
- [ ] All checks pass
- [ ] Manual check: run a small task on a scratch repo and read the output

**Dependencies:** Tasks 2, 4, 5

**Files likely touched:**
- `src/coding_agent/cli.py`, `src/coding_agent/prompts.py`
- `tests/live/test_run.py`, `tests/fixtures/*.json`, `tests/unit/test_fixtures_clean.py`
- `README.md`

**Estimated scope:** Medium

---

## Checkpoint 2: First real run
- [ ] Spec success criterion 1 passes live
- [ ] README has install, configure and run instructions
- [ ] Review with human

---

## Phase 3: Text editor tool

## Task 7: Editor `view` and `create` with workspace path checks

**Description:** Implement path resolution inside the container (`realpath -m`, path passed as an argument) that rejects anything outside `/workspace` and anything under `/workspace/.git`, then `view` (line-numbered, directory listing, truncated for large files) and `create`. Not yet declared to the model. Write the decision record on path checks versus the host boundary.

**Acceptance criteria:**
- [ ] `..`, absolute paths outside the workspace, symlinks pointing outside, and `.git/` paths each return `is_error: true`
- [ ] `view` shows numbered lines and truncates large files; `create` writes the exact content, including content with quotes and `$`
- [ ] Nothing outside the workspace changes in any abuse test

**Verification:**
- [ ] Tests pass: `uv run pytest -m docker tests/docker/test_editor.py` and `uv run pytest tests/unit/test_editor.py`
- [ ] All checks pass

**Dependencies:** Task 6

**Files likely touched:**
- `src/coding_agent/tools/editor.py`
- `src/coding_agent/sandbox.py` (read/write helpers)
- `tests/docker/test_editor.py`, `tests/unit/test_editor.py`
- `docs/decisions/0003-path-checks-inside-container.md`

**Estimated scope:** Medium

---

## Task 8: Editor `str_replace` and `insert`; declare the editor tool

**Description:** Add `str_replace` (exactly one match required) and `insert` (after a given line), then declare `text_editor_20250728` alongside bash so the model can use it. Verify the tool version and declaration against current docs first.

**Acceptance criteria:**
- [ ] `str_replace` with zero or multiple matches returns an error naming the count and leaves the file unchanged
- [ ] `insert` at line 0, a middle line and the last line produces the expected file; an out-of-range line is an error
- [ ] Live: a task that edits an existing file completes using the editor tool

**Verification:**
- [ ] Tests pass: `uv run pytest tests/unit/test_editor.py` and `uv run pytest -m live tests/live/test_run.py`
- [ ] All checks pass

**Dependencies:** Task 7

**Files likely touched:**
- `src/coding_agent/tools/editor.py`
- `src/coding_agent/prompts.py`
- `tests/unit/test_editor.py`, `tests/live/test_run.py`

**Estimated scope:** Medium

---

## Checkpoint 3: Both tools
- [ ] Live run that edits an existing file succeeds
- [ ] Review with human

---

## Phase 4: Hardening and polish

## Task 9: Ctrl-C handling and guaranteed container cleanup

**Description:** On SIGINT, stop the loop, remove the container, print what was done so far, and exit `130`.

**Acceptance criteria:**
- [ ] Sending SIGINT to a running `coding-agent run` exits `130` (spec criterion 10)
- [ ] No container from that run remains afterward

**Verification:**
- [ ] Tests pass: `uv run pytest -m docker tests/docker/test_interrupt.py`
- [ ] All checks pass

**Dependencies:** Task 6

**Files likely touched:**
- `src/coding_agent/cli.py`
- `tests/docker/test_interrupt.py`

**Estimated scope:** Small

---

## Task 10: Streamed terminal output with rich

**Description:** Move output into `render.py`: model text appears as it streams, tool calls show the command or path, results are shown truncated, and the end-of-run summary is formatted.

**Acceptance criteria:**
- [ ] Text is rendered incrementally from stream events, not after the full message
- [ ] Rendering is tested against a captured `rich` console for a replayed run

**Verification:**
- [ ] Tests pass: `uv run pytest tests/unit/test_render.py`
- [ ] All checks pass
- [ ] Manual check: a live run reads well in the terminal

**Dependencies:** Task 6

**Files likely touched:**
- `src/coding_agent/render.py`
- `src/coding_agent/loop.py` (emit events)
- `tests/unit/test_render.py`

**Estimated scope:** Small

---

## Task 11: Success-criteria pass and v1 docs

**Description:** Map each of the spec's 12 success criteria to the test that proves it, fill any gaps, and finish the docs: README security model section, `CHANGELOG.md` entry for 0.1.0, spec status updated.

**Acceptance criteria:**
- [ ] Every spec success criterion has a named passing test (the list lives in the PR description)
- [ ] README explains in plain terms what the sandbox does and does not protect against
- [ ] `CHANGELOG.md` has a 0.1.0 entry

**Verification:**
- [ ] Tests pass: `uv run pytest`, `uv run pytest -m docker`, `uv run pytest -m live`
- [ ] All checks pass

**Dependencies:** Tasks 8, 9, 10

**Files likely touched:**
- `README.md`, `CHANGELOG.md`, `SPEC.md`
- `tests/...` (gaps only)

**Estimated scope:** Medium

---

## Checkpoint 4: v1 complete
- [ ] All 12 spec success criteria verified
- [ ] Definition of Done holds
- [ ] Ready for review
