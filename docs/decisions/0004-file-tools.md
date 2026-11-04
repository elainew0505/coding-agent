# 0004: Add read, write and edit tools next to run_command

Status: proposed, 2026-11-03. Changes the "bash is the only tool" choice in `SPEC.md`'s introduction, modelled on mini-swe-agent; `run_command` itself is unchanged.

## Context

The MVP gives Claude one tool, `run_command`, and the tool description tells it to edit files with heredocs, `sed -i` or a short Python script. That keeps the harness tiny, but every edit depends on the model getting shell quoting right, and a wrong `sed` expression can silently change the wrong lines. Reading a file with `cat` also returns it without line numbers, capped at the head and tail, so the middle of a long file is cut out.

`SPEC-Phase1.md` had deferred a structured `edit_file` tool until the eval showed bash edits failing. The user chose to add the tools now, behind a switch, so the eval measures them instead of being their precondition.

## Options

1. **Keep bash only, with better tips in the tool description.** No new code; the model still has to quote every edit.
2. **Anthropic's text editor tool** (`str_replace_based_edit_tool`): view, create, str_replace, insert, a contract Claude is trained on. Its schema is Anthropic's, not ours.
3. **Custom tools, like `run_command`:** `read_file`, `write_file` and `edit_file` (exact-string replace), each with a pydantic model and a hand-written schema.
4. **Helper scripts in the image** (SWE-agent style), called through `run_command`. They'd have to be copied into every SWE-bench image too.

## Decision

Option 3, as specified in `SPEC.md` § File tools (T1–T6):

- On by default; `--bash-only` sends `run_command` alone with today's texts, and the tool set is recorded, so `eval-compare` can compare them.
- `edit_file` replaces an exact string that must appear once (or with `replace_all`), the most common shape in coding agents and robust to lines shifting.
- The read and replace logic runs in host memory: the harness reads bytes with `docker exec … head -c`, and writes them back on `docker exec -i` stdin. Only `head`, `cat` and `sh` are needed in the container, so the tools work the same in the agent image and every SWE-bench image, and the logic is unit-testable without Docker.

## Consequences

- The model gets three more tools to choose from, and the system prompt and `run_command` description now differ between tool sets. Each set is still fixed for a run, so prompt caching keeps hitting.
- New attack surface: model-chosen paths and file contents. They're always separate arguments or stdin bytes, never shell text, with tests that try `"; touch /tmp/pwned"`, `$(…)` and backticks. Sizes are capped (1,000,000 bytes per file, 10,000 characters of output) so the harness's memory stays bounded.
- File bytes pass through the host process's memory, but never your disk: the no-mounts rule from [0002](0002-copy-in-diff-out.md) still holds.
- The eval measures the agent with the tools, and `--bash-only` runs show whether they help. If they don't, the switch makes removing them easy.
- Searching stays with `run_command` (`grep`, `find`): it already has the timeout, output cap and kill-on-stop, and dedicated search tools can be added later if the eval shows a need.
- More code in `tools.py` to maintain: three input models, schemas and handlers.
