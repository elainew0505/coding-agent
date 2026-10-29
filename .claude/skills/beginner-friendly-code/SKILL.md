---
name: beginner-friendly-code
description: Writes a separate, simplified learning version of existing project code in learn/, with plain-language explanations. Only run when the user invokes it with /beginner-friendly-code.
disable-model-invocation: true
argument-hint: "[file, function, or concept to explain]"
---

# Beginner-Friendly Code

The user wants to understand: $ARGUMENTS

## Background and audience

This repo is a coding agent: a CLI that calls Claude in a tool-use loop and runs bash commands and file edits inside a Docker sandbox (see `SPEC.md`). The owner is building it to learn, and uses this skill to fill specific knowledge gaps.

**Audience level: between a data scientist and a software developer.**

- **Already knows — don't explain:** Python syntax, functions, classes, dicts/lists, comprehensions, imports, virtual environments, pip/`uv` basics, running scripts and notebooks, calling an HTTP/JSON API, pandas-style data work, basic git (commit, branch, push).
- **Likely gaps — explain these well:** how the OS runs processes (subprocess, stdin/stdout/stderr, exit codes, signals, killing a process), bash beyond `cd`/`ls` (pipes, redirection, `$?`, quoting), Docker (images vs containers, mounts, users, capabilities, network isolation), concurrency (threads, queues, `async`/`await`), timeouts, security thinking (untrusted input, path escapes, secrets), testing with fakes/mocks, typing and pydantic, and the Anthropic tool-use protocol.

Pitch explanations at that level: skip the basics, go straight to the idea that's new, and explain the "why" an experienced developer would take for granted.

## What this skill does — and doesn't

The project code is written to normal industry standards and **stays that way**. This skill never edits it. Instead it writes a separate, simplified version in `learn/` that teaches the same idea, so the user can study it, run it, and then go back and read the real code with understanding.

If `$ARGUMENTS` is empty, ask which file, function or concept they want explained.

## Steps

1. **Read the real code** the user named (or the code that uses the concept they named). If it isn't written yet, work from its section in `SPEC.md` and say so in the README. Identify the ideas from the "likely gaps" list above that it depends on.
2. **Create a folder** `learn/<short-topic-name>/` (e.g. `learn/sandbox-run-command/`). Don't overwrite an existing one; add a suffix instead.
3. **Write a simplified script** there (e.g. `simple.py`, or `simple.sh` for bash) that does the same core job in the plainest way. Rules below.
4. **Write `README.md`** in the same folder explaining it. Format below.
5. **Don't run or test the scripts** unless the user asks you to. Writing them is the job; the user runs them to learn. Never start services (e.g. the Docker daemon), pull images, or install anything for this skill without being asked. In the README's "Run it" section, describe the expected output rather than pasting output from a run.
6. **Reply in chat** with a short summary and the path to the folder.

## Writing the simplified script

- One file, top to bottom, no imports from the project's `src/`. It must run on its own.
- Plain, ordinary Python: no clever tricks or dense one-liners, but normal idioms the audience knows (comprehensions, f-strings, `with`) are fine.
- Full variable names (`response`, not `r`). One step per line where the step is the new idea.
- Comment the lines that carry a new concept, saying what they do and why. Don't comment plain Python.
- Hard-coded example values instead of config or CLI parsing, so it's obvious what goes in.
- Leave out what isn't the core idea (retries, logging, extra edge cases) — but say in the README what was left out and why the real code has it.
- Keep it short: ideally under ~60 lines. Split into several numbered scripts (`01_...py`, `02_...py`) if one idea builds on another.
- Safe to run: no real API calls (fake the response with a hard-coded dict), never print secrets, don't touch files outside `learn/`. Only hard-coded example commands may run on the host; anything standing in for model-issued commands goes in Docker. Scripts that need Docker must say so at the top.

## README.md format

```markdown
# <Topic>

**Real code:** `path/to/file.py` → `function_name`
**In one sentence:** what this does and why the project needs it.

## New concepts
- **<term>** — short definition, plus an analogy where it helps.
  Only terms from the "likely gaps" list; don't define things the audience already knows.

## Walkthrough
Go through simple.py in order: quote a line or block, then explain it.
Use concrete values ("if path is ../secret.txt, this check rejects it because ...").

## Shell commands
For every command: break it into parts, explain each flag, and say
whether it only reads or changes something / costs money.

## Run it
The exact command and the output to expect.

## From simple to real
How the real code differs (extra checks, types, async, error handling)
and why each difference matters. Point at the lines in the real file.

## Try this
One or two small changes the user can make to test their understanding.
```

## Rules

- Never edit files outside `learn/`.
- Never present the simplified version as a replacement for the real code, and never import from `learn/` in project code or tests.
- Safety checks in the real code are explained, not hand-waved: if the simple version skips one, the "From simple to real" section says what it protects against.
