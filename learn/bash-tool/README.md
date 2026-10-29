# The bash tool

**Real code:** not written yet. This is based on `SPEC.md` → "Tools" (bash) and "Sandbox". The real code will live in `src/coding_agent/tools/bash.py` and `sandbox.py`.

**In one sentence:** when Claude wants to run a shell command, it sends a `tool_use` block; the bash tool runs that command in a long-lived bash process inside a Docker container and sends back the output and exit code as a `tool_result`.

The four scripts build this up one idea at a time:

| Script | Idea |
|---|---|
| `01_run_one_command.py` | Run one command from Python; capture output, exit code, timeout |
| `02_persistent_shell.py` | Keep one bash process alive so `cd` and `export` carry over |
| `03_tool_result.py` | Turn the model's `tool_use` into a `tool_result`; truncate long output |
| `04_in_docker.py` | Run bash inside a locked-down container instead of on your machine |

## New concepts

- **Process:** a running program. When Python runs `bash`, the OS starts a *child process* with its own memory, current directory and environment variables. When it exits, all of that is gone.
- **stdin / stdout / stderr:** every process has three standard streams: one input (stdin) and two outputs (stdout for normal output, stderr for errors and warnings). In a terminal all three are your keyboard and screen. From Python you can attach *pipes* to them and read or write them like files.
- **Exit code:** a number a process returns when it finishes. `0` means success; anything else is failure, and the number's meaning depends on the program. In bash, `$?` holds the exit code of the last command.
- **Timeout / kill:** a command can run forever (`sleep infinity`, a server, a program waiting for input). The only safe way to handle that is to stop waiting after N seconds and *kill* the process (send it a signal the OS enforces).
- **Blocking read:** reading from a pipe waits until data arrives. If the command never prints anything, a plain `read` waits forever. Step 2 gets around this with a **thread** (a second line of execution in the same program) that does the waiting, plus a **queue** (a thread-safe list) that we read from with a timeout.
- **Tool use (Anthropic API):** you tell the model which tools exist. When it wants one, its response contains a `tool_use` block with an `id`, a `name` and an `input`. You run the tool and reply with a user message holding a `tool_result` block that has the same `tool_use_id`. `is_error: true` tells the model that the tool itself failed.
- **Docker image vs container:** an *image* is a read-only template (here, a minimal Linux with Python). A *container* is a running, isolated instance of it: its own filesystem, users, processes and network, sharing the host's kernel.
- **Mount (`--volume`):** makes one host folder visible inside the container. It's the only way files cross between them, so it decides exactly what the agent can touch.
- **Capabilities (`--cap-drop ALL`):** Linux splits root's powers into ~40 *capabilities* (change file owners, bind low ports, load kernel modules...). Dropping all of them means that even a process that becomes root inside the container can do very little.

## Walkthrough

### 01 — one command

```python
subprocess.run(["bash", "-c", command], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=...)
```

- `["bash", "-c", command]` starts bash and asks it to run the string `command`. Passing a list (not one string) means Python starts `bash` directly, without another shell in between.
- `stderr=subprocess.STDOUT` merges errors into normal output. If you captured them separately, you'd lose the order in which lines appeared, and order matters when reading a failing build.
- `timeout=` makes `run` kill the process and raise `TimeoutExpired`.

The examples show the problem that motivates step 2: `cd /tmp && pwd` prints `/tmp`, but the next `pwd` prints your original folder, because each call is a fresh bash process.

### 02 — one shell that stays alive

- `subprocess.Popen(["bash"], stdin=PIPE, ...)` starts bash *without* waiting for it to finish. bash sits there reading commands from its stdin, just like a terminal.
- `stdin.write(...)` + `flush()` "types" a command into it. `flush()` matters: Python buffers writes, and without it bash may never receive the command.
- **The marker trick.** bash doesn't tell us when a command is finished; it just waits for the next one. So after each command we also send
  `printf '\n__COMMAND_DONE__ %s\n' $?`
  which prints a line we'll recognise, plus `$?`, the exit code of the command just before. We read lines until we see the marker, so everything before it is the command's output. For `ls /does-not-exist` the last line would be `__COMMAND_DONE__ 2`.
