"""The agent loop: send the conversation, run requested tools, repeat until done.

The model client is injected as a `stream` callable (in production,
`anthropic.Anthropic().beta.messages.stream`) so tests can replay responses.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from anthropic.types.beta import BetaMessage, BetaToolUseBlock

from coding_agent.prompts import SYSTEM_PROMPT
from coding_agent.tools import ToolHandler, ToolResult

MAX_TOKENS_PER_RESPONSE = 64_000

# Server-side fallback: if a request is declined by a safety classifier, the API
# retries it on a fallback model chosen by refusal category.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class FinalMessageSource(Protocol):
    def get_final_message(self) -> BetaMessage: ...


StreamFn = Callable[..., AbstractContextManager[FinalMessageSource]]


class StopReason(StrEnum):
    DONE = "done"
    TURN_LIMIT = "turn limit"
    TOKEN_LIMIT = "token limit"
    MAX_TOKENS = "response hit max_tokens"
    CONTEXT_WINDOW = "context window full"
    REFUSAL = "refused"


@dataclass(frozen=True)
class Limits:
    max_turns: int = 50
    max_total_tokens: int = 2_000_000


@dataclass(frozen=True)
class RunResult:
    stop: StopReason
    final_text: str
    turns: int
    total_tokens: int
    messages: list[dict[str, Any]]


_CUT_OFF: dict[str | None, StopReason] = {
    "max_tokens": StopReason.MAX_TOKENS,
    "model_context_window_exceeded": StopReason.CONTEXT_WINDOW,
    "refusal": StopReason.REFUSAL,
}


def run_agent(
    stream: StreamFn,
    prompt: str,
    handle_tool: ToolHandler,
    *,
    tools: list[dict[str, Any]],
    model: str,
    effort: str,
    limits: Limits,
) -> RunResult:
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    turns = 0
    total_tokens = 0
    response: BetaMessage | None = None

    def result(stop: StopReason) -> RunResult:
        return RunResult(stop, _final_text(response), turns, total_tokens, messages)

    while True:
        if turns >= limits.max_turns:
            return result(StopReason.TURN_LIMIT)
        if total_tokens >= limits.max_total_tokens:
            return result(StopReason.TOKEN_LIMIT)

        with stream(
            model=model,
            max_tokens=MAX_TOKENS_PER_RESPONSE,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
            output_config={"effort": effort},
            cache_control={"type": "ephemeral"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as response_stream:
            response = response_stream.get_final_message()
        turns += 1
        total_tokens += _tokens_used(response)

        # Append the full content, thinking blocks included, even when stopping.
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason in _CUT_OFF:
            return result(_CUT_OFF[response.stop_reason])
        if response.stop_reason == "pause_turn":
            continue

        tool_uses = [block for block in response.content if isinstance(block, BetaToolUseBlock)]
        if not tool_uses:
            return result(StopReason.DONE)

        messages.append(
            {"role": "user", "content": [_run_tool(handle_tool, block) for block in tool_uses]}
        )


def _run_tool(handle_tool: ToolHandler, block: BetaToolUseBlock) -> dict[str, Any]:
    try:
        outcome = handle_tool(block.name, block.input)
    except Exception as exc:  # a broken tool must not end the run; the model can recover
        outcome = ToolResult.error(f"{type(exc).__name__}: {exc}")
    return {
        "type": "tool_result",
        "tool_use_id": block.id,
        "content": outcome.content,
        "is_error": outcome.is_error,
    }


def _tokens_used(response: BetaMessage) -> int:
    usage = response.usage
    return (
        usage.input_tokens
        + usage.output_tokens
        + (usage.cache_creation_input_tokens or 0)
        + (usage.cache_read_input_tokens or 0)
    )


def _final_text(response: BetaMessage | None) -> str:
    if response is None:
        return ""
    return "\n".join(block.text for block in response.content if block.type == "text")
