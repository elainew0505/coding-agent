# Spec: coding-agent

Status: **approved 2026-10-29**

## Objective

A command-line coding agent. You give it a task and a git repository; it calls Claude in a tool-use loop, runs commands and edits files inside a Docker sandbox, and stops when the task is done or a limit is hit. Its changes are left uncommitted in your working tree for you to review.

**User:** a developer (initially the repo owner) running it locally with Docker. Not multi-user, not hosted.

**Why:** a small, readable agent where every safety property is enforced in code and covered by tests, rather than trusted to the prompt.

### User stories

- I run `coding-agent run "add a --verbose flag to cli.py" --workspace ~/src/proj` and watch it work: Claude's text, each command and its result, each file edit.
- When it finishes I see a summary of what changed (`git diff --stat`) and review the diff myself.
- It can't touch anything outside the workspace, can't reach the network unless I pass `--allow-network`, and can't commit.
- If it loops, hangs or runs away, a limit stops it and tells me which limit.
- Ctrl-C stops it cleanly and removes the container.

### Decisions (agreed 2026-10-29)

| Topic | Decision |
|---|---|
| Workspace | The target folder is mounted read-write and edited directly. It must be a git repo. |
| Approvals | Fully autonomous. The sandbox is the safety boundary; no per-command prompts. |
| Network | Off by default; `--allow-network` enables it for one run. |
| Git | The agent never commits. `.git/` is mounted read-only in the sandbox, so no commit can be written. |
| Where tools run | Both tools (bash and file edits) execute inside the container. Nothing model-issued runs on the host. |

### Out of scope for v1

Interactive chat / REPL; session resume; context compaction; web fetch or search tools; MCP; subagents; reading the workspace's own `CLAUDE.md`/`AGENTS.md`; Windows support; running without Docker.

## Behavior

### CLI

```
coding-agent run TASK [--workspace DIR] [--allow-network] [--max-turns N]
                      [--model ID] [--effort LEVEL] [--image NAME]
coding-agent --version
```

