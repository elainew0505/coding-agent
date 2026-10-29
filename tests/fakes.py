"""Test doubles for the model client.

Until Task 6 records real responses into tests/fixtures/, responses are built
from the SDK's own BetaMessage type so the loop sees the same shapes the API returns.
"""

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from anthropic.types.beta import BetaMessage


def text(value: str) -> dict[str, Any]:
    return {"type": "text", "text": value}


def tool_use(block_id: str, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": block_id, "name": name, "input": tool_input}


def message(
    *content: dict[str, Any],
    stop_reason: str = "end_turn",
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> BetaMessage:
    return BetaMessage.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": list(content),
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }
    )


class _FinalMessage:
    def __init__(self, response: BetaMessage):
        self._response = response

    def get_final_message(self) -> BetaMessage:
        return self._response


class FakeClient:
    """Replays scripted responses in order and records every request.

    Requests are deep-copied when recorded, so later appends to the loop's
    history can't change what an earlier request looked like.
    """

    def __init__(self, *responses: BetaMessage):
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    @contextmanager
    def stream(self, **kwargs: Any) -> Iterator[_FinalMessage]:
        self.requests.append(copy.deepcopy(kwargs))
        yield _FinalMessage(self._responses.pop(0))


class RepeatingClient(FakeClient):
    """Returns the same response forever, for limit tests."""

    def __init__(self, response: BetaMessage):
        super().__init__()
        self._response = response

    @contextmanager
    def stream(self, **kwargs: Any) -> Iterator[_FinalMessage]:
        self.requests.append(copy.deepcopy(kwargs))
        yield _FinalMessage(self._response)
