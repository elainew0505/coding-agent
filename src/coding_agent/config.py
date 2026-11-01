"""
config.py - every setting in one place (SPEC.md L9, S9).

Plain constants: there is no config file, and nothing is read from the repo
you point the agent at, so a repo can't change its own limits (SPEC.md Scope).
"""

# --- model (L1, L6) ----------------------------------------------------------
MODEL = "claude-opus-5-5"
EFFORT = "medium"  # output_config.effort; set explicitly on every request
MAX_TOKENS = 32_000  # per reply
MAX_RETRIES = 3  # SDK retries on API errors (its default is 2)

# --- loop limits (L4, L5) ----------------------------------------------------
MAX_CALLS = 50  # model calls per run
CONTEXT_THRESHOLD = 150_000  # input tokens; above this the task stops and goes to review

# --- sandbox (S1, run_command) -----------------------------------------------
IMAGE = "coding-agent:toy"  # built with: docker build -t coding-agent:toy .
DEFAULT_TIMEOUT = 60  # seconds per command, if the model doesn't say
MAX_TIMEOUT = 300
BACKSTOP_EXTRA = 10  # seconds the host waits beyond the in-container timeout
HEAD_CHARS = 5_000  # output cap: keep the first 5,000 ...
TAIL_CHARS = 5_000  # ... and the last 5,000 characters

# --- copy-in deny list (S9) --------------------------------------------------
# Never copied, even if git tracks them. Anything copied in can be read by the
# model, sent to the API and written to the trajectory.
DENY_NAMES = [".env", ".env.*", "*.pem", "*.key", "id_rsa*", "id_ecdsa*", "id_ed25519*"]
DENY_DIRS = [".agent"]
