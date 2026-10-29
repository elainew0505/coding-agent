# Testing Patterns Reference (Python / pytest)

Quick reference of Python testing patterns for this project — a coding agent built on the Anthropic Python SDK — illustrating the universal principles from the `test-driven-development` skill. Tooling: `pytest`, `pytest-asyncio`, `typer.testing.CliRunner`, run through `uv run pytest`.

Adapted from the JS/TS version in [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) (MIT).

## Table of Contents

- [Test Structure (Arrange-Act-Assert)](#test-structure-arrange-act-assert)
- [Test Naming Conventions](#test-naming-conventions)
- [Common Assertions](#common-assertions)
- [Fixtures](#fixtures)
- [Mocking Patterns](#mocking-patterns)
- [Testing the Agent Loop (no network)](#testing-the-agent-loop-no-network)
- [Testing Tools](#testing-tools)
- [CLI Testing](#cli-testing)
- [Live API Tests](#live-api-tests)
- [Async Tests](#async-tests)
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
class TestEditTool:
    def test_replaces_unique_match(self): ...
    def test_errors_when_match_is_not_unique(self): ...
    def test_errors_when_file_is_outside_workspace(self): ...
    def test_preserves_trailing_newline(self): ...
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
@pytest.mark.parametrize("path", ["../etc/passwd", "/etc/passwd", "a/../../b"])
def test_rejects_paths_outside_workspace(path, workspace):
    with pytest.raises(PathOutsideWorkspace):
        resolve_in_workspace(workspace, path)
```

## Fixtures

```python
# tests/conftest.py
import pytest

@pytest.fixture
def workspace(tmp_path):
    """A throwaway repo directory for tools to operate on."""
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
├── Time/clock (when needed)   ├── Path resolution logic
└── Environment variables      └── Pure functions
```

### Inject the boundary, don't patch it

Design the agent to take its model client as a parameter, so tests pass a fake instead of patching imports:

```python
class Agent:
    def __init__(self, client, tools, model="claude-opus-5-5"):
        self.client = client
        ...
```

Reach for `monkeypatch.setattr` or `unittest.mock` only when injection isn't possible.

## Testing the Agent Loop (no network)

Record real API responses once and replay them. Save with `response.model_dump_json()` into `tests/fixtures/`, then load with the SDK's own types so the loop sees exactly what the API returns:

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


def test_agent_runs_tool_then_finishes(workspace):
    client = FakeClient(load_message("edit_tool_use"), load_message("final_answer"))
    agent = Agent(client=client, tools=default_tools(workspace))

    result = agent.run("Fix the add function")

    assert "return a + b" in (workspace / "app.py").read_text()
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

Test each tool as a plain function against `tmp_path`. No model involved.

```python
def test_bash_tool_runs_in_sandbox(workspace, fake_sandbox):
    out = bash_tool({"command": "ls"}, sandbox=fake_sandbox)

    assert fake_sandbox.commands == ["ls"]
    assert out.is_error is False

def test_bash_tool_times_out(fake_sandbox):
    fake_sandbox.hang = True
    out = bash_tool({"command": "sleep 999"}, sandbox=fake_sandbox, timeout=0.1)

    assert out.is_error
    assert "timed out" in out.content
```

Security-relevant behaviour (path escapes, symlinks, oversized output, commands that never return) belongs here as explicit tests, not as assumptions.

## CLI Testing

```python
from typer.testing import CliRunner
from coding_agent.cli import app

runner = CliRunner()

def test_cli_requires_a_prompt():
    result = runner.invoke(app, [])

    assert result.exit_code != 0
    assert "Missing argument" in result.output
```

## Live API Tests

Tests that call the real API cost money and are non-deterministic. Mark them and keep them out of the default run:

```python
# pyproject.toml
# [tool.pytest.ini_options]
# markers = ["live: calls the real Anthropic API"]
# addopts = "-m 'not live'"

@pytest.mark.live
def test_model_can_fix_simple_bug(tmp_path): ...
```

Run them deliberately with `uv run pytest -m live`. Use them to re-record fixtures, not as the main safety net.

## Async Tests

```python
import pytest

@pytest.mark.asyncio
async def test_streams_final_message(): ...
```

Always `await` the call under test; an un-awaited coroutine passes silently.

## Test Anti-Patterns

| Anti-Pattern | Problem | Better Approach |
|---|---|---|
| Hitting the real API in unit tests | Slow, costs money, flaky | Replay recorded responses |
| Hand-writing API response dicts | Drift from the real SDK shape | Record with `model_dump_json()`, load with `model_validate_json()` |
| Testing implementation details | Breaks on refactor | Test inputs/outputs and recorded requests |
| Asserting on exact model wording | Model output varies | Assert on effects (files changed, tools called) |
| Running tool tests on the real repo | Tests mutate your code | Use `tmp_path` |
| Shared mutable state | Tests pollute each other | Function-scoped fixtures |
| Permanent `pytest.mark.skip` | Dead code | Remove or fix it |
| Overly broad assertions | Doesn't catch regressions | Be specific |