- `--workspace` defaults to the current directory.
- Before starting, the CLI checks: Docker is reachable, `ANTHROPIC_API_KEY` is set, and the workspace is a git repo. If the working tree has uncommitted changes, it prints a warning (they will mix with the agent's in `git diff`) and continues.
- During the run it streams to the terminal with `rich`: model text as it arrives, then each tool call (command or file path) and a truncated view of its result.
- At the end it prints Claude's final message, the stop reason, `git diff --stat` for the workspace (run on the host by our code), and turns/tokens used.

**Exit codes:** `0` task finished (model ended its turn); `1` runtime error (API, Docker); `2` usage or precondition error; `3` a limit was hit; `130` interrupted.

### Agent loop

- Model `claude-opus-5-5` by default, `output_config.effort` set explicitly (default `medium`), requests streamed.
- History is append-only; each turn appends the full `response.content`.
- `system` and `tools` are byte-identical on every turn (prompt caching). The task, workspace path and anything time-varying go in the first user message.
- All `tool_result` blocks for a turn go back in one user message; a failed tool returns `is_error: true` with a short message.
- The loop ends when the model stops without requesting a tool, or when a limit is hit. A response cut off by `max_tokens` or a context-window limit ends the run with exit code `3` and a clear message.
- No forced `tool_choice`, prefill, sampling parameters or `budget_tokens`.
- Exact parameter names and tool versions are verified against current Claude API docs at implementation time (`source-driven-development`, `claude-api` skill).

### Tools

Anthropic-defined, declared without `input_schema`:

- **bash** (`bash_20250124`): a persistent shell session inside the container: working directory and environment carry across calls, starting in the workspace. `restart` starts a fresh session. Each command has a wall-clock timeout; on timeout the process is killed and the tool returns an error. stdout and stderr are combined, truncated (head and tail kept, with a note of how much was cut), and returned with the exit code.
- **text editor** (`text_editor_20250728`): `view`, `create`, `str_replace`, `insert`, executed inside the container. Every path is resolved (symlinks included) and must be inside the workspace; anything else returns an error. Writes into `.git/` are rejected. Large files are truncated on `view`.

Tool inputs are validated with pydantic before any action.

### Sandbox

One container per run, created at start and removed at the end (including on error and Ctrl-C).

- Default image: the public `python:3.12-slim`, pinned by digest. Any image with `bash` can be used via `--image`. No custom image or build step in v1.
- Runs as the host user's uid:gid, never root; `--cap-drop ALL`; `--security-opt no-new-privileges`; `--network none` unless `--allow-network`.
- Memory, CPU and pid limits set.
- Mounts: the workspace read-write at `/workspace`, its `.git/` read-only on top. Nothing else from the host.
- Host environment is not passed through. `ANTHROPIC_API_KEY` never enters the container.

### Limits (defaults, all configurable)

| Limit | Default |
|---|---|
| Turns per run | 50 |
| Wall-clock timeout per command | 120 s |
| Tool result size returned to the model | 30,000 characters |
| Total tokens per run (input + output) | 2,000,000 |

### Configuration

Precedence: CLI flags > environment variables (`CODING_AGENT_*`) > user config file (`~/.config/coding-agent/config.toml`) > defaults. Loaded with `pydantic-settings`; the API key is a `SecretStr`.

**Config is never read from the workspace.** A repository you point the agent at could otherwise turn on network or loosen limits.

## Tech Stack

As in [CLAUDE.md](CLAUDE.md#stack): Python 3.12+ with `uv`; `anthropic`; `typer` + `rich`; `pydantic` / `pydantic-settings`; `pytest` + `pytest-asyncio`; `ruff`; `pyright`; Docker. Exact versions are whatever `uv.lock` pins.

## Commands

```bash
uv sync                                        # install
uv run coding-agent run "TASK" --workspace DIR # run the agent
uv run pytest                                  # unit tests: no network, no Docker
uv run pytest -m docker                        # sandbox tests: real Docker, no API
uv run pytest -m live                          # end-to-end: real API (costs money)
uv run ruff check . && uv run ruff format .
uv run pyright
```

## Project Structure

```
src/coding_agent/
  cli.py          → typer app: argument parsing, preflight checks, exit codes
  config.py       → Settings (pydantic-settings), limits, defaults
  loop.py         → agent loop; takes an injected model client and sandbox
  prompts.py      → system prompt and tool declarations (constants, never per-turn)
  render.py       → rich terminal output
  sandbox.py      → container lifecycle, exec, persistent shell session
  tools/
    bash.py       → bash tool handler
    editor.py     → text editor handler and path resolution
tests/
  unit/           → no network, no Docker (fake client, fake sandbox)
  docker/         → marked `docker`: real container, no API
  live/           → marked `live`: real API end-to-end
  fixtures/       → recorded API responses (scrubbed of headers and keys)
docs/decisions/   → decision records
tasks/            → plan.md and todo.md
```

Documentation follows the conventions in [CLAUDE.md § Documentation](CLAUDE.md#documentation): where each kind of doc lives, and that docs change in the same commit as the behavior they describe.

## Code Style

`ruff` formatting and `pyright` strict on `src/`. Type hints everywhere; small functions; pydantic models at boundaries; errors become tool results, not exceptions that escape the loop.

```python
class StrReplace(BaseModel):
    command: Literal["str_replace"]
    path: str
    old_str: str
    new_str: str


def str_replace(sandbox: Sandbox, args: StrReplace) -> ToolResult:
    """Replace exactly one occurrence of old_str in a workspace file."""
    path = sandbox.resolve_in_workspace(args.path)
    if path is None:
        return ToolResult.error(f"{args.path} is outside the workspace")

    text = sandbox.read_text(path)
    count = text.count(args.old_str)
    if count != 1:
        return ToolResult.error(f"old_str must match exactly once, found {count}")

    sandbox.write_text(path, text.replace(args.old_str, args.new_str))
    return ToolResult.ok(f"Edited {args.path}")
```

Naming: `snake_case` modules and functions, `PascalCase` classes, no abbreviations beyond common ones (`cfg`, `args`). Comments explain why, especially around security checks.

## Testing Strategy

Patterns in [.claude/references/testing-patterns.md](.claude/references/testing-patterns.md). Test-first for every behavior (`test-driven-development`).

| Level | Marker | What it covers |
|---|---|---|
| Unit | (default) | Loop logic with a fake client replaying `tests/fixtures/`; tool handlers against a fake sandbox; config precedence; CLI arguments and exit codes; request shape (system/tools identical across turns) |
| Sandbox | `docker` | Real container: path escapes rejected, timeout kills, output truncated, network off/on, `.git` read-only, key absent from container env, container removed on exit and on Ctrl-C |
| End-to-end | `live` | A few real tasks against the API on a temp git repo |

`uv run pytest` runs only unit tests (`addopts = "-m 'not docker and not live'"`). No coverage percentage target; every tool handler, safety check and exit path must have a test. Abuse cases (`..`, absolute paths, symlinks, huge output, `sleep infinity`, `git commit`) are tests, not comments.

## Boundaries

For anyone (human or agent) building this project:

- **Always:** write the failing test first; run `pytest`, `ruff` and `pyright` before committing; keep `system` and `tools` constant across turns; validate tool inputs; update the spec before changing a decision recorded here.
- **Ask first:** adding a dependency; changing any sandbox flag, mount or network default; adding a tool; changing a limit's default; changing the CLI interface or exit codes.
- **Never:** run model-issued commands on the host; pass `ANTHROPIC_API_KEY` or host env into the container; read config from the workspace; commit fixtures containing keys or auth headers; disable or skip a failing safety test.

## Success Criteria

v1 is done when all of these hold:

1. `coding-agent run "create hello.py that prints hello" --workspace <temp repo>` creates the file, exits `0`, prints the diff stat, and leaves the change uncommitted. (`live`)
2. Every path escape (`..`, absolute path outside the workspace, symlink to outside, write into `.git/`) returns `is_error: true` and nothing outside the workspace changes. (`docker`)
3. A command that never exits is killed at the timeout and the run continues. (`docker`)
4. Output over the limit is truncated with a note of how much was cut. (unit + `docker`)
5. With default flags, a network request from the sandbox fails; with `--allow-network` it succeeds. (`docker`)
6. Writing anything under `.git/` from inside the sandbox fails. (`docker`)
7. `env` inside the sandbox does not contain `ANTHROPIC_API_KEY`. (`docker`)
8. Hitting the turn or token limit exits `3` and names the limit. (unit)
9. Not a git repo, missing key, or Docker unavailable each exit `2` with a one-line explanation; a dirty tree prints a warning and continues. (unit)
10. Ctrl-C exits `130` and no container from the run remains. (`docker`)
11. `system` and `tools` in every request of a run are byte-identical. (unit)
12. `uv run pytest`, `uv run pyright` and `uv run ruff check .` all pass; `grep -rE 'sk-ant-|x-api-key|authorization' tests/fixtures` finds nothing.

## Open Questions

Resolved 2026-10-29 by taking the simplest option for v1:

1. Dirty working tree: warn and continue (no `--allow-dirty` flag).
2. Effort default: `medium`.
3. Budget: a token cap (2M per run), not a cost cap.
4. Sandbox image: public `python:3.12-slim` pinned by digest, overridable with `--image`.

None open.
