# Tasks: Phase 1, evaluation

Plan: [plan.md](plan.md). Spec: [SPEC-Phase1.md](../SPEC-Phase1.md). Every task also meets the Definition of Done (`.claude/references/definition-of-done.md`) and updates docs in the same commit. Phase 0's tasks are in [archive/](archive/).

**All checks:**

```bash
uv run pytest && uv run pyright && uv run ruff check . && uv run ruff format --check .
```

Tasks marked **(user)** cost money or need a rented machine: only the user runs them, never an agent.

---

## Before Phase A: File tools

Spec: `SPEC.md` § File tools (T1–T6), [0004](../docs/decisions/0004-file-tools.md). Every slice is approved by the user before it starts (CLAUDE.md § Working style).

## Task FT1: `read_file` and `write_file`, with the sandbox methods they need

**Description:** Two sandbox methods, then the two tools on top of them. `read_bytes(path, limit)` runs `head -c <limit> -- <path>` in the container; `write_bytes(path, data)` sends the bytes on `docker exec -i` stdin to `sh -c 'mkdir -p -- "$(dirname -- "$1")" && cat > "$1"' sh <path>` (T2, T4). Both run with the same `-w` working folder as `run()`, not `_exec`'s `-w /`, pass the path unchanged, use an argument list and a timeout, and raise `SandboxError` with the program's last error line. On top, `read_file` and `write_file` in `tools.py` (T3, T4), each with a pydantic input model, a hand-written `input_schema` and a handler returning a `ToolResult`: `read_file` numbers lines, stops at 10,000 characters with a footer naming the next `start_line`, cuts lines at 2,000 characters and reports binary files; `write_file` refuses content over 1,000,000 bytes; a NUL in a path is invalid input. Not yet sent to the model: FT3 does that.

**Acceptance criteria:**
- [ ] A path or content holding `"; touch /tmp/pwned`, `$(…)` or backticks is written exactly and creates no `/tmp/pwned*`, on the host or in the container (docker)
- [ ] A file written by relative path with `write_file` reads back with `run_command` `cat <path>` (docker)
- [ ] `read_file` on a 50 MB file holds at most 1,000,001 bytes and says the file was cut; line ranges, empty files, a `start_line` past the end and a missing file each give the result the spec says
- [ ] Each schema agrees with its pydantic model (unit test)

**Verification:**
- [ ] Tests pass: `uv run pytest tests/test_sandbox.py tests/test_file_tools.py` (Docker running)
- [ ] All checks pass

**Dependencies:** None

**Files likely touched:** `src/coding_agent/sandbox.py`, `src/coding_agent/tools.py`, `src/coding_agent/prompts.py`, `src/coding_agent/config.py`, `tests/fakes.py`, `tests/test_sandbox.py`, `tests/test_file_tools.py`

**Estimated scope:** Medium

---

## Task FT2: `edit_file`

**Description:** Exact-string replace (T5): `old_string` must appear once, or at least once with `replace_all`. The replace happens in host memory and the file goes back through `write_bytes`. The result shows the edited lines with their numbers.

**Acceptance criteria:**
- [ ] Zero matches, two matches without `replace_all`, old equal to new, a non-UTF-8 file, a file over 1,000,000 bytes and a `replace_all` result over 1,000,000 bytes are all `is_error` and leave the file unchanged
- [ ] The size of a `replace_all` result is checked before it is built
- [ ] One Docker test edits a real file whose name and content hold shell text

**Verification:**
- [ ] Tests pass: `uv run pytest tests/test_file_tools.py` (Docker running for the one Docker test)
- [ ] All checks pass

**Dependencies:** FT1

**Files likely touched:** `src/coding_agent/tools.py`, `src/coding_agent/prompts.py`, `tests/test_file_tools.py`

**Estimated scope:** Small

---

## Task FT3: Tool sets and `--bash-only`

**Description:** Two tool sets (T1): `all` (default: `run_command` with a description pointing at the file tools, plus the three file tools and the matching system prompt) and `bash` (`run_command` alone). Both sets' system prompts and descriptions name no folder and no git rule: those facts move to the first user message (`SPEC.md` § First message), so the eval can reuse the same texts. `Model` takes the tool set; the loop dispatches every tool call by name and prints one line per call (T6); `coding-agent --bash-only` picks `bash`; the trajectory records the set's name, system prompt and tools. README and CLAUDE.md mention the flag.

