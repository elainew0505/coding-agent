"""
test_agent.py - the agent loop and the model wrapper (SPEC.md L1-L9).
Unit tests only: a scripted fake model, a fake sandbox, no Docker, no API.

Run them like any test:
  uv run pytest tests/test_agent.py -v

Or by hand:
  uv run python tests/test_agent.py             # every test, verbose
  uv run python tests/test_agent.py ctrl cap    # only tests whose name matches
  uv run python tests/test_agent.py --list      # list the test names
"""

import sys

import anthropic
import httpx2
import pytest

from coding_agent.agent import run_task
from coding_agent.config import CONTEXT_THRESHOLD, MAX_CALLS
from coding_agent.model import FALLBACK_BETA, Model
from coding_agent.prompts import SYSTEM_PROMPT
from coding_agent.sandbox import CommandResult
from coding_agent.tools import TOOL

from fakes import FakeClient, FakeModel, FakeSandbox, reply


def quiet(*_):
    """Swallow the loop's printing."""


def go(*replies, sandbox=None):
    model = FakeModel(*replies)
    sandbox = sandbox or FakeSandbox(CommandResult(0, "ok\n"))
    return run_task("fix the bug", model, sandbox, out=quiet), model, sandbox


# --- the loop ----------------------------------------------------------------


def test_runs_tools_then_finishes():
    run, _, sandbox = go(reply("ls", "pytest"), reply(text="Fixed it."))
    assert run.stop_reason == "done"
    assert [c for c, _ in sandbox.commands] == ["ls", "pytest"]
    assert run.calls == 2
    # L2: the tool results for one reply come back together, in order, in one user message
    results = run.messages[2]["content"]
    assert run.messages[2]["role"] == "user"
    assert [r["tool_use_id"] for r in results] == [b.id for b in run.messages[1]["content"]]
    assert all(r["content"] == "exit_code: 0\nok\n" and not r["is_error"] for r in results)


def test_history_is_append_only():
    _, model, _ = go(reply("ls"), reply("pytest"), reply(text="Done."))
    first, second, third = model.seen
    assert second[: len(first)] == first
    assert third[: len(second)] == second


def test_full_reply_content_is_appended():
    first = reply("ls", text="Let me look.")
    run, _, _ = go(first, reply(text="Done."))
    assert run.messages[1] == {"role": "assistant", "content": first.content}


def test_nonzero_exit_goes_back_as_a_normal_result():
    sandbox = FakeSandbox(CommandResult(1, "1 failed\n"))
    run, _, _ = go(reply("pytest"), reply(text="Done."), sandbox=sandbox)
    assert run.messages[2]["content"][0]["is_error"] is False


@pytest.mark.parametrize("reason", ["max_tokens", "refusal"])
def test_other_stop_reasons_stop_without_running_tools(reason):
    run, _, sandbox = go(reply("rm -rf half_written", stop_reason=reason))
    assert run.stop_reason == reason
    assert reason in run.detail
    assert sandbox.commands == []  # L3: a cut-off tool call is never run


def test_call_cap():
    run, _, _ = go(*[reply("ls") for _ in range(MAX_CALLS + 5)])
    assert run.stop_reason == "call_cap"
    assert run.calls == MAX_CALLS


def test_context_backstop_stops_after_running_that_replys_tools():
    run, _, sandbox = go(reply("ls", input_tokens=CONTEXT_THRESHOLD + 1), reply(text="never"))
    assert run.stop_reason == "context_full"
    assert sandbox.commands == [("ls", 60)]  # L5: that reply's tool calls still ran
    assert run.messages[-1]["role"] == "user"


def test_api_error_ends_the_task():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.APIConnectionError(request=request)
    run, _, _ = go(reply("ls"), error)
    assert run.stop_reason == "api_error"
    assert run.calls == 1


def test_model_call_failure_ends_the_task():
    # e.g. no credentials: the SDK raises TypeError on the first call
    run, _, _ = go(TypeError("Could not resolve authentication method"))
    assert run.stop_reason == "model_error"
    assert "authentication" in run.detail


def test_ctrl_c_while_a_reply_streams_drops_it():
    run, _, _ = go(reply("ls"), KeyboardInterrupt())
    assert run.stop_reason == "interrupted"
    assert run.messages[-1]["role"] == "user"  # nothing partial was appended


def test_ctrl_c_while_a_command_runs_ends_the_task():
    run, _, _ = go(reply("sleep 99"), sandbox=FakeSandbox(error=KeyboardInterrupt()))
    assert run.stop_reason == "interrupted"


def test_usage_counts_cached_input():
    run, _, _ = go(reply("ls", input_tokens=100, output_tokens=7), reply(text="Done."))
    assert run.usage == {"input_tokens": 200, "output_tokens": 17}


def test_prints_commands_and_text():
    printed = []
    run_task(
        "t",
        FakeModel(reply("ls", text="Looking."), reply(text="Done.")),
        FakeSandbox(CommandResult(0, "a\nb\n")),
        out=printed.append,
    )
    assert printed == ["Looking.", "$ ls", "a\nb", "Done."]


# --- the model wrapper -------------------------------------------------------


def test_every_request_has_the_same_system_and_tools():
    client = FakeClient(reply("ls"), reply(text="Done."))
    model = Model(client)
    model.query([{"role": "user", "content": "a"}])
    model.query([{"role": "user", "content": "b"}])
    first, second = client.requests
    assert first["system"] == second["system"] == SYSTEM_PROMPT
    assert first["tools"] == second["tools"] == [TOOL]


def test_request_settings():
    client = FakeClient(reply(text="Done."))
    Model(client).query([{"role": "user", "content": "a"}])
    request = client.requests[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["output_config"] == {"effort": "medium"}
    assert request["cache_control"] == {"type": "ephemeral"}
    assert request["fallbacks"] == "default"
    assert request["betas"] == [FALLBACK_BETA]
    assert "temperature" not in request and "tool_choice" not in request


def test_query_returns_the_final_message():
    message = reply(text="Done.")
    assert Model(FakeClient(message)).query([]) is message


# --- run by hand -------------------------------------------------------------

if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--list"]:
        sys.exit(pytest.main([__file__, "--collect-only", "-q"]))
    selection = ["-k", " or ".join(args)] if args else []
    # Running as a script imports the SDK (and anyio) before pytest starts; that
    # only means pytest can't rewrite anyio's asserts, so hide the warning.
    quiet_warning = ["-W", "ignore::pytest.PytestAssertRewriteWarning"]
    sys.exit(pytest.main([__file__, "-v", *quiet_warning, *selection]))
