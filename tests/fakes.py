"""Stand-ins for the real boundaries, so unit tests need no Docker and no API."""

from coding_agent.sandbox import CommandResult


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
