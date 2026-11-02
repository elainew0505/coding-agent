"""Stand-ins for the real boundaries, so unit tests need no Docker and no API."""

import itertools

from anthropic.types.beta import BetaMessage

from coding_agent.sandbox import CommandResult

_ids = itertools.count(1)


def reply(*commands, text="", stop_reason=None, input_tokens=100, output_tokens=10):
    """A real BetaMessage, as Model.query would return it.

    reply("ls", "pytest")                -> two run_command calls (stop_reason tool_use)
    reply(text="Done.")                  -> a final answer (end_turn)
    reply("ls", stop_reason="max_tokens") -> a cut-off reply holding a tool call
    Built with model_validate, so pydantic rejects shapes the SDK wouldn't produce.
    """
    content = [{"type": "text", "text": text}] if text else []
    content += [
        {
            "type": "tool_use",
            "id": f"toolu_{next(_ids)}",
            "name": "run_command",
            "input": {"command": command},
        }
        for command in commands
    ]
    return BetaMessage.model_validate(
        {
            "id": f"msg_{next(_ids)}",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": content,
            "stop_reason": stop_reason or ("tool_use" if commands else "end_turn"),
            "stop_sequence": None,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        }
    )


class FakeModel:
    """Returns scripted replies in order (or raises one, if it's an exception).
    Records the history it was given on each call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen = []

    def query(self, messages):
        self.seen.append(list(messages))
        next_reply = self.replies.pop(0)
        if isinstance(next_reply, BaseException):
            raise next_reply
        return next_reply


class FakeClient:
    """Mimics client.beta.messages.stream(...) for testing Model itself.
    Records the keyword arguments of every request."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []
        self.beta = self
        self.messages = self

    def stream(self, **kwargs):
        self.requests.append(kwargs)
        return _FakeStream(self.replies.pop(0))


class _FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class FakeSandbox:
    """Records each command and returns a preset result (or raises)."""

    def __init__(self, result=None, error=None):
        self.result = result or CommandResult(0, "")
        self.error = error
        self.commands = []

    def run(self, command, timeout):
        self.commands.append((command, timeout))
        if self.error:
            raise self.error
        return self.result
