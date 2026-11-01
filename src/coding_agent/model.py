"""
model.py - the one place that talks to the Anthropic API (SPEC.md L1, L6).

The loop only ever calls Model.query(messages) and gets a reply back, as in
mini-swe-agent. Everything about the request lives here, and every request
sends the same system prompt and tool, so prompt caching keeps hitting.
"""

import anthropic

from coding_agent.config import EFFORT, MAX_RETRIES, MAX_TOKENS, MODEL
from coding_agent.prompts import SYSTEM_PROMPT
from coding_agent.tools import TOOL

# Retry a safety-classifier refusal on Anthropic's recommended fallback model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class Model:
    def __init__(self, client=None):
        # The client reads ANTHROPIC_API_KEY from the environment; it goes nowhere else.
        self.client = client or anthropic.Anthropic(max_retries=MAX_RETRIES)

    def query(self, messages):
        """Send the whole history and return Claude's reply (a BetaMessage).

        Streamed so a long reply can't hit the HTTP timeout; we only keep the
        final message. API errors (after the SDK's retries) and Ctrl+C are
        raised for the loop to handle (L6, L8).
        """
        with self.client.beta.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=[TOOL],
            messages=messages,
            output_config={"effort": EFFORT},  # Opus 5.5 defaults to medium; say so
            cache_control={"type": "ephemeral"},  # caches the growing history
            fallbacks="default",
            betas=[FALLBACK_BETA],
        ) as stream:
            return stream.get_final_message()
