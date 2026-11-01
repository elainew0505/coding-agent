# Tasks: coding-agent MVP

Plan: [plan.md](plan.md). Spec: [SPEC.md](../SPEC.md). Every task also meets the Definition of Done (`.claude/references/definition-of-done.md`) and updates docs in the same commit.

**All checks:**

```bash
uv run pytest && uv run pyright && uv run ruff check . && uv run ruff format --check .
```

---

## Task 1: uv project and the sandbox module

**Description:** Create the `uv` project and move `src/sandbox.py` into the package layout from `SPEC.md` § Project layout. Pin the Dockerfile's base image by digest (S1). Remove leftover `__pycache__` folders from the old implementation.

**Acceptance criteria:**
- [x] `uv sync` installs from a committed `uv.lock`; runtime deps are `anthropic` and `pydantic` only
- [x] `src/coding_agent/sandbox.py` exists; `python -m coding_agent.sandbox` still runs the demo
- [x] `Dockerfile` starts `FROM python:3.12-slim@sha256:…`

**Verification:**
- [x] All checks pass (no tests yet beyond collection)
- [x] Manual check: `docker build -t coding-agent:toy .` succeeds

**Dependencies:** None

**Files likely touched:** `pyproject.toml`, `uv.lock`, `src/coding_agent/__init__.py`, `src/coding_agent/sandbox.py`, `Dockerfile`

**Estimated scope:** Small

---

## Task 2: Docker safety tests for the sandbox

**Description:** Add `tests/conftest.py` (skip Docker tests when Docker isn't running; register `--live`) and Docker tests for every sandbox safety test in `SPEC.md` § Acceptance tests. Fix whatever they find in `sandbox.py`.

**Acceptance criteria:**
- [ ] Tests for: no network, `; touch /tmp/pwned` never reaching the host, timeout (including background processes), output cap, Ctrl+C killing everything but PID 1, deny-listed and ignored files absent, `rm -rf /workspace` still diffable, subfolder run applying at the top folder, failed `git apply --check` leaving the repo unchanged, no labelled container left after exit
- [ ] Without Docker running, these tests are skipped, not failed

**Verification:**
- [ ] Tests pass: `uv run pytest tests/test_sandbox.py`
- [ ] All checks pass

**Dependencies:** Task 1

**Files likely touched:** `tests/conftest.py`, `tests/test_sandbox.py`, `src/coding_agent/sandbox.py`

**Estimated scope:** Medium

---

## Checkpoint A: review with the user

---

## Task 3: `run_command` tool and config

**Description:** `config.py` holds the constants from L9 and S9 (the sandbox reads its limits and deny list from it). `tools.py` holds `RunCommandInput`, the hand-written `input_schema` constant, the tool description and a handler that validates input, calls `Sandbox.run` and returns the result text.

**Acceptance criteria:**
- [ ] Invalid input (empty command, timeout outside 1–300) returns an `is_error` result with the pydantic message
- [ ] A non-zero exit is a normal result starting `exit_code: <n>`; a timeout is `is_error`
- [ ] A unit test checks the `input_schema` constant agrees with `RunCommandInput`

**Verification:**
- [ ] Tests pass: `uv run pytest tests/test_tools.py`
- [ ] All checks pass

**Dependencies:** Task 1

**Files likely touched:** `src/coding_agent/config.py`, `src/coding_agent/tools.py`, `src/coding_agent/sandbox.py`, `tests/test_tools.py`

**Estimated scope:** Small

---

## Task 4: Agent loop

**Description:** `prompts.py` (system prompt) and the loop in `agent.py`, implementing L1–L9 with an injected client and sandbox. Check current API docs for streaming, effort and prompt caching first.

**Acceptance criteria:**
- [ ] Unit tests with a fake client (replies built as `anthropic.types.Message`) cover: tool use then `end_turn`; `max_tokens` with a cut-off tool call stops without running it; the 50-call cap; the context backstop; API error; Ctrl+C during a command and during streaming
- [ ] Every request has byte-identical `system` and `tools`; all tool results for a turn are in one user message
- [ ] One recorded reply in `tests/fixtures/` loads as a `Message`

**Verification:**
- [ ] Tests pass: `uv run pytest tests/test_agent.py`
- [ ] All checks pass

**Dependencies:** Task 3

**Files likely touched:** `src/coding_agent/prompts.py`, `src/coding_agent/agent.py`, `tests/fakes.py`, `tests/test_agent.py`, `tests/fixtures/`

**Estimated scope:** Medium

---

## Checkpoint B: review with the user

---

## Task 5: Review, apply, trajectory and the `coding-agent` command

**Description:** Wire it together: `__main__.py` runs start → copy in → baseline → loop → review → apply or discard → trajectory → cleanup (`SPEC.md` § How a run works, D1–D6, § Output, § Trajectory). Remove the demo from `sandbox.py`.

**Acceptance criteria:**
- [ ] Empty diff says "no file changes"; otherwise the stat then the diff, then `[a]pply / [d]iscard`; Ctrl+C or Ctrl+D at the prompt discards
- [ ] A failed `git apply --check` saves the patch to `.agent/patches/` and says why
- [ ] A trajectory JSON is written on normal exit, error and Ctrl+C, without the API key
- [ ] `uv tool install --editable .` then `coding-agent "…"` works from any repo

**Verification:**
- [ ] Tests pass: `uv run pytest`
- [ ] All checks pass
- [ ] Manual check: the user runs it once on a scratch repo with the real API

**Dependencies:** Tasks 2 and 4

**Files likely touched:** `src/coding_agent/__main__.py`, `src/coding_agent/agent.py`, `src/coding_agent/sandbox.py`, `tests/test_agent.py`, `pyproject.toml`

**Estimated scope:** Medium

---

## Task 6: Live task tests and README

**Description:** Live tests for the four task tests, run against the existing toy repo at `../toy-repo` (a sibling of this repo, with the planted off-by-one bug in `calc/ranges.py`). Each test copies the toy repo into `tmp_path` first, so the original is never changed. Skipped unless `--live` and a key are given; only the user runs them. Plus a README covering install, run and the security model in plain terms.

**Acceptance criteria:**
- [ ] `uv run pytest` skips the live tests; `uv run pytest --live` runs them (user only)
- [ ] README says: build the image, install, run, add `.agent/` to `.gitignore`, keep Docker Desktop's disk limit below your free space, read the whole diff before applying

**Verification:**
- [ ] All checks pass
- [ ] The user runs `uv run pytest --live` and the four task tests pass

**Dependencies:** Task 5

**Files likely touched:** `tests/test_live.py`, `README.md`

**Estimated scope:** Small

---

## Checkpoint C: every `SPEC.md` acceptance test checked off; review with the user; open the PR
