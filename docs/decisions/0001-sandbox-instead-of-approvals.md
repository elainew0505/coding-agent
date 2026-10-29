# 0001: The sandbox, not per-command approval, is the safety boundary

Status: accepted, 2026-10-29

## Context

The agent runs model-issued shell commands and file edits in a user's git repository. Something has to stop those actions from doing damage. The two common answers are to ask the user before each action, or to confine every action so that approving it doesn't matter much.

Evidence on the first option, from the analysis of Claude Code v2.1.88 in Liu et al., *Dive into Claude Code: The Design Space of Today's and Future AI Agent Systems* (arXiv:2604.14228, § 5 and § 11.3):

- Users approved about 93% of permission prompts, so the paper treats interactive confirmation as unreliable when it is the only safeguard ("approval fatigue").
- Auto-approve rates grow with use, from about 20% under 50 sessions to over 40% by 750 sessions.
- Sandboxing reduced permission prompts by an estimated 84%. Claude Code's response to unreliable approval was to cut the number of decisions people must make.
- Two Claude Code CVEs (CVE-2025-59536, CVE-2026-21852) came from project-supplied hooks, MCP servers and settings taking effect before the user accepted the trust dialog: configuration from the repository acted before any safety check applied.

SWE-Agent and OpenHands confine the agent's whole execution environment in a container instead of judging each action.

## Options

1. **Per-command approval** on the host, with allow and deny rules. Fine-grained, but it relies on the attention the evidence above says users don't sustain, and needs a rule engine and command parser that are hard to get right.
2. **Container sandbox, no prompts.** Every action runs in a locked-down container that can only reach the workspace. Coarse, but enforced by the kernel and testable.
3. **Both.** The strongest, but it brings back the prompts and the rule engine for a single-user local tool.

## Decision

Option 2. Every model-issued command and file operation runs in the Docker sandbox described in `SPEC.md` § Sandbox, with no per-command prompts. Network is off unless `--allow-network` is passed for that run. `.git/` is read-only, so the agent can't commit and its work stays reviewable as an uncommitted diff.

Following the pre-trust lesson, nothing that controls the agent is read from the workspace: no config, no `CLAUDE.md`, no hooks (`SPEC.md` § Configuration).

## Consequences

- The sandbox flags and mounts are the security boundary, so changing any of them is "ask first" in the spec and each is covered by a `docker` test.
- Inside the workspace the agent can do anything bash can, including deleting files. Git is the recovery path; the CLI warns when the tree is already dirty.
- Host code that touches agent-written files must not run anything those files select. Today that is host-side git, hardened as in `SPEC.md` § Host-side git.
- Grants don't outlive a run. If session resume is added later, a resumed run must not inherit an earlier run's `--allow-network`; the user passes it again.
