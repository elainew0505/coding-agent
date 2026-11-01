# 0002: Copy the repo into the container and bring changes out as a reviewed diff

Status: accepted, 2026-11-01. Supersedes the mount design in [0001](0001-sandbox-instead-of-approvals.md); 0001's core decision (the sandbox, not per-command approval, is the safety boundary) still holds.

## Context

0001 put the sandbox at the centre of safety and specified it as a bind mount: the workspace mounted read-write at `/workspace`, its `.git/` read-only on top, the container running as the host user's uid. That design left several hard problems:

- The agent edited your real files directly, so a bad run was undone with git, and only if the tree had been clean.
- Host code had to run `git status` and `git diff` over files the agent wrote, and git config or an agent-written `.gitattributes` can make those run programs (`core.fsmonitor`, `diff.external`, textconv). Each needed a flag, and one residual (user-defined filters) stayed open.
- A read-only `.git` overlay and uid mapping are details that break across Docker setups.

The MVP also changed shape: modelled on [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent), one task per run.

## Options

1. **Bind mount (0001 as written).** Changes are live in your repo; no copy step. Keeps the problems above.
2. **Copy in, diff out.** Stream the repo into a container with no mounts; the harness tracks changes in its own git directory there and hands you a diff to apply or discard.
3. **Copy in, copy back.** Like 2, but sync the whole tree back at the end. No review step, and deletions and partial work are hard to get right.

## Decision

Option 2, as specified in `SPEC.md` § How a run works, § Sandbox and § Review and apply:

- Only files git would track are copied in, minus `.agent/` and a deny list of secret files (S4, S9).
- The harness's git directory is `/harness.git`, outside the workspace, so the model can't delete or rewrite the baseline (S5).
- Nothing is written to your repo until you choose apply after seeing the diff (D4). If `git apply --check` fails, the patch is saved instead (D5). The container is removed at the end of every run (S6).

## Consequences

- Your repo is never touched during a task; the review step is the one checkpoint, so read the whole diff, including `Binary files differ` and mode lines such as `new file mode 120000` (a symlink).
- Host-side git runs only on your own repo: `git ls-files` before copy-in and `git apply` on a patch you've reviewed. The `fsmonitor`/`diff.external` hardening from the old spec is no longer needed.
- No mounts means root in the container can't create files on the host, so the uid mapping is dropped; capabilities are still dropped (S2).
- The model can't see your git history (`.git` isn't copied) or ignored files such as a local `.env`. Tasks that need either are out of scope for the MVP.
- No network plus no mounts means dependencies must be baked into the image (S1); the MVP only runs tests for the toy repo.
- A copy step per run; fine for small repos, may need revisiting for large ones.
