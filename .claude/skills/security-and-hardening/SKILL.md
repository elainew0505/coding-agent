---
name: security-and-hardening
description: Hardens the coding agent against misuse of its own tools. Use when writing or changing the Docker sandbox, copying the repo in, the review and `git apply` step, the `run_command` handler, the agent loop's handling of tool output, secret loading, the trajectory file, or recorded test fixtures. Use when adding a dependency, auditing dependencies, or assessing supply-chain risk. Use when anything model-generated reaches a shell, a file path, or the host.
---

# Security and Hardening

## Overview

This project runs commands and edits files on behalf of a model. The model is not the adversary, but everything it reads is potentially written by one: repository files, command output, fetched pages, issue text. A single injected instruction turns the model into the attacker's hands. Security here means the *code* enforces the boundaries; the system prompt is never the control.

## When to Use

- Building or changing the Docker sandbox (image, flags, lifecycle)
- Changing what is copied into the container, or how the diff is reviewed and applied
- Writing the `run_command` handler
- Anything that turns a model-supplied string into an argv or a container setting
- Reading `ANTHROPIC_API_KEY`, writing the trajectory, or recording fixtures
- Adding or upgrading a dependency

## Process: Threat Model First

Before hardening, spend five minutes thinking like an attacker:

1. **Map the trust boundaries.** Untrusted data enters through: model output (tool inputs), tool results (command output, file contents), the workspace itself (a cloned repo can contain anything, including symlinks and planted config), and the task text. Trust follows who *wrote* a value, not which channel delivered it.
2. **Name the assets.** Your real repo and the rest of the host filesystem, the API key, the user's other credentials (`~/.ssh`, `~/.aws`, git credentials), the network, the user's money (token spend), and the workspace's own git history.
3. **Run STRIDE over each boundary:**

| Threat | Ask | Mitigation here |
|---|---|---|
| **S**poofing | Can tool output pose as the user or the system? | Tool results stay in `tool_result` blocks; never merged into user or system text |
| **T**ampering | Can the model change your real repo? | No mounts; changes leave only as a diff you review before `git apply`; no host-side shell |
| **R**epudiation | Can we reconstruct what the agent did? | The trajectory file holds every message and tool result |
| **I**nformation disclosure | Can the key or host files leak? | Key never enters the sandbox or the trajectory; no mounts; secret files never copied in (S9); no network |
| **D**enial of service | Can a command hang or flood? | Timeouts, CPU/memory/pid limits, output truncation, call cap and context backstop |
| **E**levation of privilege | Can the sandbox reach the host? | No mounts, dropped capabilities, `no-new-privileges`, no Docker socket, no privileged mode |

4. **Write abuse cases next to use cases.** For each tool, ask "what's the worst input the model could send?" and make that a test.

## The Three-Tier Boundary System

### Always Do (No Exceptions)

- **Run every model-issued command inside the sandbox**, invoked with an argv list (`["docker", "exec", ...]`), never `shell=True` on the host
- **Copy in only what `SPEC.md` S4 and S9 allow**: files git would track, never `.agent/` or deny-listed secret files
- **Show the whole diff before `git apply`**, and write nothing to the repo until the user chooses apply
- **Validate every tool input** with pydantic before acting on it
- **Bound everything**: per-command timeout, container resource limits, output truncation, call cap, context backstop
- **Return failures as `is_error: True` tool results**, with a short message, not a host stack trace. A non-zero exit code is a normal result, not a failure
- **Treat tool output as data**: it goes back to the model in a `tool_result`, never into `system`, never parsed as instructions by our code

### Ask First (Requires Human Approval)

- Giving the sandbox network access, or widening it
- Adding any mount
- Changing the container user, capabilities, or security options
- Changing what is copied in (S4, S9) or how the diff is applied
- Adding a new tool, or letting a tool act on the host
- Adding a dependency

### Never Do

- **Never pass `ANTHROPIC_API_KEY`** (or any host env) into the container
- **Never mount** the Docker socket, `$HOME`, `~/.ssh`, `~/.aws`, `~/.config`, or `/`
- **Never run the container `--privileged`**, and run it as root only with `--cap-drop ALL`, `no-new-privileges` and no mounts
- **Never log or print the key**, request headers, or full environment
- **Never commit fixtures** containing keys, auth headers, or real user data
- **Never let model output choose** container flags, mounts, images, or host paths
- **Never read config, instructions or hooks from the workspace**: a repo must not be able to change the agent's limits or behaviour

## Hardening Controls

Reference implementations (some in TypeScript; the principle carries over) live in [references/hardening-patterns.md](references/hardening-patterns.md). Open the section you need when you reach that code.

### The Docker sandbox

The container is the main boundary. Start locked down and loosen only with approval:

