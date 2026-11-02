"""
agent.py - the agent loop, the review, and the trajectory.

  run_task          the loop (SPEC.md § Agent loop, L2-L9). One task per run: send
                    the history to the model, run the tool calls it asks for,
                    append the results, repeat until it's done or a limit is hit.
  review            show the diff and apply or discard it (D2-D5)
  write_trajectory  save the whole run as JSON (§ Trajectory)
"""

import json
from dataclasses import dataclass, field
from datetime import datetime

import anthropic

from coding_agent.config import CONTEXT_THRESHOLD, MAX_CALLS, MODEL
from coding_agent.prompts import SYSTEM_PROMPT
from coding_agent.sandbox import apply_patch
from coding_agent.tools import TOOL, ToolResult, run_command

PREVIEW_LINES = 5  # lines of each command's output shown to the user (§ Output)

# Control characters (except newline and tab) and bidi overrides, shown as
# escapes. Printed raw, they can move the cursor, erase lines or reorder text,
# so a file the model wrote could hide part of the diff you're approving.
_C0_C1 = [*range(0x09), *range(0x0B, 0x20), *range(0x7F, 0xA0)]
_BIDI = [*range(0x202A, 0x202F), *range(0x2066, 0x206A)]
_HIDDEN = {c: f"\\x{c:02x}" for c in _C0_C1} | {c: f"\\u{c:04x}" for c in _BIDI}


def visible(text):
    """`text` safe to print to a terminal: control characters become escapes."""
    return text.translate(_HIDDEN)


@dataclass
class Run:
    messages: list
    stop_reason: str = ""  # "done", "max_tokens", "refusal", "call_cap", "context_full", ...
    detail: str = ""  # a human-readable line about why it stopped, if not "done"
    calls: int = 0
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})

    @classmethod
    def for_task(cls, task):
        return cls(messages=[{"role": "user", "content": task}])


def input_tokens(usage):
    """Everything the model read this call. With caching on, input_tokens
    alone leaves out the cached part (L5)."""
    return (
        usage.input_tokens
        + (usage.cache_read_input_tokens or 0)
        + (usage.cache_creation_input_tokens or 0)
    )


def run_task(run, model, sandbox, out=print):
    """Work on the task in `run` (from Run.for_task) until the model stops or
    a limit is hit. Returns the same Run, which is updated as it goes, so the
    caller still has the history if something unexpected crashes.

    Never raises for API errors or Ctrl+C: both end the task, and the caller
    goes to review either way (D6).
    """
    while run.calls < MAX_CALLS:  # L4
        try:
            reply = model.query(run.messages)
        except KeyboardInterrupt:  # L8: a reply still streaming is dropped
            return _stop(run, "interrupted", "Stopped by Ctrl+C.")
        except anthropic.APIError as e:  # L6: after the SDK's own retries
            return _stop(run, "api_error", f"API error: {e}")

        run.calls += 1
        run.usage["input_tokens"] += input_tokens(reply.usage)
        run.usage["output_tokens"] += reply.usage.output_tokens
        run.messages.append({"role": "assistant", "content": reply.content})  # L2: all of it

        for block in reply.content:
            if block.type == "text" and block.text.strip():
                out(visible(block.text))

        if reply.stop_reason == "end_turn":  # L3
            return _stop(run, "done")
        if reply.stop_reason != "tool_use":  # L3: max_tokens, refusal, ...; may be cut off
            return _stop(run, reply.stop_reason, f"Claude stopped early: {reply.stop_reason}.")

        try:
            results = [
                _run_tool(block, sandbox, out)
                for block in reply.content
                if block.type == "tool_use"
            ]
        except KeyboardInterrupt:  # L8: the sandbox already killed the command
            return _stop(run, "interrupted", "Stopped by Ctrl+C.")
        run.messages.append({"role": "user", "content": results})  # L2: one message

        if input_tokens(reply.usage) > CONTEXT_THRESHOLD:  # L5
            return _stop(run, "context_full", "Context is full.")

    return _stop(run, "call_cap", f"Hit the cap of {MAX_CALLS} model calls.")


def _run_tool(block, sandbox, out):
    """Run one tool_use block and turn the outcome into a tool_result block."""
    if block.name == "run_command":
        command = block.input.get("command", "") if isinstance(block.input, dict) else ""
        out(visible(f"$ {command}"))
        result = run_command(block.input, sandbox)
    else:
        result = ToolResult(f"Unknown tool: {block.name}", is_error=True)
    lines = result.content.splitlines()
    if lines and lines[0].startswith("exit_code:"):
        lines = lines[1:]
    if lines[:PREVIEW_LINES]:
        out(visible("\n".join(lines[:PREVIEW_LINES])))
    return {
        "type": "tool_result",
        "tool_use_id": block.id,
        "content": result.content,
        "is_error": result.is_error,
    }


def _stop(run, reason, detail=""):
    run.stop_reason = reason
    run.detail = detail
    return run


# --- review and apply (D2-D5) ------------------------------------------------


def review(diff, root, ask=None, out=print):
    """Show the changes and apply them only if the user says so.

    Returns "no changes", "applied", "discarded" or "saved" (apply failed).
    Nothing is written to the repo before the answer (D4).
    """
    ask = ask or input
    if diff.empty:  # D2
        out("No file changes.")
        return "no changes"
    out(visible(diff.stat))  # D3: summary first, then the full diff
    out(visible(diff.patch.decode(errors="replace")))
    while True:
        try:
            answer = ask("[a]pply / [d]iscard? ").strip().lower()
        except (KeyboardInterrupt, EOFError):  # D4: Ctrl+C or Ctrl+D means discard
            answer = "d"
        if answer in ("a", "apply", "d", "discard"):
            break
        out("Type a to apply or d to discard.")  # D4: a typo must not lose the work
    if answer in ("d", "discard"):
        out("Discarded. Your repo is unchanged.")
        return "discarded"
    ok, message = apply_patch(root, diff.patch)  # D5: checks first, then applies
    if ok:
        out("Applied to your repo.")
        return "applied"
    path = _agent_file(root, "patches", "patch")
    path.write_bytes(diff.patch)  # D5: keep the work when it doesn't apply
    out(f"Could not apply: {message}\nSaved the patch to {path}")
    return "saved"


# --- trajectory (§ Trajectory) -----------------------------------------------


def write_trajectory(root, task, run, outcome, model=MODEL):
    """Save the run to .agent/trajectories/<timestamp>.json and return the path.

    Holds the request settings, every message and the result. Never the API
    key: it lives only inside the SDK client, which is not saved.
    """
    run = run or Run(messages=[], stop_reason="not started")
    path = _agent_file(root, "trajectories", "json")
    data = {
        "task": task,
        "model": model,
        "system": SYSTEM_PROMPT,
        "tools": [TOOL],
        "messages": run.messages,
        "stop_reason": run.stop_reason or "crashed",  # run_task always sets one when it returns
        "detail": run.detail,
        "calls": run.calls,
        "usage": run.usage,
        "outcome": outcome,
    }
    path.write_text(json.dumps(data, indent=2, default=_jsonable))
    return path


def _jsonable(value):
    """SDK reply blocks are pydantic models; store them as plain JSON."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    raise TypeError(f"can't save {type(value).__name__} in a trajectory")


def _agent_file(root, folder, suffix):
    """A new timestamped file under <repo>/.agent/<folder>/."""
    folder_path = root / ".agent" / folder
    folder_path.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return folder_path / f"{stamp}.{suffix}"
