---
name: security-and-hardening
description: Hardens the coding agent against misuse of its own tools. Use when writing or changing the Docker sandbox, the bash or text-editor tool handlers, path resolution, the agent loop's handling of tool output, config and secret loading, logging, or recorded test fixtures. Use when adding a dependency, auditing dependencies, or assessing supply-chain risk. Use when anything model-generated reaches a shell, a file path, or the host.
---

# Security and Hardening

## Overview

This project runs commands and edits files on behalf of a model. The model is not the adversary, but everything it reads is potentially written by one: repository files, command output, fetched pages, issue text. A single injected instruction turns the model into the attacker's hands. Security here means the *code* enforces the boundaries; the system prompt is never the control.

## When to Use

- Building or changing the Docker sandbox (image, mounts, flags, lifecycle)
- Writing the `bash` or `text_editor` tool handlers
- Anything that turns a model-supplied string into a path, an argv, or a container setting
- Loading config, reading `ANTHROPIC_API_KEY`, logging, or recording fixtures
- Adding or upgrading a dependency

## Process: Threat Model First

Before hardening, spend five minutes thinking like an attacker:

1. **Map the trust boundaries.** Untrusted data enters through: model output (tool inputs), tool results (command output, file contents), the workspace itself (a cloned repo can contain anything, including symlinks and planted config), and the task text. Trust follows who *wrote* a value, not which channel delivered it.
2. **Name the assets.** The host filesystem outside the workspace, the API key, the user's other credentials (`~/.ssh`, `~/.aws`, git credentials), the network, the user's money (token spend), and the workspace's own git history.
3. **Run STRIDE over each boundary:**

| Threat | Ask | Mitigation here |
|---|---|---|
| **S**poofing | Can tool output pose as the user or the system? | Tool results stay in `tool_result` blocks; never merged into user or system text |
| **T**ampering | Can the model write outside the workspace? | Resolved-path checks, sandbox mounts, no host-side shell |
| **R**epudiation | Can we reconstruct what the agent did? | Log every tool call and its outcome (redacted) |
| **I**nformation disclosure | Can the key or host files leak? | Key never enters the sandbox or logs; minimal mounts |
| **D**enial of service | Can a command hang or flood? | Timeouts, CPU/memory/pid limits, output truncation, turn and token caps |
| **E**levation of privilege | Can the sandbox reach the host? | Non-root user, dropped capabilities, no Docker socket, no privileged mode |

4. **Write abuse cases next to use cases.** For each tool, ask "what's the worst input the model could send?" and make that a test.

## The Three-Tier Boundary System

### Always Do (No Exceptions)

- **Run every model-issued command inside the sandbox**, invoked with an argv list (`["docker", "exec", ...]`), never `shell=True` on the host
- **Resolve every file-tool path** (symlinks included) and reject anything not under the resolved workspace root
- **Validate every tool input** with pydantic before acting on it
- **Bound everything**: per-command timeout, container resource limits, output truncation, max turns, max tokens
- **Return failures as `is_error: True` tool results**, with a short message, not a host stack trace
- **Treat tool output as data**: it goes back to the model in a `tool_result`, never into `system`, never parsed as instructions by our code

### Ask First (Requires Human Approval)

- Giving the sandbox network access, or widening it
- Adding a mount, or mounting anything read-write besides the workspace
- Changing the container user, capabilities, or security options
- Adding a new tool, or letting a tool act on the host
- Adding a dependency

### Never Do

- **Never pass `ANTHROPIC_API_KEY`** (or any host env) into the container
- **Never mount** the Docker socket, `$HOME`, `~/.ssh`, `~/.aws`, `~/.config`, or `/`
- **Never run the container `--privileged`** or as root
- **Never log or print the key**, request headers, or full environment
- **Never commit fixtures** containing keys, auth headers, or real user data
- **Never let model output choose** container flags, mounts, images, or host paths

## Hardening Controls

Reference implementations (some in TypeScript; the principle carries over) live in [references/hardening-patterns.md](references/hardening-patterns.md). Open the section you need when you reach that code.

### The Docker sandbox

The container is the main boundary. Start locked down and loosen only with approval:

- Non-root user (`--user`), `--cap-drop ALL`, `--security-opt no-new-privileges`
- `--network none` by default
- `--memory`, `--cpus`, `--pids-limit` set; a wall-clock timeout per command, enforced by the host, which kills the process (not just stops waiting)
- Mount only the workspace, read-write; everything else read-only or absent. Prefer a read-only root filesystem with a tmpfs for scratch
- An image pinned by digest, not a floating tag
- No secrets in the image, its build args, or its environment

Verify flags against the current Docker docs (`source-driven-development`) rather than from memory.

### File tools and paths

The text-editor tool takes a model-supplied path. Before any read or write:

```python
target = (workspace / requested).resolve()
if not target.is_relative_to(workspace.resolve()):
    return error_result("path is outside the workspace")
```