**Acceptance criteria:**
- [ ] `--bash-only` sends `[run_command]` alone; no system prompt or tool description names `/workspace`, and the first message does (unit)
- [ ] With the default set, a fake model's `edit_file` call fixes the toy bug end to end and the diff applies (docker)
- [ ] An unknown tool name gets an `is_error` result, never a crash

**Verification:**
- [ ] Tests pass: `uv run pytest` (Docker running)
- [ ] All checks pass
- [ ] Manual check: the user runs `coding-agent` once with and once without `--bash-only` on `../toy-repo`

**Dependencies:** FT2

**Files likely touched:** `src/coding_agent/model.py`, `src/coding_agent/agent.py`, `src/coding_agent/__main__.py`, `tests/test_agent.py`, `tests/test_main.py`, `README.md`

**Estimated scope:** Medium

---

## Checkpoint FT: the File tools acceptance tests in `SPEC.md` checked off; review with the user; open the PR

---

## Task 1: The `eval` extra and the dataset loader

**Description:** Add `swebench` and `datasets` as an optional `eval` extra, after the `security-and-hardening` supply-chain check. Add `eval/dataset.py`: load `MariusHobbhahn/swe-bench-verified-mini`, split `test`, at a pinned revision (E1), and drop the tasks in `evals/exclusions.txt`. Only `coding_agent.eval` imports the new packages.

**Acceptance criteria:**
- [ ] `uv sync` still installs only `anthropic` and `pydantic` at runtime; `uv sync --extra eval` adds both, pinned in `uv.lock`
- [ ] The loader returns 50 tasks at the pinned revision, minus the exclusions; `--instances` and `--limit N` subsets work (unit test with a small fake dataset)
- [ ] The exclusion file format is one `instance_id  # reason` per line; a line without a reason is an error

**Verification:**
- [ ] Tests pass: `uv run pytest tests/test_eval_dataset.py`
- [ ] All checks pass
- [ ] Supply-chain check written up in the commit message

**Dependencies:** None

**Files likely touched:** `pyproject.toml`, `uv.lock`, `CLAUDE.md` (§ Stack, § Commands), `src/coding_agent/eval/__init__.py`, `src/coding_agent/eval/dataset.py`, `evals/exclusions.txt`, `tests/test_eval_dataset.py`

**Estimated scope:** Small

---

## Task 2: The gold check

**Description:** `eval/images.py` pulls each task's image on x86 or builds it with `--namespace ''` on Apple Silicon, reusing cached ones (E2). `eval/grade.py` runs the official harness and reads its per-task results. A gold-check command grades the dataset's own fixes (`--predictions_path gold`, E3) and writes every task that fails to build or resolve into `evals/exclusions.txt` with the reason (E3, E4). It needs no agent code and no model, so it finds image and grading problems first.

**Acceptance criteria:**
- [ ] The gold check runs on the Mac and records the harness version
- [ ] Every task either resolves or is in `evals/exclusions.txt` with a reason
- [ ] `grade.py` maps the harness's results to resolved, unresolved, empty patch or error (unit test on a saved harness results file)

**Verification:**
- [ ] Tests pass: `uv run pytest tests/test_eval_grade.py`
- [ ] All checks pass
- [ ] Manual check: the gold check on the Mac; the count of resolved tasks is noted in the commit

**Dependencies:** Task 1

**Files likely touched:** `src/coding_agent/eval/images.py`, `src/coding_agent/eval/grade.py`, `evals/exclusions.txt`, `tests/test_eval_grade.py`, `tests/fixtures/`

**Estimated scope:** Medium

---

## Task 3: Sandbox and model take their settings from the caller

**Description:** The first half of the 0003 refactor, with no behaviour change. `Sandbox` takes its image, working folder, command prefix and limits (memory, default timeout) as one settings value instead of reading `WORKDIR` and `config` directly. `Model` takes its system prompt and tool instead of importing them. The MVP's values become the defaults, so `coding-agent "<task>"` works exactly as before.

**Acceptance criteria:**
- [ ] No `/workspace` left in `sandbox.py` or `model.py` outside the MVP's default settings
- [ ] Every existing test passes without being edited
- [ ] A unit test builds a Sandbox with another working folder and checks `docker exec -w` and the lockdown flags use it