- **The reader thread.** `for line in self.process.stdout` blocks until bash prints something. If the command hangs, that wait never ends. So a background thread does nothing but read lines and `put` them on a queue, and `run()` calls `self.lines.get(timeout=...)`, which gives up after the timeout and raises `queue.Empty`.
- On timeout we `kill()` the whole shell. A real implementation then starts a new one (the API's bash tool has a `restart` input for this).

### 03 — tool_use in, tool_result out

- `fake_model_content` is what `response.content` looks like when Claude asks to use bash: a list of blocks, some `text`, some `tool_use`. One response can contain several tool calls.
- `handle_bash_call` reads `tool_use["input"]["command"]`, runs it, and builds a `tool_result` whose `tool_use_id` matches the call's `id`. That id is how the model knows which output belongs to which command.
- **Non-zero exit ≠ error.** `cat missing.txt` exits `1`, but the tool worked fine: it ran the command and is reporting what happened. So that result has no `is_error`, and the model reads the exit code itself. Only the timeout (`sleep 10`) is a tool failure, with `is_error: True`.
- `truncate` keeps the first and last 100 characters and notes how many were cut. Output goes straight into the model's context, which costs tokens and attention; `seq 1 500` prints ~1,900 characters. Keeping the tail matters because errors and test summaries come last.
- All results go into **one** user message (`{"role": "user", "content": results}`). The API expects every tool call from a turn to be answered together.

### 04 — inside Docker

- `docker run --detach ... sleep infinity` starts a container whose only job is to stay alive; `--detach` returns straight away and prints its id.
- Each flag removes a way to cause harm. See the table under "Shell commands".
- `docker exec -i <id> bash` starts bash *inside* that container with its stdin and stdout connected to ours, so it can be driven exactly like step 2. For brevity this step sends all commands at once with `communicate()`, which writes the script, closes stdin (so bash exits when done) and reads everything.
- `shlex.quote(...)` wraps text in quotes that are safe for bash, so the command text can be echoed even though it contains quotes of its own.
- `finally: docker stop` makes sure the container is removed even if something above raised an exception. `--rm` then deletes it.

## Shell commands

Commands the scripts run (none of them change anything outside a temp folder, and none cost money):

| Command | Parts | Effect |
|---|---|---|
| `ls /does-not-exist` | list a folder that isn't there | read only; prints an error, exit code 2 |
| `cd /tmp && pwd` | `&&` = run `pwd` only if `cd` succeeded | read only |
| `export GREETING=hi` | set an environment variable for this shell and its children | changes only that shell |
| `seq 1 500` | print the numbers 1 to 500 | read only |
| `sleep 10` | do nothing for 10 seconds | read only |
| `printf '\n__COMMAND_DONE__ %s\n' $?` | `printf` = formatted print; `%s` is filled with `$?` (last exit code) | read only |
| `echo ... > from_container.txt` | `>` = send output into a file, replacing it | writes a file in the temp workspace |
| `env \| grep -c ANTHROPIC \|\| true` | `\|` = pipe output of `env` into `grep`; `-c` = count matching lines; `\|\| true` = make the exit code 0 even when there are no matches | read only |
| `... 2>&1 \| tail -1` | `2>&1` = send stderr to wherever stdout goes; `tail -1` = keep only the last line | read only |

The `docker run` flags in step 4:

| Flag | What it stops |
|---|---|
| `--network none` | No network at all: the agent can't download things or send your code anywhere |
| `--cap-drop ALL` | Removes root's special powers inside the container |
| `--security-opt no-new-privileges` | Stops a process gaining privileges later (e.g. via `sudo` or setuid programs) |
| `--user UID:GID` | Runs as you, not root, so files it creates on the mount belong to you |
| `--memory`, `--cpus`, `--pids-limit` | Stops a runaway command (or a fork bomb) taking down your machine |
| `--volume host:/workspace` | The only host folder visible; nothing else from your disk is reachable |
| `--rm` | Deletes the container when it stops, so nothing is left behind |

`docker run` downloads the `python:3.12-slim` image (~50 MB) the first time. `docker stop --time 0` stops the container immediately instead of waiting 10 seconds for a graceful shutdown.

## Run it

```bash
python3 learn/bash-tool/01_run_one_command.py
python3 learn/bash-tool/02_persistent_shell.py
python3 learn/bash-tool/03_tool_result.py
python3 learn/bash-tool/04_in_docker.py   # needs Docker Desktop running
```

What to expect:

- **01:** `echo hello` → exit 0, `'hello'`. `ls /does-not-exist` → a non-zero code (2 on Linux, 1 on macOS) and a "No such file or directory" message. `cd /tmp && pwd` → `/tmp` (on macOS it may show `/private/tmp`), then `pwd` → the folder you ran it from. `sleep 10` → after 2 seconds, exit `-1` and the timeout message.
- **02:** the same commands, but now `pwd` after `cd /tmp` shows `/tmp` and `echo $GREETING` shows `hi`. `sleep 10` times out after 2 seconds.
- **03:** four results. `toolu_01` → `hello` and exit 0. `toolu_02` → the first numbers (1, 2, 3...) one per line, then a `... [N characters cut] ...` line, then the last numbers up to 500. `toolu_03` → a "No such file" error and exit 1, `is_error=False`. `toolu_04` → `is_error=True` and the timeout message after 5 seconds.
- **04:** each command echoed with its output. `hello.txt` is readable, `/home` is empty, `id -u` is your user id (on Linux, not 0 unless you run as root), the ANTHROPIC count is `0`, the Python request ends with a `URLError` about name resolution, and the last line confirms the file written in the container appeared on the host.

## From simple to real

| Simplified here | Real code (per `SPEC.md`) | Why |
|---|---|---|
| Steps 1–3 run on your machine | Every model-issued command runs in the container | The model's commands are untrusted; the sandbox is the safety boundary |
| Dicts for `tool_use` / `tool_result` | Types from the `anthropic` SDK, input validated with pydantic | Catches a malformed `input` before anything runs |
| Marker read line by line | Must also handle output with no newline at the very end, binary output, and a command that prints the marker itself | Model-generated commands can print anything |
| Timeout kills the shell, then stops | Kill, return `is_error`, start a fresh session; `restart` input supported | The run must continue after a hung command |
| 200-character limit | A configurable limit; the note says how much was cut | Large enough to be useful, small enough to protect context |
| `.git` and secrets not handled | `.git/` mounted read-only on top of the workspace; host env never passed in | The agent must not commit, and must never see the API key |
| Container stopped in `finally` | Removed at the end of the run, including on error and Ctrl-C | No containers left running in the background |
| Image by name | `python:3.12-slim` pinned by digest | A name can point to different contents tomorrow; a digest can't |

## Try this

1. In `02_persistent_shell.py`, add `"false"` and `"true"` to `examples` and predict their exit codes before running.
2. In `03_tool_result.py`, change `MAX_OUTPUT_CHARS` to `50`, then to `5000`, and watch how the `toolu_02` result changes.
3. In `04_in_docker.py`, remove `"--network", "none",` and run it again. What does the last Python command print now? (Put it back afterwards.)
