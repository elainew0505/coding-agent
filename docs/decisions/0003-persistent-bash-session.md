# 0003: A persistent bash session over one `docker exec -i` process

Status: accepted, 2026-10-29

## Context

The bash tool (`bash_20250124`) is a persistent session: `cd`, exported variables and shell functions carry across calls (SPEC.md § Tools). Each command needs a timeout, combined stdout/stderr, an exit code, and bounded output, and a stuck command must not take the run down with it.

## Options

- **One `docker exec` per command, replaying state.** Simple and stateless, but persistence would have to be faked (track `cd`, re-export variables), which breaks on functions, `source`, `set -o` and anything we didn't anticipate.
- **A pseudo-terminal (`docker exec -it`) driven like expect.** Real interactive shell, but prompts, echo, line editing and terminal escape codes all leak into the output and must be stripped.
- **One `docker exec -i ... bash` process per run, fed commands on stdin, with an end-of-command marker.** Real persistence, no terminal noise. We have to find where each command's output ends.

## Decision

Use one non-interactive `bash --noprofile --norc` per run (`Sandbox.bash`, `sandbox._Shell`). Each command is written as

```
eval '<command, shell-quoted>' < /dev/null
printf '\n%s %d\n' <random token> $?
```

- `eval` of a single quoted word means the command can't break out of the wrapper, and a syntax error (an unclosed quote) is an ordinary exit code `2` instead of a shell waiting forever for more input.
- `< /dev/null` stops a command that reads stdin (`cat`, `read`, an interactive prompt) from swallowing the marker line; it gets EOF instead.
- The marker carries a fresh random token per command, so output can't fake it, and the exit code.
- Output is read with `select` against a deadline. Only the first and last 1 MB are kept in memory (counted as `CommandResult.dropped`), so `yes` can't exhaust memory; the tool then truncates to 30,000 characters, keeping head and tail with a note of how much was cut.
- **On timeout**, and on `restart`, we run `kill -9 -1` in the container: every process except PID 1 (`sleep infinity`) dies, including background jobs, and the next call starts a fresh shell. The tool result is an error saying the session was reset.
- If a command exits the shell (`exit 3`), the result is an error and a fresh shell is started.
- A non-zero exit code is not a tool error: the model gets the output and `[exit code: N]` and decides what to do.

## Consequences

- Persistence is exactly bash's own; nothing is emulated.
- A timeout costs the model its shell state (directory, variables, background servers). The error message says so, so it can redo the setup.
- Output a background job prints after its command's marker is not discarded: it stays in the pipe and appears at the start of the next command's output.
- The dropped-bytes count is reported as characters; they differ only for non-ASCII output larger than 2 MB.