**Verification:**
- [ ] Tests pass: `uv run pytest` (Docker running)
- [ ] All checks pass

**Dependencies:** None

**Files likely touched:** `src/coding_agent/sandbox.py`, `src/coding_agent/model.py`, `src/coding_agent/config.py`, `tests/test_sandbox.py`

**Estimated scope:** Medium

---

## Task 4: `modes.py`: the MVP as CopyInSetup and ReviewAndApply

**Description:** The second half of the refactor. `modes.py` defines the Setup interface (provides the M1–M7 values, then gets the code in and takes the baseline) and the Finish interface (takes the diff and the stop reason). CopyInSetup wraps today's copy-in and S5 baseline; ReviewAndApply wraps today's review, apply and trajectory path. `__main__.main()` runs Setup → `run_task` → Finish.

**Acceptance criteria:**
- [ ] Every existing test passes without being edited, including `test_main.py`
- [ ] `__main__.py` no longer calls `copy_in`, `baseline` or `review` directly
- [ ] SPEC.md is unchanged: the MVP behaves exactly as specified

**Verification:**
- [ ] Tests pass: `uv run pytest` (Docker running)
- [ ] All checks pass
- [ ] Manual check: `uv run coding-agent "What does this project do?"` in `../toy-repo` behaves as before (user, one cheap real call)

**Dependencies:** Task 3

**Files likely touched:** `src/coding_agent/modes.py`, `src/coding_agent/__main__.py`, `src/coding_agent/agent.py`, `tests/test_modes.py`

**Estimated scope:** Medium

---

## Checkpoint A: review with the user

- [ ] Gold check done; exclusions written down with reasons
- [ ] Every MVP test passes unchanged

---

## Task 5: ImageSetup on one real task image

**Description:** `eval/mode.py` ImageSetup for one task: its SWE-bench image (M1, S8 checks it exists), `/testbed` (M2), commands with the `testbed` conda environment active (M3; find out how the images activate it), eval limits 4 GB and 120 s (M4), no copy-in (M6), clean git history (E5), and a baseline of exactly the repo's own files from `git -C /testbed ls-files -z` (M7). Docker tests use one small, fast task image.

**Acceptance criteria:**
- [ ] After setup, `git -C /testbed log --all` and `git -C /testbed fsck --unreachable` show no commit newer than the task's starting commit
- [ ] Running the repo's tests (which rebuilds ignored build files) and then taking the diff gives an empty patch
- [ ] A command can import the repo's package, i.e. the conda environment is active; the container has `--network none` and 4 GB memory

**Verification:**
- [ ] Tests pass: `uv run pytest tests/test_eval_mode.py` (Docker running, task image built)
- [ ] All checks pass

**Dependencies:** Tasks 2 and 4

**Files likely touched:** `src/coding_agent/eval/mode.py`, `src/coding_agent/config.py`, `tests/test_eval_mode.py`

**Estimated scope:** Medium

---

## Task 6: Eval first message

**Description:** The eval's first user message (A4, M5): `/testbed` is a git repo at the starting commit; `git diff`/`log`/`status` fine, no commit, stash or checkout; no network; reproduce first; source files only; run tests; delete scratch scripts; short summary; then the issue text. ImageSetup provides it. The system prompt and tool descriptions are the shared ones from FT3, unchanged.

**Acceptance criteria:**
- [ ] With either tool set, eval requests send exactly the same `system` and `tools` as `coding-agent` with that set (fake client)
- [ ] The facts and the issue text are in the first user message only, so `system` is the same for every task
- [ ] Neither the system prompt nor any tool description mentions `/workspace` or `/testbed` (unit test over both tool sets)

**Verification:**
- [ ] Tests pass: `uv run pytest tests/test_tools.py tests/test_eval_mode.py`
- [ ] All checks pass

**Dependencies:** Task 5

**Files likely touched:** `src/coding_agent/prompts.py`, `src/coding_agent/tools.py`, `src/coding_agent/eval/mode.py`, `tests/test_tools.py`

**Estimated scope:** Small

---

## Task 7: RecordPrediction, and the gold fix through `run_command`

**Description:** RecordPrediction (F1, F2, A5): append `{instance_id, model_name_or_path, model_patch}` to the predictions file right away, empty patch included, for every stop reason; write the trajectory to `runs/<run id>/trajectories/<trial>/<instance_id>.json`. Then the key Docker test: a fake model writes the task's gold fix into `/testbed` with `run_command`, and the official harness resolves the recorded patch.

