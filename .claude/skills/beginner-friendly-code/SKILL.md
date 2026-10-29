---
name: beginner-friendly-code
description: Writes simple, readable code and explains it in plain language for a learner. Use whenever writing or changing code, shell commands, Docker setup, or config in this repo, and whenever the user asks "what does this do", "explain", or "I don't understand". The repo owner is still learning bash, Docker, async Python and the Anthropic API, so default to this skill for all code work.
---

# Beginner-Friendly Code

## Overview

The person who owns this repo is learning. Code they can't read is code they can't review, debug or trust. So the goal is not the shortest or cleverest code: it is code a beginner can follow line by line, plus a plain-language explanation of anything new.

Simple never means unsafe. The safety rules in `CLAUDE.md` (Docker sandbox, workspace path checks, never logging the API key) still apply in full. If a safety check makes code longer, keep the check and explain why it is there.

## When to Use

- Writing any new code, test, shell command, Dockerfile or config file
- Changing existing code
- The user asks what something means or does
- Introducing a concept, library or tool for the first time in the conversation

## Writing the Code

**Prefer the plain version.**

| Instead of | Write |
|---|---|
| A nested comprehension or long one-liner | A normal `for` loop with clear steps |
| Chained calls `a(b(c(x)))` | One step per line, each stored in a well-named variable |
| Short names `p`, `r`, `cfg` | Full names `path`, `response`, `config` |
| Clever tricks (walrus `:=`, `lambda` chains, `functools.reduce`) | Ordinary `if`/`for`/function code |
| Several new ideas in one function | Small functions that each do one thing |
| Piped bash one-liners `a \| b \| c` | Separate commands, or Python, with a comment per step |

**Keep functions short** — aim for something that fits on one screen. Give each a one-line docstring saying what it does in plain words.

**Use type hints** (the project requires them for `pyright`), and when one is unusual, say what it means in a comment, e.g. `# str | None means "a string, or nothing"`.

**Comment the why, and the what when it's new.** Normally comments only explain why. For this user, also add a short "what" comment the first time a concept appears in a file (e.g. the first `async def`, the first `subprocess` call, the first `with` block). Don't repeat it on every later use.

**Add one piece at a time.** Prefer several small changes the user can follow over one large change. Follow `incremental-implementation`.

## Explaining the Code

After writing or changing code, explain it in the chat reply (not in a separate file):

1. **One-sentence summary** — what this code does and why we need it.
2. **Walkthrough** — go through the code in order, in plain language. Quote the line or block, then say what it does.
3. **New concepts** — for each term the user may not know (e.g. `bash`, `stdin`, exit code, container, `async`/`await`, context manager, environment variable, JSON, API), give a short definition and an everyday analogy where it helps.
4. **How to try it** — the exact command to run, and what the user should expect to see.

Keep explanations short and concrete. Use examples with real values ("if `path` is `../secret.txt`, this check rejects it because ...") rather than abstract descriptions.

### Shell commands get extra care

The user is new to bash. For every command you ask them to run, or that the code runs:

- Break it into parts and say what each part does (e.g. `uv run pytest -m live` → `uv run` = run inside this project's Python environment; `pytest` = the test runner; `-m live` = only tests marked "live").
- Say whether it changes anything (installs, deletes, costs money by calling the API) or only reads.
- Explain flags rather than assuming they are known.

### Explaining existing code

When the user asks about code that already exists, explain it the same way: summary, walkthrough, new concepts. Don't rewrite it unless they ask.

## Checking Understanding

At the end of a larger change, offer — in one line — to go deeper on any part, or suggest one small thing the user could change themselves to test their understanding (e.g. "try changing the timeout to 5 and run the test again"). Don't quiz them unprompted.

## Red Flags

- A line that needs a comment to decode *what* it does, when a plainer version exists
- A concept used without being explained the first time
- A bash command given without saying what it does
- "Simplifying" by removing a safety check, a test, or error handling
- A large change delivered at once with no walkthrough

## Verification

Before finishing:

- [ ] Could a beginner read each function top to bottom without jumping around?
- [ ] Every new concept and every shell command is explained in plain words
- [ ] The explanation includes how to run or test it
- [ ] Safety rules and tests are intact; `ruff` and `pyright` still pass
