# 0001: Drive Docker through the `docker` CLI, not the Python SDK

Status: accepted, 2026-10-29

## Context

Every model-issued command runs in a Docker container (SPEC.md § Sandbox). The agent needs to start one container per run with specific security flags, run commands in it, and always remove it. Two ways to do that from Python: the Docker SDK for Python (`docker` on PyPI), or calling the `docker` command-line tool with `subprocess`.

## Options

- **Docker SDK for Python.** Typed-ish API, no subprocess handling. A new dependency with its own release cycle; flags are spelled differently from the CLI (`cap_drop=["ALL"]`, `security_opt=[...]`), so what the code does is harder to compare with Docker's documentation or reproduce by hand.
- **`docker` CLI via `subprocess`, argv lists only.** No new dependency. The exact command is a list of strings that a unit test can assert on and a person can paste into a terminal. Needs Docker's CLI installed, which Docker Desktop provides.

## Decision

Use the `docker` CLI through `subprocess.run` with argv lists, never `shell=True`. The full `docker run` command is built by one function, `docker_run_args`, which has a unit test for every security flag.

## Consequences

- The sandbox's security settings are reviewable in one place and testable without Docker.
- Debugging a run means copying the printed command, not reading SDK docs.
- We parse CLI output ourselves where needed; so far only exit codes and combined stdout/stderr.
- If we ever need streaming container events or many concurrent containers, revisit the SDK.