**Acceptance criteria:**
- [ ] Every stop reason (done, call cap, context limit, refusal, API error) records a prediction (unit test, fake sandbox)
- [ ] The fake-model gold fix on the Task 5 image is resolved by the official harness
- [ ] A do-nothing fake model gives an empty patch, graded as empty patch, not error

**Verification:**
- [ ] Tests pass: `uv run pytest tests/test_eval_mode.py`
- [ ] All checks pass

**Dependencies:** Task 6

**Files likely touched:** `src/coding_agent/eval/mode.py`, `src/coding_agent/agent.py` (trajectory writer takes a path), `tests/test_eval_mode.py`

**Estimated scope:** Medium

---

## Checkpoint B: review with the user

- [ ] One task goes through ImageSetup → loop → RecordPrediction → harness, and the gold fix resolves

---

## Task 8: The eval runner and `coding-agent eval`

**Description:** `eval/runner.py` runs the selected tasks one after another, each in a fresh container, into `runs/<run id>/` (`meta.json`, `predictions-1.jsonl`, `trajectories/1/`). Resume (R4): skip tasks already in the predictions file. Clean Ctrl+C (R5): stop the current task, remove its container, end the run without recording it. CLI: `coding-agent eval [--model ID] [--bash-only] [--limit N | --instances ID,...] [--run-id ID]`; `--bash-only` picks the `bash` tool set from FT3 (A1). `runs/` is git-ignored.

**Acceptance criteria:**
- [ ] `meta.json` records model, tool set, settings, agent git commit, dataset revision, harness version and the task list; resuming with a different model or tool set is refused
- [ ] A run stopped after 2 of 4 tasks resumes and runs only the other 2 (unit test, fake model and sandbox)
- [ ] After Ctrl+C, no container from the run is left running, and the interrupted task isn't in the predictions file (Docker test)

**Verification:**
- [ ] Tests pass: `uv run pytest tests/test_eval_runner.py`
- [ ] All checks pass

**Dependencies:** Task 7 and FT3

**Files likely touched:** `src/coding_agent/eval/runner.py`, `src/coding_agent/__main__.py`, `.gitignore`, `tests/test_eval_runner.py`

**Estimated scope:** Medium

---

## Task 9: Cost: price table, per-model context limit, `--max-cost`

**Description:** A price table per model in `config.py`, from the current pricing docs, not memory (R3). Context limit per model (R2): 90,000 for Haiku 5.5, 150,000 otherwise, checked after each response as in L5 (the pre-send estimate is Task 13). Cost per task in the trajectory; `--max-cost` (default $5) starts no new task once the estimated spend reaches it.

**Acceptance criteria:**
- [ ] Cost is computed from fresh input, cache read, cache write and output tokens separately (unit test with a fixed usage)
- [ ] With Haiku 5.5, a task stops with stop reason `context limit` above 90,000 input tokens and still records its diff
- [ ] With `--max-cost` set below the first task's cost, the second task never starts; resuming with a higher limit continues

**Verification:**
- [ ] Tests pass: `uv run pytest tests/test_eval_runner.py tests/test_agent.py`
- [ ] All checks pass

**Dependencies:** Task 8

**Files likely touched:** `src/coding_agent/config.py`, `src/coding_agent/agent.py`, `src/coding_agent/eval/runner.py`, `tests/test_eval_runner.py`

**Estimated scope:** Medium

---

## Task 10: Grading and the report

**Description:** After all tasks, the runner calls `grade.py` on the predictions file (`--run_id <run id>-t1`, `--namespace ''` on Apple Silicon) and keeps the harness's logs in `runs/<run id>/harness/`. `eval/report.py` joins the grades with each trajectory into `report.md` and `results.json`: resolved count and percentage, empty patches and errors listed by task, total and per-task cost, median calls and time, stop reason counts. Errors are reported separately, never as agent failures.

**Acceptance criteria:**
- [ ] Report maths on a saved set of trajectories and harness results gives the expected figures (unit test)
- [ ] `results.json` holds the per-task rows that `eval-compare` will need
- [ ] A harness error on one task shows up as an error, not as unresolved

