# coding-agent

A minimal coding agent, modelled on [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent). You give it one task. Claude works on a **copy** of your repo inside a locked-down Docker container, using one tool: a bash command. When it's done you see the diff, and nothing touches your repo unless you choose to apply it.

What it is and isn't is in [SPEC.md](SPEC.md); why it's built this way is in [docs/decisions/](docs/decisions/).

## Install

You need Docker Desktop (running), [uv](https://docs.astral.sh/uv/), git, and an Anthropic API key.

```bash
git clone <this repo> ~/code/coding-agent
cd ~/code/coding-agent
uv sync

# The agent image. This step needs the network; the agent itself never has it.
docker build -t coding-agent:toy .

export ANTHROPIC_API_KEY=sk-ant-...   # read from the environment only
```

To type `coding-agent` in any folder, install it as a command (it runs the code in this folder, so edits take effect without reinstalling):

```bash
uv tool install --editable .          # undo with: uv tool uninstall coding-agent
```

Or skip that and run it through uv each time: `uv run --project ~/code/coding-agent coding-agent "<task>"`.

Add `.agent/` to the `.gitignore` of any repo you use it on. That's where its trajectories and saved patches go.

## Run

From anywhere inside a git repo:

```bash
coding-agent "Some tests fail. Find the bug and fix it."
coding-agent --model claude-opus-5-5 "Refactor the parser and keep the tests passing."
```

- **Model:** Claude Haiku 5.5 (`claude-haiku-5-5`) by default, the cheapest. Pass `--model claude-opus-5-5` for the strongest; it's about 40 times the price, and it retries a safety-classifier refusal on a fallback model, which Haiku can't.
- **While it works** you see each command as `$ <command>` with a few lines of output, and Claude's messages.
- **At the end** you see a summary of the changed files, then the full diff, then `[a]pply / [d]iscard?`. `a` applies; `d`, Ctrl+C or Ctrl+D discards; anything else asks again. Control characters in the diff (which could hide lines on screen) are shown as escapes like `\x1b`. If the diff no longer applies (you changed the same lines meanwhile), the patch is saved to `.agent/patches/` instead.
- **Ctrl+C** during the task stops it and still takes you to the review, so partial work isn't lost.
- **Limits:** 50 model calls, a 60-second default per command (300 at most), and a context backstop. All settings are in [`src/coding_agent/config.py`](src/coding_agent/config.py).
- **Every run** is saved as JSON in `.agent/trajectories/`: the task, every message and command, token usage and the outcome.

This MVP's image only contains what the toy repo's (`~/code/toy-repo`) tests need (pytest). On other repos, Claude can read and edit code, but test commands that need other packages will fail.

### Try the sandbox for free

No API key, no cost: a scripted demo copies the current repo into the sandbox, runs its tests, makes a change, and asks you to apply or discard it.

```bash
cd ~/code/toy-repo
uv run --project ~/code/coding-agent python -m coding_agent.sandbox
```

## Security model, in plain terms

The container is the safety boundary; there are no per-command approval prompts ([0001](docs/decisions/0001-sandbox-instead-of-approvals.md), [0002](docs/decisions/0002-copy-in-diff-out.md)).

- **It works on a copy.** Your repo is copied in at the start. No folder of your Mac is mounted, so nothing Claude runs can touch your files, even `rm -rf /`.
- **What's copied:** only files git would track. Ignored files are not copied, and neither are `.agent/` or anything named like a secret (`.env`, `.env.*`, `*.pem`, `*.key`, SSH keys), even if git tracks it. Anything that *is* copied can be read by Claude and so is sent to the API and saved in the trajectory.
- **No network** inside the container, no Docker socket, no API key (it stays in the host process), and limits on memory, CPU and processes. The container is removed at the end, even after an error or Ctrl+C.
- **The only way changes reach your repo is the diff you approve.** That makes the review your one checkpoint: read the whole diff before applying, including binary files and new symlinks (`new file mode 120000`), and be wary of changes to files that run automatically, like `.envrc`, `Makefile`, `package.json` scripts, `.vscode/` or CI workflows.
- **Nothing in your repo configures the agent.** A repo you point it at can't change its limits or behaviour.
- **Disk:** the container has no disk limit, so keep Docker Desktop's disk size (Settings → Resources) below your free space.

## Develop

```bash
uv run pytest                   # unit + Docker tests; no network, no cost
uv run ruff check . && uv run ruff format .
uv run pyright
```

Docker tests are skipped (with the reason) if Docker isn't running or the image isn't built. Every test file can also be run by hand, e.g. `uv run python tests/test_sandbox.py network ctrl` (only tests whose names match) or `--list`.

**Live tests call the real API and cost money** (a few cents on Haiku). Only run them yourself, never through an AI agent:

```bash
uv run pytest --live tests/test_live.py -v
```

They clone `~/code/toy-repo` (or `$TOY_REPO`) into a temp folder, so the toy repo itself is never changed. The first live run records one real reply to `tests/fixtures/reply.json`; commit it, and a normal `uv run pytest` then checks that our fake replies still match the real SDK's shape.
