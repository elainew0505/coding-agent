"""
agent.py - the agent loop (SPEC.md § Agent loop, L2-L9).

One task per run: send the history to the model, run the tool calls it asks
for, append the results, repeat until it's done or a limit is hit. The
history is a plain list that only grows; it is saved as the trajectory.
"""

from dataclasses import dataclass, field

import anthropic

from coding_agent.config import CONTEXT_THRESHOLD, MAX_CALLS
from coding_agent.tools import ToolResult, run_command

PREVIEW_LINES = 5  # lines of each command's output shown to the user (§ Output)


@dataclass
class Run:
    messages: list
    stop_reason: str = ""  # "done", "max_tokens", "refusal", "call_cap", "context_full", ...
    detail: str = ""  # a human-readable line about why it stopped, if not "done"
    calls: int = 0
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})


def input_tokens(usage):
    """Everything the model read this call. With caching on, input_tokens
    alone leaves out the cached part (L5)."""
    return (
        usage.input_tokens
        + (usage.cache_read_input_tokens or 0)
        + (usage.cache_creation_input_tokens or 0)
    )


def run_task(task, model, sandbox, out=print):
    """Work on `task` until the model stops or a limit is hit. Returns a Run.

    Never raises for API errors or Ctrl+C: both end the task, and the caller
    goes to review either way (D6).
    """
    run = Run(messages=[{"role": "user", "content": task}])

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
                out(block.text)

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
        out(f"$ {command}")
        result = run_command(block.input, sandbox)
    else:
        result = ToolResult(f"Unknown tool: {block.name}", is_error=True)
    lines = result.content.splitlines()
    if lines and lines[0].startswith("exit_code:"):
        lines = lines[1:]
    if lines[:PREVIEW_LINES]:
        out("\n".join(lines[:PREVIEW_LINES]))
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