**Verification:**
- [ ] Tests pass: `uv run pytest tests/test_eval_report.py`
- [ ] All checks pass

**Dependencies:** Tasks 2 and 9

**Files likely touched:** `src/coding_agent/eval/report.py`, `src/coding_agent/eval/runner.py`, `src/coding_agent/eval/grade.py`, `tests/test_eval_report.py`

**Estimated scope:** Medium

---

## Task 11: Fake-model runs over every task

**Description:** Two runs of the whole pipeline over the full task list, with no model cost: a fake model that writes each task's gold fix through `run_command`, and a fake model that does nothing. They check the pipeline and the lockdown flags on every task, not just one. A small command or test option selects the fake model, and it can't be used by accident in a normal run.

**Acceptance criteria:**
- [ ] Gold fixes through `run_command`: every task in the list resolves
- [ ] Do-nothing: every task is an empty patch, none is an error
- [ ] Any task that fails only inside the lockdown flags is fixed through M4's limits or excluded with a reason

**Verification:**
- [ ] All checks pass
- [ ] Manual check: both runs' `report.md` (slow on the Mac; may run on x86)

**Dependencies:** Task 10

**Files likely touched:** `src/coding_agent/eval/runner.py`, `tests/test_eval_runner.py`, `evals/exclusions.txt`

**Estimated scope:** Small

---

## Task 12: Live runs and the README (user)

**Description:** A live test on 3 tasks with all tools and a low call cap (`--live` only). Then the user's first full Haiku 5.5 run on Verified Mini within the `--max-cost` budget. The README gains an eval section: install the extra, build or pull images, the gold check, a run, resume, reading the report, the known limitation about other runs during an eval.

**Acceptance criteria:**
- [ ] `uv run pytest` skips the live eval test; `uv run pytest --live` runs it (user only)
- [ ] The full run writes `report.md` within the budget
- [ ] A run killed mid-way (Ctrl+C, or the process killed) resumes and skips finished tasks

**Verification:**
- [ ] All checks pass
- [ ] The user runs the live test and the full run, and the First acceptance tests are checked off in `SPEC-Phase1.md`

**Dependencies:** Task 11

**Files likely touched:** `tests/test_live.py`, `README.md`, `CLAUDE.md` (§ Commands), `SPEC-Phase1.md` (acceptance boxes)

**Estimated scope:** Small

---

## Checkpoint C: every First acceptance test checked off; review with the user; open the PR

- [ ] Every **First** acceptance test in `SPEC-Phase1.md` checked off
- [ ] Review the branch with the user
- [ ] Open the PR

---

## Phase D: Later

These follow the first real run. Each gets full acceptance criteria when it starts, using what the first run showed.

### Task 13: Pre-send context estimate (A2)

Before each request, estimate its size (last input total + output tokens + new tool-result characters / 3.5) and stop with `context limit` without sending if over the limit. Acceptance: no request in any trajectory goes over 100,000 input tokens, and the report says so. **Dependencies:** Task 9.

### Task 14: Per-run labels (R1)

Each container gets `coding-agent.run=<run id>` and the runner's process id; S7 removes leftovers only from runs whose process has exited. Acceptance: two eval workers in parallel don't remove each other's containers (Docker test); the known limitation is removed from the spec. **Dependencies:** Task 8.

### Task 15: Trials (A6)

`--trials k`: `predictions-<trial>.jsonl` and `trajectories/<trial>/` per trial, each graded with `--run_id <run id>-t<trial>`; the report adds the average and the share solved in all k trials. **Dependencies:** Task 10.

### Task 16: `eval-compare`

`coding-agent eval-compare <run A> <run B>` prints both summaries side by side and the tasks that flipped between resolved and unresolved. Acceptance: the flipped tasks are listed correctly for two saved `results.json` files. **Dependencies:** Task 10.

### Task 17: mini-swe-agent baseline (user)

Run mini-swe-agent once on the same tasks and model and record its resolved count and cost in the first report. **Dependencies:** Task 12.

### Task 18: A full run on AWS Spot (user)

A persistent Spot `c7a.2xlarge` with interruption behaviour `stop` and a 100 GB disk, prebuilt x86 images; a README runbook. Acceptance: the run completes using prebuilt images. **Dependencies:** Task 12.

## Checkpoint D: every acceptance test in `SPEC-Phase1.md` checked off; review with the user
