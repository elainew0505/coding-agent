# Testing Patterns Reference (Python / pytest)

Quick reference of Python testing patterns for this project — a coding agent built on the Anthropic Python SDK — illustrating the universal principles from the `test-driven-development` skill. Tooling: `pytest`, run through `uv run pytest`.

Adapted from the JS/TS version in [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) (MIT).

## Table of Contents

- [Test Structure (Arrange-Act-Assert)](#test-structure-arrange-act-assert)
- [Test Naming Conventions](#test-naming-conventions)
- [Common Assertions](#common-assertions)
- [Fixtures](#fixtures)
- [Mocking Patterns](#mocking-patterns)
- [Testing the Agent Loop (no network)](#testing-the-agent-loop-no-network)
- [Testing Tools](#testing-tools)
- [Docker Tests](#docker-tests)
- [Live API Tests](#live-api-tests)
- [Test Anti-Patterns](#test-anti-patterns)

## Test Structure (Arrange-Act-Assert)

```python
def test_create_task_defaults_to_pending():
    # Arrange: set up test data and preconditions
    data = {"title": "Test Task", "priority": "high"}

    # Act: perform the action being tested
    task = create_task(data)

    # Assert: verify the outcome
    assert task.title == "Test Task"
    assert task.priority == "high"
    assert task.status == "pending"
```

## Test Naming Conventions

```python
# Pattern: test_<unit>_<expected behavior>_<condition>
# Group related tests in a module or a plain class (no unittest.TestCase needed).
class TestRunCommand:
    def test_returns_exit_code_on_first_line(self): ...
    def test_nonzero_exit_is_not_an_error(self): ...
    def test_rejects_empty_command(self): ...
    def test_caps_output_keeping_head_and_tail(self): ...
```

## Common Assertions

```python
# Equality and identity
assert result == expected
assert result is None

# Collections
assert item in items
assert len(items) == 3
assert result == {"id": 1, "title": "x"}      # dicts compare deeply

# Floats
assert result == pytest.approx(0.3)

# Strings
assert "substring" in text
assert re.search(r"pattern", text)

# Exceptions
with pytest.raises(ValidationError):
    fn()
with pytest.raises(ValueError, match="title is required"):
    fn()

# Many inputs, one behavior
@pytest.mark.parametrize("args", [{"command": ""}, {"command": "ls", "timeout": 0}, {"command": "ls", "timeout": 301}])
def test_rejects_invalid_input(args):
    with pytest.raises(ValidationError):
        RunCommandInput.model_validate(args)
```

## Fixtures

```python
# tests/conftest.py
import pytest

@pytest.fixture
def workspace(tmp_path):
    """A throwaway repo directory to copy into the sandbox."""
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a - b\n")
    return tmp_path

@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    """Unit tests must never reach the real API."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
```

Prefer `tmp_path`, `monkeypatch` and `capsys` over hand-rolled setup and teardown.

## Mocking Patterns

### Mock at Boundaries Only

```
Mock these:                    Don't mock these:
├── The Anthropic client       ├── The agent loop itself
├── Subprocess / Docker        ├── Message/history handling
├── Network requests           ├── Tool input validation
├── Time/clock (when needed)   ├── Output truncation
└── Environment variables      └── Pure functions
```

### Inject the boundary, don't patch it

Design the agent to take its model client as a parameter, so tests pass a fake instead of patching imports:

```python
class Agent:
    def __init__(self, client, sandbox, model="claude-opus-5-5"):
        self.client = client
        self.sandbox = sandbox
        ...
```

Reach for `monkeypatch.setattr` or `unittest.mock` only when injection isn't possible.

## Testing the Agent Loop (no network)

Script the replies in each test, built as the SDK's own `Message` type so pydantic rejects any shape the API wouldn't return (a small `reply(...)` helper keeps this short). Keep one or two real responses, saved with `response.model_dump_json()` into `tests/fixtures/`, to catch drift; load them the same way:

```python
from pathlib import Path
from anthropic.types import Message

FIXTURES = Path(__file__).parent / "fixtures"

def load_message(name: str) -> Message:
    return Message.model_validate_json((FIXTURES / f"{name}.json").read_text())

class FakeClient:
    """Returns scripted responses in order and records every request."""

    def __init__(self, *responses: Message):
        self._responses = list(responses)
        self.requests: list[dict] = []
        self.messages = self   # so agent code can call client.messages.create(...)

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self._responses.pop(0)


def test_agent_runs_tool_then_finishes(fake_sandbox):
    client = FakeClient(
        reply(tool_calls=["sed -i 's/a - b/a + b/' app.py"]),
        reply(text="Fixed add()."),
    )
    agent = Agent(client=client, sandbox=fake_sandbox)

    result = agent.run("Fix the add function")

    assert fake_sandbox.commands == ["sed -i 's/a - b/a + b/' app.py"]
    assert result.stop_reason == "end_turn"
    # History is append-only: second request starts with the first request's messages
    first, second = client.requests
    assert second["messages"][: len(first["messages"])] == first["messages"]
```

Adapt `FakeClient` to whatever call the agent makes (for example `messages.stream(...)` returns a context manager with `get_final_message()`).

Things worth asserting on the recorded requests:
- The full `response.content` (not just text) is appended back to history.
- Every `tool_use` gets a matching `tool_result`, all in one user message.
- A failed tool produces `tool_result` with `is_error: True`, not an exception.
- `system` and `tools` are byte-identical across turns (prompt caching depends on it).

## Testing Tools

Test the `run_command` handler as a plain function against a fake sandbox. No model involved.

```python
def test_run_command_nonzero_exit_is_not_an_error(fake_sandbox):
    fake_sandbox.next_result = (1, "")
    out = run_command({"command": "false"}, sandbox=fake_sandbox)

    assert fake_sandbox.commands == ["false"]
    assert out.is_error is False
    assert out.content.startswith("exit_code: 1")

def test_run_command_times_out(fake_sandbox):
    fake_sandbox.hang = True
    out = run_command({"command": "sleep 999", "timeout": 1}, sandbox=fake_sandbox)

    assert out.is_error
    assert "timed out" in out.content
```

Security-relevant behaviour (oversized output, commands that never return, shell metacharacters such as `"; touch /tmp/pwned"` never reaching a host shell) belongs here and in the Docker tests as explicit tests, not as assumptions.

## Docker Tests

Run in the default `uv run pytest`, and skip themselves when Docker isn't running (a fixture that runs `docker version` once and calls `pytest.skip`). Use the real agent image with `--network none`, and remove the container in `finally`.

## Live API Tests

Tests that call the real API cost money and are non-deterministic. They're skipped unless `--live` is passed (an option registered in `tests/conftest.py`) and `ANTHROPIC_API_KEY` is set:

```python
# tests/conftest.py
def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", help="Run tests that call the real API (cost money)")
```

Only the user runs `uv run pytest --live`; an AI agent never does. Use them to re-record fixtures, not as the main safety net.

## Test Anti-Patterns

| Anti-Pattern | Problem | Better Approach |
|---|---|---|
| Hitting the real API in unit tests | Slow, costs money, flaky | A fake client with scripted replies |
| Hand-writing API response dicts | Drift from the real SDK shape | Build `anthropic.types.Message` objects; keep a few recorded replies as a drift check |
| Testing implementation details | Breaks on refactor | Test inputs/outputs and recorded requests |
| Asserting on exact model wording | Model output varies | Assert on effects (files changed, tools called) |
| Running tool tests on the real repo | Tests mutate your code | Use `tmp_path` |
| Shared mutable state | Tests pollute each other | Function-scoped fixtures |
| Permanent `pytest.mark.skip` | Dead code | Remove or fix it |
| Overly broad assertions | Doesn't catch regressions | Be specific |
