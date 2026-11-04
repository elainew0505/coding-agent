# MVP detailed requirement

The MVP (Phase 0) is a command-line tool: `coding-agent "<task>"` runs one task, with Claude working through a `run_command` tool and three file tools (§ File tools) inside a throwaway Docker container that holds a copy of your repo. Nothing touches your real repo until you review the final `git diff` and choose to apply it. It is done when it fixes a planted bug in a toy repo and the diff applies cleanly.

The design follows [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent): one task per run, every command runs in a fresh shell, and the history is a plain linear list of messages saved at the end as a trajectory. One difference: Claude calls `run_command` through native tool calling instead of writing a bash block that the harness parses out of its text. That makes malformed output impossible and a task ends when Claude stops calling tools, so no completion marker is needed. We don't use the Anthropic-defined `bash` tool because its contract is a persistent shell session. Unlike mini-swe-agent, reading and editing files also have their own tools, so the model doesn't have to get `sed` and heredoc quoting right for every edit ([0004](docs/decisions/0004-file-tools.md)); `--bash-only` turns them off, so the eval can compare the two.

## Scope

**In scope**

- One task per run from the terminal, one user, one run at a time (S7 removes any other run's container)
- Claude through the Anthropic Python SDK, using native tool calling
- `run_command` plus three file tools (`read_file`, `write_file`, `edit_file`), all executed inside a Docker container with no network
- Your repo copied into the container at the start; changes leave only as a diff you review
- Ctrl+C to stop the task at any time
- Nothing in the workspace configures the agent: no config, instruction or hook file in your repo is read by the harness. A repo you point the agent at must not be able to change its limits or behaviour (see [0001](docs/decisions/0001-sandbox-instead-of-approvals.md))

**Out of scope (later phases)**

- Benchmark suite (Phase 1, see [SPEC-Phase1.md](SPEC-Phase1.md))
- Follow-up tasks in one session (a REPL), todo lists, project instruction files, history summaries (Phase 2)
- Git branch and commit on your repo, network access per command, auto-approve rules (Phase 3). Until there's network, the agent image only has the toy repo's dependencies (S1)
- MCP, sub-agents, session resume, TUI (Phase 4)

## How a run works

1. **Start:** `coding-agent [--model ID] "<task>"` anywhere in your repo starts a labelled container from the agent image. The harness works from the repo's top folder (`git rev-parse --show-toplevel`) for every host step, because `git apply` reads patch paths relative to it.
2. **Copy in:** the files git would track (tracked, plus untracked ones `.gitignore` doesn't exclude), minus `.agent/` and a deny list of secret files, are streamed into `/workspace` in the container.
3. **Baseline:** the harness (not the model) creates a git repo at `/harness.git`, outside the workspace, and commits a baseline.
4. **Loop:** Claude calls `run_command` and the file tools until it stops asking for tools or a limit is hit; each call and a short output preview print as they run.
5. **Diff out:** the harness stages everything and takes `git diff --cached --binary <baseline>`.
6. **Review:** you see a file summary and the full diff, then choose apply or discard.
7. **Apply:** `git apply --check`, then `git apply` on your real repo.
8. **Clean up:** the trajectory is saved and the container is removed, including after an error or Ctrl+C.

## Sandbox

The container is the safety boundary: nothing inside it can reach your files, your network or your credentials.

| ID | Requirement |
| --- | --- |
| S1 | Agent image built from a small `Dockerfile`: `python:3.12-slim` pinned by digest (`FROM python:3.12-slim@sha256:<digest>`, so the base can't change under you) plus `git` (not in the base image), `WORKDIR /workspace` (copy-in needs the folder to exist) and the toy repo's test dependencies (no network at run time, so dependencies are baked in). This means the MVP can only run tests for the toy repo; on any other repo, test commands will fail on missing packages |
| S2 | Start with `docker run -d --name agent-<id> --label coding-agent=1 --network none --memory 2g --cpus 2 --pids-limit 256 --cap-drop ALL --security-opt no-new-privileges <image> sleep infinity` |
| S3 | No volume mounts, no Docker socket, no `-e` secrets; the API key stays in the host Python process |
| S4 | Copy in with a tar stream. List files with `git ls-files -z --cached --others --exclude-standard`, drop paths that no longer exist on disk and paths matching S9, then `COPYFILE_DISABLE=1 tar -c --no-xattrs --null --no-recursion -T - \| docker exec -i agent-<id> tar -x --no-same-owner -C /workspace`. `COPYFILE_DISABLE=1` and `--no-xattrs` stop macOS tar adding `._*` files and Apple attributes. `--no-same-owner` is required: without it, tar as root tries to keep your uid, `--cap-drop ALL` forbids that, and tar exits 2 |
| S5 | Baseline inside the container: `git init`, `git add -A --force` (everything in `/workspace` was chosen by S4, including files the repo tracks despite `.gitignore`, so their later edits are diffed too), `git -c user.name=agent -c user.email=agent@local commit -m baseline`; store the commit SHA. Every harness git command runs as `git --git-dir=/harness.git --work-tree=/workspace …`, so there is no `.git` in the workspace for the model to delete, commit to or rewrite |
| S6 | Remove the container with `docker rm -f agent-<id>` in a `try/finally` and an `atexit` hook |
| S7 | On startup, remove leftover containers only if they carry the `coding-agent=1` label, never by name pattern or `docker ps` alone |
| S8 | Fail fast with a clear message, before starting a container, if Docker isn't running, the image is missing, the current folder isn't a git repo, or neither `ANTHROPIC_API_KEY` nor `ANTHROPIC_AUTH_TOKEN` is set |
| S9 | Never copy: anything under `.agent/`, or a file whose name matches `.env`, `.env.*`, `*.pem`, `*.key`, `id_rsa*`, `id_ecdsa*` or `id_ed25519*` (case-insensitive: macOS treats `Server.PEM` and `server.pem` as the same file), even if git tracks it. The list lives in `config.py`. Anything copied in can be read by the model and so sent to the API and written to the trajectory |

The agent runs as root inside the container. That is fine here: with no mounts, root inside cannot create root-owned files or stray `.git` folders on your machine. `--cap-drop ALL` and `no-new-privileges` still take away root's extra kernel permissions, which nothing the agent does needs, so a container escape is harder if one exists.

## Agent loop

| ID | Requirement |
| --- | --- |
| L1 | All model calls go through one thin wrapper, `Model.query(messages)` in `model.py`; the loop never touches the SDK. `Model(name)` works with any current model: the default is `claude-haiku-5-5` (the cheapest), and `--model claude-opus-5-5` picks the strongest for one run. Each call sends the system prompt, full message history and the run's tool set (§ File tools) as a streaming request (`client.beta.messages.stream(...)` + `get_final_message()`, which avoids timeouts on long replies), with `output_config.effort` set explicitly (default `medium`), top-level `cache_control: {type: "ephemeral"}` so the growing history is cached, and, only for models that have one (Opus 5.5, Opus 5, Fable 5.1, Sonnet 5.5; not Haiku 5.5), `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`) so a safety-classifier refusal is retried on Anthropic's recommended fallback model. Without a fallback, a refusal stops the task (L3). `eager_input_streaming` is off: the tool's input is one short command, and leaving it off keeps the server's input validation |
| L2 | Append the full response content as the assistant message, then act on `stop_reason`. `tool_use`: run each tool call in order and send all the results, in the same order, in a single user message |
| L3 | `end_turn`: the task is done; print the final text and go to review. Any other `stop_reason` (e.g. `max_tokens`, `refusal`) ends the task with a message naming it, then goes to review, even if the reply contains a tool call, since it may be cut off |
| L4 | Stop after 50 model calls, tell the user the cap was hit, and still go to review |
| L5 | Context backstop: after each response, total its input tokens from `usage` (`input_tokens + cache_read_input_tokens + cache_creation_input_tokens`; with caching on, `input_tokens` alone leaves out the cached part). Above 150,000, finish that response's tool calls, say the context is full, and go to review |
| L6 | On an API error, rely on the SDK's built-in retries (`max_retries=3`, set explicitly; the SDK default is 2), then end the task with the error and go to review. A missing key is caught up front (S8). Any other exception is a bug: it is not caught, and the run crashes after the trajectory is written and the container removed |
| L7 | Invalid tool input, timeouts and harness errors (e.g. `docker exec` failing) go back to the model as `is_error` tool results, never a crash. A non-zero exit code is a normal result, not an error: a failing test run is information, as in mini-swe-agent |
| L8 | Ctrl+C stops the task: kill any running command (see below) and go to review. A reply that was still streaming is dropped |
| L9 | Model name, effort, max tokens, call cap, context threshold and timeouts live in `config.py` |

## The run\_command tool

| Field | Spec |
| --- | --- |
| Input | `command` (non-empty string), `timeout` (integer seconds, default 60, 1–300) |
| Validation | A pydantic model `RunCommandInput` validates the input before anything runs; on failure the pydantic error message goes back as an `is_error` result. The tool's `input_schema` is a hand-written constant next to the model in `tools.py`, with a unit test checking that the two agree, so it stays byte-identical across turns |
| Runs as | `docker exec -w /workspace agent-<id> timeout <t> bash -lc <command>`, passed to `subprocess` as an argument list, never a string with `shell=True`. A string would let the host shell interpret the command, so `"; rm -rf ~"` would run on your machine |
| Returns | Text: `exit_code: <n>` on the first line, then combined stdout and stderr |
| Output cap | 10,000 characters: the first 5,000 and last 5,000 with `[truncated N chars]` between. Output is read in chunks while the command runs, keeping only the first 5,000, a rolling last 5,000 and a count, so a command that prints forever can't fill host memory. Bytes are decoded as UTF-8 with `errors="replace"` |
| On timeout | The in-container `timeout` kills the command (exit 124); the host gets `timeout + 10` as a backstop |
| Approval | None per command; the diff review is the checkpoint |

The tool description tells the model:

- Each call starts a fresh shell in `/workspace`; `cd` does not carry over, so chain with `&&`
- There is no network: no `pip install`, `curl` or `git clone`
- Edit files with heredocs (`cat <<'EOF' > file`), `sed -i` or a short Python script
- `/workspace` is not a git repo and must not become one: don't run `git init` anywhere in it; the harness tracks changes itself

The in-container `timeout` matters: killing the host-side `docker exec` alone leaves the command running inside the container. For the same reason, on Ctrl+C or when the host backstop fires, the harness runs `docker exec agent-<id> bash -c 'kill -KILL -1'` (the slim image has no `kill` program, only the shell builtin). That kills every process in the container except PID 1 (`sleep infinity`), including anything the command started in the background, so nothing is still writing files when the diff is taken.

Known limitation: PID 1 is `sleep`, which never reaps orphaned processes, so killed background processes stay as zombies and count toward `--pids-limit 256`. In a 50-call run that is a handful at most. `docker run --init` would reap them, but then `kill -1` would also kill `sleep infinity` and stop the container.

## File tools

Three tools for reading and editing files, next to `run_command` ([0004](docs/decisions/0004-file-tools.md)). Each one runs inside the container through `docker exec` with an argument list, like `run_command`; the file's bytes pass through the host process's memory, but nothing is written to or read from your disk. Each has a pydantic input model and a hand-written `input_schema`, checked against each other by a unit test.

| ID | Requirement |
| --- | --- |
| T1 | **Tool sets.** By default every request sends all four tools, and the system prompt and `run_command` description name the file tools. `--bash-only` sends `run_command` alone with today's texts, so the eval can compare the two. The set is fixed for the whole run (byte-identical `tools` and `system`) and recorded in the trajectory |
| T2 | **Paths.** Any path the model gives is relative to the working folder (`/workspace`) or absolute inside the container, and is always passed as its own argument after `--`, never inside a shell string. The container is the boundary, so paths outside the working folder are allowed there, as they are for `run_command` |
| T3 | **`read_file`** `{path, start_line?, end_line?}`: returns the lines with their numbers (`cat -n` style). It reads at most 1,000,000 bytes (`head -c` in the container). Output stops at 10,000 characters and each line at 2,000, and a footer gives the lines shown, the total, and the `start_line` to continue from. A file with a NUL byte in its first 8,000 bytes is reported as binary, as an error |
| T4 | **`write_file`** `{path, content}`: writes the whole file, creating it and any missing folders, or replacing it. Content is at most 1,000,000 characters. The bytes go in on `docker exec -i` stdin to `sh -c 'mkdir -p … && cat > "$1"'`, with the path as `$1`, so no part of the path or content is ever parsed as shell. Returns the line count written |
| T5 | **`edit_file`** `{path, old_string, new_string, replace_all?}`: `old_string` must appear exactly once (or at least once with `replace_all`). Zero matches, more than one without `replace_all`, `old_string` equal to `new_string`, a file that isn't valid UTF-8, a file over 1,000,000 bytes, or a result over 1,000,000 bytes are `is_error` results that say what to do next. The replace happens in host memory and the file is written back as in T4. Returns the edited lines with their numbers |
| T6 | Every failure around a file tool (bad input, a missing file, Docker failing) is an `is_error` result with a short message, never a crash (L7). Each call prints one line, e.g. `read_file src/app.py:10-40` or `edit_file src/app.py`, through `visible()` |

The `run_command` description (with all tools) says to use the file tools for reading and editing, and `run_command` for everything else: searching (`grep`, `find`), tests, scripts, git, package tools.

## Review and apply

| ID | Requirement |
| --- | --- |
| D1 | When the task ends, run `git add -A && git diff --cached --binary <baseline>` in the container (with the S5 flags) |
| D2 | Empty diff: say "no file changes" and skip the prompt |
| D3 | Show a summary (`--stat`) first, then the full diff |
| D4 | Ask `[a]pply / [d]iscard`; nothing is written to your repo before this answer. Only `a`/`apply` applies and only `d`/`discard`, Ctrl+C or Ctrl+D discard; anything else asks again, so a typo can't throw the work away |
| D5 | Apply: run `git apply --check` on the host, then `git apply`. If the check fails, write the patch to `.agent/patches/<timestamp>.patch` and say why, so the work isn't lost |
| D6 | A stopped, capped or failed task still goes to review, so partial work is never lost silently |

Known limitations, accepted for the MVP (none can change your repo without the review):

- **Global ignore rules.** Copy-in (S4) also honours your global gitignore (`core.excludesFile`) and `.git/info/exclude`, but the container's harness git only sees the repo's `.gitignore`. Files the model creates that only your global rules ignore (e.g. a `.venv/`) show up in the diff; discard or ignore them in the repo's `.gitignore`.
- **`/harness.git` is writable by the model.** It runs as root in the container, so it could delete the harness repo; the diff then fails and the run ends without a review. Your repo is unchanged; the run's work is lost.
- **A dead container.** If the container stops mid-run, each later command reports Docker's error as an ordinary non-zero exit, so the model may spend calls retrying before the run ends; the diff then fails and there is nothing to review.

## Output

- Print each command as `$ <command>` with the first 5 lines of its output, and Claude's text when a reply arrives (text isn't streamed token by token: on Opus 5.5 the notes between tool calls come back as hidden thinking, so there would be little to show)
- At the end, print the stop reason and token usage (input and output)
- Everything printed that came from the model or the sandbox (Claude's text, commands, output previews, the diff) shows control characters and bidi overrides as escapes (`\x1b`, `\u202e`). Printed raw, they could move the cursor or erase lines, so a file the model wrote could hide part of the diff you approve

## System prompt

A short, fixed text in `prompts.py` that tells the model:

- It is a coding agent working on a copy of the user's repo at `/workspace`, with no network
- Which tools it has: with all tools, to read and edit with the file tools and use `run_command` for everything else; with `--bash-only`, today's text, which names `run_command` as its one tool
- To explore (`ls`, `cat`, `grep`) before editing
- To run the tests after a change when the repo has them
- That the user reviews a diff of all its changes at the end, so it should keep changes focused
- To finish with a short summary of what it changed and how it checked

## Trajectory

- At the end of every run (in the same `finally` as S6), write one JSON file to `.agent/trajectories/<timestamp>.json` on the host: the task, tool set name, system prompt, tool definitions, full message history, total usage and stop reason. If the harness crashes mid-task, the history so far is still written, with stop reason `crashed`
- Never write the API key

## Project layout

```
pyproject.toml    # uv project; deps: anthropic, pydantic; dev: pytest, ruff, pyright
uv.lock
Dockerfile        # agent image
src/coding_agent/
  __main__.py     # entry point for the `coding-agent` command
  agent.py        # agent loop, review prompt, trajectory
  model.py        # Model.query: the one place that calls the Anthropic API
  sandbox.py      # start, copy_in, exec, diff, cleanup
  tools.py        # run_command and the file tools: input models, input_schema constants, handlers, tool sets
  prompts.py      # system prompt, tool description
  config.py       # model, limits, timeouts, image name, copy-in deny list
tests/
```

The API key comes from the `ANTHROPIC_API_KEY` environment variable on the host. Add `.agent/` to your repo's `.gitignore`.

## Testing

Modelled on mini-swe-agent's test suite. Three levels, all under `tests/`:

| Level | Runs with | What it uses | Covers |
| --- | --- | --- | --- |
| Unit | `uv run pytest` | A fake client that returns replies scripted in each test, and a fake sandbox | The L and D rules: stop reasons, call cap, context backstop, Ctrl+C, input validation, truncation, request shape |
| Docker | `uv run pytest` (skipped automatically if Docker isn't running) | A real container from the agent image, plus the fake client | Every S rule and the safety tests below |
| Live | `uv run pytest --live` with `ANTHROPIC_API_KEY` set | The real API on a toy repo, with a low call cap | The task tests below |

- **Fake client.** Replies are built in the test with a helper that returns real `anthropic.types.Message` objects (e.g. `reply(tool_calls=["ls"])`, `reply(text="done", stop_reason="max_tokens")`), so pydantic rejects any shape the SDK wouldn't produce. The fake records every request so tests can check that `system` and `tools` are byte-identical across turns and that tool results arrive in one user message. One or two replies recorded from the real API sit in `tests/fixtures/` to catch drift from the SDK's shape.
- **Docker tests** use `--network none` like the real sandbox, so they need no network. Each removes its container in `finally`.
- **Live tests** are skipped unless both `--live` is passed and the key is set. They cost money, so an AI agent never runs them; the test file says so at the top.

## Acceptance tests

The MVP passes when every task test succeeds on a toy repo and every safety test leaves your machine untouched. Each item says which test level checks it.

**Task tests** (live)

- [x] "What does this project do?" answered from reading files, with an empty diff
- [x] Fix a planted off-by-one bug so the failing test passes; the diff applies cleanly
- [x] Add a new function plus a test for it, and run the tests
- [x] Recover from a wrong first edit after seeing the test failure (the test plants a wrong fix, `range(start, end + 2)`, before the run, since a real model can't be made to get its first edit wrong)

**Safety tests**

- [x] `rm -rf /workspace` inside the container leaves your repo unchanged, and the review still works and shows every file deleted (docker)
- [x] A request to `https://example.com` fails (docker; the image has no curl, so the test uses Python)
- [x] A command containing `"; touch /tmp/pwned` never creates `/tmp/pwned` on the host (docker)
- [x] `yes` is cut off at the timeout with output capped at 10,000 characters, and the harness never holds more than the head, tail and count (docker)
- [x] Run from a subfolder, the diff applies at the repo's top folder (docker)
- [x] `false` returns `exit_code: 1` as a normal result, not `is_error` (unit)
- [x] A tracked `.env`, an ignored `secrets.txt` and `.agent/` are all absent from `/workspace` (docker)
- [x] `sleep 1000` is killed at the timeout and the loop continues (docker)
- [x] Ctrl+C during `sleep 1000 & sleep 1000` leaves nothing running but PID 1 in the container (zombies aside) before the diff is taken, then goes to review (docker)
- [x] Ctrl+C while a reply is streaming goes to review without appending the partial reply (unit)
- [x] With the context threshold set to 5,000, the task stops, says the context is full and goes to review (unit)
- [x] Discarding leaves your repo unchanged; a failed `git apply --check` leaves it unchanged and saves the patch (docker)
- [ ] `write_file` and `edit_file` with a path or content holding `"; touch /tmp/pwned`, `$(…)` or backticks write exactly those characters and never create `/tmp/pwned`, on the host or in the container (docker)
- [ ] `read_file` on a 50 MB file holds at most 1,000,000 bytes in the harness (docker)
- [ ] `edit_file` with zero matches, two matches, a binary file, or a `replace_all` result over 1,000,000 bytes returns `is_error` and leaves the file unchanged (unit)
- [ ] `--bash-only` sends exactly today's `system` and the `run_command` tool alone (unit)
- [x] Normal exit, Ctrl+C and a crash all leave no `coding-agent` container running (`docker ps --filter label=coding-agent=1`) and write a trajectory (docker)
