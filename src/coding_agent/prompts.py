"""
prompts.py - the fixed text sent to the model. Never changes between requests,
so prompt caching keeps hitting (CLAUDE.md § Claude API conventions).
"""

# What the model is told about run_command (SPEC.md § The run_command tool).
TOOL_DESCRIPTION = """\
Run one bash command in /workspace, a copy of the user's repo, and get back its \
exit code and combined stdout and stderr.

- Each call starts a fresh shell in /workspace: `cd` and variables don't carry \
over, so chain steps with `&&`.
- There is no network: `pip install`, `curl` and `git clone` won't work.
- Edit files with heredocs (`cat <<'EOF' > file`), `sed -i` or a short Python script.
- /workspace is not a git repo and must not become one: don't run `git init` \
anywhere in it. The harness tracks your changes itself.
- Long output is cut to its first and last 5,000 characters.\
"""