- Resolve, then compare. String-prefix checks on the raw path miss `..` and symlinks.
- **Symlink race:** the model can create symlinks from inside the sandbox (via `bash`) in the mounted workspace. If file tools run on the host, a link can be swapped between check and use. Prefer running file operations inside the container too; otherwise open with no-follow semantics and re-check the opened file.
- Reject writes to `.git/` internals unless the task explicitly needs them, and cap file sizes read into context.

Patterns: [Destructive paths](references/hardening-patterns.md#destructive-operations-on-derived-paths). Worked checklist: `../../references/security-checklist.md`.

### Commands

- The model's command string runs *inside* the container's shell; that's its job. What must never happen is that string reaching a host shell. Build `docker exec` as an argv list.
- Truncate stdout/stderr before returning them (keep head and tail, say how much was cut). Unbounded output is both a DoS and a way to flood the context with injected text.
- Record the exit code and include it in the result.

Pattern: [Injection](references/hardening-patterns.md#injection).

### Prompt injection through tool output

Anything a tool returns can contain "ignore previous instructions". Defenses that actually hold are in code, not in prose:

- The sandbox and path checks above bound what an injected instruction can do.
- Tool results are only ever `tool_result` content. Never interpolate them into `system` (which also breaks prompt caching).
- Our own code never branches on instruction-like text in tool output.
- Cap turns and spend so a hijacked loop terminates.

Pattern: [LLM output handling](references/hardening-patterns.md#llm-output-handling) (LLM01, LLM05, LLM06, LLM10).

### Secrets and logging

- Load the key with `pydantic-settings` as a `SecretStr`, from the environment only. Pass it to the `anthropic` client and nowhere else.
- Redact logs: no headers, no env dumps. Log tool calls and outcomes, not the key.
- **Recorded fixtures in `tests/fixtures/` are a leak path.** Record only response bodies; scrub headers and request metadata before saving, and grep fixtures before committing.
- A key that reaches a remote is compromised the moment it lands: rotate it first, then purge history.

Pattern: [Secrets management](references/hardening-patterns.md#secrets-management).

### Dependencies and supply chain

1. **One manager, one lockfile.** `uv` with a committed `uv.lock`. Add dependencies with `uv add`, never `uv pip install` (it bypasses the lock). CI installs with `uv sync --locked`.
2. **Audit before every release** against the locked set with a known-advisory scanner (e.g. `pip-audit`; check its current invocation for uv projects). Triage critical/high by **reachability**; never auto-apply upgrades. Document every deferral with a reason and a review date.
3. **Audits only match known advisories.** Review every new dependency for typosquatting, maintenance, release age and transitive graph. Python packages can run code at install time (sdist builds); prefer wheels.

Triage decision tree: [Dependency audit triage](references/hardening-patterns.md#dependency-audit-triage).

## Review Checklist

Before sign-off, walk the AI/LLM, dependency and error-handling sections of `../../references/security-checklist.md`. Skip its web sections (cookies, CORS, headers); they don't apply to a local CLI.

## Common Rationalizations

| Rationalization | Reality |
|---|---|
| "The system prompt tells the model not to do that" | The system prompt is not a security boundary. An injected instruction in a README can override it. Enforce in code. |
| "It's in Docker, so it's safe" | Only as safe as its flags. A mounted socket, root user, or `$HOME` mount makes the container a formality. |
| "The path starts with the workspace dir, so it's inside" | `workspace/../etc` and `workspace/link -> /` both start with it. Resolve, then compare. |
| "It's only my own machine" | The workspace content isn't yours. Any repo you point the agent at can carry injected instructions. |
| "The fixture is just a response" | Recorders capture headers. Scrub before saving. |
| "We need network for pip install" | Then grant it per task with approval, not by default. |

## Red Flags

- `subprocess.run(..., shell=True)` with any model-derived string on the host
- A path check done on the raw string, or before resolving symlinks
- `os.environ` passed wholesale to `docker run`, or any `-e ANTHROPIC_API_KEY`
- A mount of `/var/run/docker.sock`, `$HOME`, or a credentials directory
- Container running as root, `--privileged`, or with network on by default
- Commands with no timeout; output returned untruncated
- Tool output concatenated into `system` or into a user-role text block
- No max-turns or token budget on the loop
- Fixtures with `authorization`, `x-api-key`, or `sk-ant-` in them
- `uv pip install` in docs or scripts; no `uv.lock` committed

## Verification

After implementing security-relevant code:

- [ ] Tests cover `..`, absolute paths, and symlink escapes for every file tool, and they are rejected with `is_error: True`
- [ ] A test proves a command that sleeps forever is killed at the timeout
- [ ] A test proves huge output is truncated
- [ ] The container runs non-root, no network, capabilities dropped, resource-limited, with only the workspace mounted
- [ ] The key is absent from the container environment (`env` inside the sandbox doesn't show it) and from logs
- [ ] `grep -rE 'sk-ant-|x-api-key|authorization' tests/fixtures` finds nothing
- [ ] The loop stops at max turns / token budget
- [ ] Dependency audit is clean or every deferral is documented