- No mounts at all; the repo is copied in. Root inside is acceptable only because of that, with `--cap-drop ALL` and `--security-opt no-new-privileges`
- `--network none`
- `--memory`, `--cpus`, `--pids-limit` set
- A wall-clock timeout per command that kills the process inside the container (`timeout` in the container, a host backstop, then `bash -c 'kill -KILL -1'`), not just stops waiting: killing the host-side `docker exec` leaves the command running
- An image pinned by digest, not a floating tag
- No secrets in the image, its build args, or its environment

Verify flags against the current Docker docs (`source-driven-development`) rather than from memory.

### Copy-in and apply

These are the only two places the harness touches your repo, so they carry the safety of the whole design:

- **Copy-in** lists files with `git ls-files` and drops `.agent/` and the deny list (S4, S9) before anything enters the container. Anything copied in can be read by the model, sent to the API and written to the trajectory.
- **Work from the repo's top folder** (`git rev-parse --show-toplevel`) for every host step; `git apply` reads patch paths relative to it.
- **Apply** runs `git apply --check` first, then `git apply`, only after the user chooses apply. If the check fails, save the patch to `.agent/patches/` instead of forcing it.
- The harness's git directory lives outside `/workspace` (S5), so the model can't delete or rewrite the baseline the diff is taken against.

Worked checklist: `../../references/security-checklist.md`.

### Commands

- The model's command string runs *inside* the container's shell; that's its job. What must never happen is that string reaching a host shell. Build `docker exec` as an argv list.
- Truncate stdout/stderr before returning them (keep head and tail, say how much was cut). Unbounded output is both a DoS and a way to flood the context with injected text.
- Record the exit code and include it in the result.

Pattern: [Injection](references/hardening-patterns.md#injection).

### Prompt injection through tool output

Anything a tool returns can contain "ignore previous instructions". Defenses that actually hold are in code, not in prose:

- The sandbox and the diff review above bound what an injected instruction can do.
- Tool results are only ever `tool_result` content. Never interpolate them into `system` (which also breaks prompt caching).
- Our own code never branches on instruction-like text in tool output.
- Cap turns and spend so a hijacked loop terminates.

Pattern: [LLM output handling](references/hardening-patterns.md#llm-output-handling) (LLM01, LLM05, LLM06, LLM10).

### Secrets and logging

- The `anthropic` client reads `ANTHROPIC_API_KEY` from the environment. The harness doesn't copy it anywhere else: not into the container, the trajectory or any printed output.
- The trajectory holds messages and tool results only: no headers, no env dumps, no key.
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
| "It's in Docker, so it's safe" | Only as safe as its flags. A mounted socket, `$HOME` mount, or root with capabilities left on makes the container a formality. |
| "The user reviews the diff, so anything can go in it" | Long diffs get skimmed. Keep tasks small and the diff readable; the review is the one checkpoint. |
| "It's only my own machine" | The workspace content isn't yours. Any repo you point the agent at can carry injected instructions. |
| "The fixture is just a response" | Recorders capture headers. Scrub before saving. |
| "We need network for pip install" | Bake dependencies into the image (S1). Network per command is a Phase 3 decision, not a quick fix. |

## Red Flags

- `subprocess.run(..., shell=True)` with any model-derived string on the host
- Any `-v`/`--mount` on `docker run`, especially `/var/run/docker.sock`, `$HOME`, or a credentials directory
- `os.environ` passed wholesale to `docker run`, or any `-e ANTHROPIC_API_KEY`
- Container `--privileged`, root without `--cap-drop ALL`, or network on
- Copy-in by `tar .` or `cp -r` instead of the S4 file list; a deny-listed file reaching `/workspace`
- `git apply` without `--check` first, or before the user chooses apply
- Commands with no timeout; output returned untruncated
- Tool output concatenated into `system` or into a user-role text block
- No call cap or context backstop on the loop
- Fixtures with `authorization`, `x-api-key`, or `sk-ant-` in them
- `uv pip install` in docs or scripts; no `uv.lock` committed

## Verification

After implementing security-relevant code:

- [ ] A test proves a command that sleeps forever is killed at the timeout, background processes included
- [ ] A test proves huge output is truncated without the harness holding all of it
- [ ] A test proves shell metacharacters in a command never reach a host shell
- [ ] The container has no mounts, no network, capabilities dropped, `no-new-privileges` and resource limits
- [ ] A test proves deny-listed and ignored files are absent from `/workspace`
- [ ] The key is absent from the container environment (`env` inside the sandbox doesn't show it) and from the trajectory
- [ ] `grep -rE 'sk-ant-|x-api-key|authorization' tests/fixtures` finds nothing
- [ ] The loop stops at the call cap and the context backstop
- [ ] Dependency audit is clean or every deferral is documented
