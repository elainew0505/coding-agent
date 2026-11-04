# Phase 1: evaluation detailed requirement

Phase 1 adds `coding-agent eval`: it runs the agent on the 50 tasks of SWE-bench Verified Mini, each inside that task's own SWE-bench Docker image, collects one patch per task, grades the patches with the official SWE-bench harness, and reports pass rate, cost and calls per run. It is done when the pipeline grades the official fixes as 50 of 50, a full Haiku 5.5 run finishes within budget, and two runs can be compared task by task.

The design follows mini-swe-agent's SWE-bench runner: the repo is baked into the image at `/testbed` and the result is a `git diff`. The agent loop, its tools (`run_command` and the file tools, `SPEC.md` § File tools) and the lockdown rules are the MVP's; only how code gets in and how results come out change.

## At a glance

The whole idea fits in five steps. For each of 50 practice tasks:

1. Start a container that already holds a project with a known bug
2. Let the agent try to fix it, with the same agent as the MVP
3. Save what it changed as a patch

Then:

4. Hand all 50 patches to SWE-bench's official grader, which runs hidden tests
5. Print a scorecard: how many it fixed, what it cost, how many steps it took

Every other rule protects the result from one specific way it could be wrong or expensive. The rules are grouped by what they protect:

| Group | Protects against |
| --- | --- |
| 1 · The core | Nothing; this is the five steps themselves, built as a mode of the MVP's agent |
| 2 · Protecting the score | A score that silently lies: leaked answers, build junk, broken test setup, wrong instructions |
| 3 · Controlling cost | Money wasted on price steps, runaway bugs, oversized runs and crashes |
| 4 · Runs not interfering | Runs deleting each other's containers, half-recorded tasks |
| 5 · Fair comparisons | Lucky runs that look like improvements |

Each rule is tagged **First** (needed for the first trustworthy numbers) or **Later** (after the first real run).

## Scope

**In scope**

- `coding-agent eval` over [SWE-bench Verified Mini](https://huggingface.co/datasets/MariusHobbhahn/swe-bench-verified-mini): 50 tasks with the same difficulty spread as the 500-task Verified set, about 5 GB of images instead of 130 GB
- One SWE-bench Docker image per task, with the repo at `/testbed` and its dependencies installed
- Claude Haiku 5.5 by default, `--model` to compare another model
- Grading by the official SWE-bench harness, never a hand-written grader
- One trajectory per task, a predictions file, and a summary report per run
- Comparing two runs task by task, including all tools against `--bash-only` (T1)
- One reference run of mini-swe-agent on the same tasks and model
- Runs on your Mac for debugging, on a rented x86 machine for full runs

**Out of scope (later)**

- Full Verified, Lite or Pro (once Verified Mini stops telling versions apart)
- Model-graded code quality; custom task sets from your own repos
- Running many tasks across several machines

## How an eval run works

1. **Select tasks:** load the dataset at a pinned revision; `--instances` or `--limit N` picks a subset.
2. **Prepare images:** pull each task's image, or build it locally on Apple Silicon; reuse cached images.
3. **Start a container** per task from its image, with the MVP's lockdown flags and a per-run label; working folder `/testbed`.
4. **Baseline:** the harness removes the repo's history after the starting commit (E5), then snapshots the repo's own files in `/harness.git` (M7).
5. **Loop:** the agent gets the issue text through the eval prompt and works with `run_command` until it stops, hits the call cap or the context limit.
6. **Diff out:** the harness takes `git diff --cached --binary <baseline>`; an empty diff is still recorded.
7. **Record:** one line `{instance_id, model_name_or_path, model_patch}` is appended to the trial's predictions file (A6) right away, so a stopped run can resume; the trajectory is saved and the container removed.
8. **Grade:** after all tasks, the official harness runs on each predictions file and decides which tasks are resolved.
9. **Report:** grades are joined with each trajectory's calls, tokens, cost and time into one summary.

## Group 1 · The core

The five steps, built as a mode of the MVP's agent so eval measures the agent you actually use. Everything in this group is **First**.

### One agent, two modes

The MVP was built for one repo copied from your machine with a human reviewing the diff. Eval differs only at the two ends of a task, so a run is split into a **Setup**, a shared middle and a **Finish** ([0003](docs/decisions/0003-setup-and-finish-modes.md)):

```
             ┌──────── same for both modes ────────┐
Setup  ───▶  agent loop + run_command + sandbox  ───▶  Finish
  │                                                        │
  ├─ CopyInSetup   (MVP):  copy repo in, /workspace        ├─ ReviewAndApply    (MVP)
  └─ ImageSetup    (eval): use task image, /testbed,       └─ RecordPrediction  (eval)
                          clean git history (E5)
```

The middle is used as it is: the loop (L1–L8), `run_command` and its output cap, kill-on-stop, the lockdown flags (S2, S3) and container removal (S6). CopyInSetup and ReviewAndApply are today's code moved behind the two seams, with no change in behaviour; the MVP's tests must pass unchanged.

**Setup.** A Setup gives the shared middle everything that differs between modes, then gets the code in and takes the baseline. Each value is fixed for the whole run, so `system` and `tools` stay byte-identical across turns. M3, M5 and M7 are Setup values too; they are in Group 2 because they protect the score.

| ID | Setup provides | CopyInSetup (MVP) | ImageSetup (eval) | Build |
| --- | --- | --- | --- | --- |
| M1 | Image | `config.IMAGE` (S1) | The task's SWE-bench image (E2); S8 checks it exists | First |
| M2 | Working folder, used by the sandbox, tool description and system prompt | `/workspace` | `/testbed` | First |
| M4 | Limits: memory, default command timeout | 2 GB, 60 s | 4 GB, 120 s, adjusted after the first runs | First |
| M6 | Code in | Copy-in (S4) with the deny list (S9); S8 checks for a git repo | Nothing: the code is already in the image, and no host files enter the container, so S4, S9 and the git-repo check are skipped | First |

**Finish.** A Finish takes the diff (D1) and the run's stop reason, whatever ended the task (A5).

| ID |  | ReviewAndApply (MVP) | RecordPrediction (eval) | Build |
| --- | --- | --- | --- | --- |
| F1 | What happens to the diff | D2–D5: show it, ask apply/discard, `git apply` | No prompt: append `{instance_id, model_name_or_path, model_patch}` to the trial's predictions file (A6) right away; an empty diff is recorded as an empty patch | First |
| F2 | Where the trajectory goes | `.agent/trajectories/<timestamp>.json` in your repo | `runs/<run id>/trajectories/<trial>/<instance_id>.json`, with model and settings recorded | First |

### Runner, dataset and agent settings

`eval/runner.py` runs many tasks, one after another, each as ImageSetup → shared middle → RecordPrediction. What only matters across many tasks lives in the runner, not in a mode (its other rules, R1–R5, are in Groups 3 and 4).

| ID | Requirement | Build |
| --- | --- | --- |
| E1 | Dataset `MariusHobbhahn/swe-bench-verified-mini`, split `test`, pinned to one revision recorded in each run's metadata | First |
| E2 | Images: on x86 machines pull prebuilt SWE-bench images; on Apple Silicon build them locally (`--namespace ''`) or try Epoch AI's best-effort ARM images. Cache them between runs | First |
| A1 | Model `claude-haiku-5-5` by default; `--model` for a comparison run. Effort `medium`, prompt caching on, as in the MVP | First |
| A3 | Call cap 50 per task to start; lower it once the cost curve from the first runs shows where extra calls stop helping | First |
| A5 | Every stop reason (done, call cap, context limit, refusal, API error) still records whatever diff exists, so the task is graded rather than lost | First |

### Grading

- Only the official harness grades: `python -m swebench.harness.run_evaluation --dataset_name <E1> --predictions_path <run>/predictions-<trial>.jsonl --run_id <run id>-t<trial>` (plus `--namespace ''` on Apple Silicon).
- The tests that decide a task are added only at grading time, in a fresh container, so the agent never sees them.
- The harness version is recorded in each run's metadata.
- Each task ends as one of: resolved, unresolved, empty patch, or error. Errors are reported separately and never counted as failures of the agent.

## Group 2 · Protecting the score

The most important group: each rule stops the score from silently measuring the wrong thing. Everything here is **First**, because a run without them can't be trusted.

| ID | Rule | The problem it prevents | Build |
| --- | --- | --- | --- |
| E5 | **Clean git history.** `/testbed/.git` is the repo's real history, and some images keep later commits (other branches, tags, reflog) that contain the fix. Before the baseline, ImageSetup keeps only the checked-out commit and its ancestors: it deletes every other branch, tag and remote ref and any stash, runs `git reflog expire --expire=now --all`, then `git gc --prune=now` | The model finds the real fix in hidden history and copies it | First |
| M7 | **Baseline of the repo's own files.** First clean the history (E5), then add exactly the repo's own files to `/harness.git`, listed with `git -C /testbed ls-files -z`, never `git add -A --force`. Build files already in the image that `.gitignore` excludes (`.pyc`, `*.egg-info`, built `.so` files) stay out of the baseline. (MVP: S5, `git add -A --force` over everything copied in) | Rebuilt build files become binary changes in the patch and stop it applying | First |
| M3 | **Conda environment.** Each command runs as `bash -lc <command>` with the image's conda environment `testbed` active; SWE-bench images keep the repo's Python packages there. Check how the images activate it when implementing. (MVP: `bash -lc <command>`) | Tests fail for setup reasons, not because the agent was wrong | First |
| M5 | **True instructions.** The eval prompt (A4) and an eval tool description that names `/testbed` and replaces the MVP's "not a git repo" lines: `/testbed` is a git repo at the task's starting commit; `git diff`, `git log` and `git status` are fine to use; don't commit, stash or check out, because the files on disk are what gets graded. The file tools' descriptions name no folder (paths are relative to the working folder), so they are shared by both modes. The input schemas stay the same; each `run_command` description has its own test that the schema matches `RunCommandInput` | Wrong instructions make the agent waste steps and work worse | First |
| A4 | **Eval prompt** in `prompts.py`: the issue text; the repo is at `/testbed` with no network; reproduce the problem first; change source files only, never tests or configuration; run the relevant tests; delete any scripts it wrote (e.g. `reproduce.py`) before finishing, since every new file in `/testbed` ends up in the patch; finish with a short summary | The agent edits tests to pass, or leaves scratch files in the patch | First |
| E3 | **Gold check.** Before the first model run, grade the dataset's own fixes (`--predictions_path gold`). Every task must resolve; any that doesn't is listed in an exclusion file with the reason, and left out of all runs | Blaming the agent for a task that is broken | First |
| E4 | **Same task list everywhere.** Tasks whose image fails to build are excluded the same way, so every run uses the same task list | Runs scored on different tasks look comparable when they aren't | First |

The repo's own `.git` folder in `/testbed` doesn't affect the harness: git never adds a folder named `.git` to a snapshot, and the harness diffs against `/harness.git`.

The proof that this group works is the fake-model test (Testing): a fake model writes the gold fix into `/testbed` with `run_command`, and the harness must resolve it.

## Group 3 · Controlling cost

| ID | Rule | The problem it prevents | Build |
| --- | --- | --- | --- |
| A2 | **Context limit** 90,000 input tokens for Haiku 5.5 (below its 100,000-token price step), counted as in L5. It is checked before each request is sent, not only after the response: the estimate is the last response's input total, plus its output tokens, plus the characters of the new tool results divided by 3.5. If the estimate is over the limit, the task stops with stop reason `context limit` without sending, so no request can cross the price step after one turn with many large tool results | Requests crossing Haiku's fivefold price step | First: L5's after-response check. Later: the pre-send estimate |
| R2 | **Context limit per model**, in `config.py`: 90,000 for Haiku 5.5 (A2), the MVP's 150,000 otherwise | One limit that's wrong for some models | First |
| R3 | **Price table** per model in `config.py`; cost per task in the trajectory and the report, and the `--max-cost` guard (cost rule 5) | Not knowing what a run cost; a bug burning the budget | First |
| R4 | **Resume:** on start, skip every task already in the trial's predictions file | Paying again from the start after a crash | First |

**Cost rules** (all First):

1. Haiku 5.5 with prompt caching and the 90,000-token limit (A1, A2).
2. The gold check first (E3): it tests images and grading without any model cost.
3. Debug on 3–5 tasks (`--limit 5`); run all 50 only to compare two versions.
4. One trial per task until two versions score within a few tasks of each other.
5. `--max-cost` (default $5): no new task starts once the estimated spend reaches it; the run can be resumed with a higher limit.
6. The Message Batches API is not used: each agent step waits for the previous answer.

## Group 4 · Runs not interfering

| ID | Rule | The problem it prevents | Build |
| --- | --- | --- | --- |
| R5 | **Clean Ctrl+C:** stops the current task (L8), removes its container and ends the run. The interrupted task is not recorded, so a resumed run starts it again; finished tasks stay in the predictions files | Leftover containers; a half-finished task recorded as a result | First |
| R1 | **Per-run labels:** every container gets `coding-agent=1` plus `coding-agent.run=<run id>` and the runner's process id. On startup S7 removes leftovers only from runs whose process has exited, so parallel eval workers, or an eval next to a normal run, never remove each other's containers | Two runs deleting each other's containers | Later: needed once runs happen in parallel |

## Group 5 · Fair comparisons

| ID | Rule | The problem it prevents | Build |
| --- | --- | --- | --- |
| A6 | **Trials:** `--trials k` runs each task k times in fresh containers; 1 by default. The harness accepts one prediction per `instance_id` per file, so each trial has its own `predictions-<trial>.jsonl` and `trajectories/<trial>/`, and is graded separately with `--run_id <run id>-t<trial>`; the report combines the k results | One lucky run looking like an improvement | Later (file naming per trial is First) |
| — | **Baseline:** run mini-swe-agent once on the same 50 tasks with the same model and record its resolved count and cost in the first report | Not knowing whether a gap comes from the harness or the model | Later |
| — | **`coding-agent eval-compare <run A> <run B>`:** prints both summaries side by side and the tasks that flipped between resolved and unresolved. With 50 tasks, a difference of two or three tasks can be noise; repeat with `--trials` before trusting it | Seeing totals without knowing which tasks changed | Later |

**Metrics and report.** Per task, taken from the trajectory and the harness result: outcome (resolved, unresolved, empty patch or error); model calls; input tokens (fresh, cache read, cache write) and output tokens; cost from the price table, wall time, stop reason, patch size in lines.

Per run, in `report.md` and `results.json`:

| Field | Meaning | Build |
| --- | --- | --- |
| Resolved | Count and percentage of the task list (pass@1; with `--trials k`, the average and the share solved in all k trials) | First (trial figures Later) |
| Empty patches, errors | Counts, listed by task | First |
| Cost | Total and per task | First |
| Calls, time | Median per task | First |
| Stop reasons | Count of each | First |

## Reference

### Where to run

The model runs in Anthropic's cloud, so the machine only runs containers and tests; no GPU is needed. The SWE-bench harness recommends 16 GB of RAM and 8 or more cores, and at least 120 GB of free disk for the full sets (Verified Mini needs far less).

| Option | Cost | Speed | Use for | Build |
| --- | --- | --- | --- | --- |
| Your Mac (Apple Silicon) | Free | Slow: x86 images run under emulation unless built locally for ARM with `--namespace ''` | The gold check, debugging on 3–5 tasks | First |
| AWS EC2 `c7a.2xlarge` (8 vCPU, 16 GB, x86), Spot | About $0.16–0.19 an hour in US regions (about $0.41 on demand) | Fast: prebuilt x86 images, no builds | Full 50-task runs | Later |
| Hetzner Cloud | Hourly billing, but dedicated-CPU prices rose by up to 176% in June 2026; check current prices | Fast | Alternative to AWS | Later |

**Recommendation:** debug on the Mac, then do full runs on an AWS Spot `c7a.2xlarge` with a 100 GB disk, stopped as soon as the run ends. A full Verified Mini run takes a few hours one task at a time, so the machine costs about a dollar, less than the model. Spot machines can be taken back without warning, which is one more reason runs must resume from their predictions files. By default AWS terminates a reclaimed Spot instance and deletes its disk, which would lose `runs/`. So launch it as a persistent Spot request with interruption behaviour `stop`: the disk, the run folder and the cached images survive, and you start the machine again and rerun the same command to resume.

### Output files

Everything for a run lives in `runs/<run id>/` in the coding-agent repo (git-ignored):

```
runs/<run id>/
  meta.json                  # model, tool set (T1), settings, agent git commit, dataset revision, harness version, task list
  predictions-<trial>.jsonl  # one line per task, appended as each finishes (trial 1 with the default --trials 1)
  trajectories/<trial>/      # <instance_id>.json, same format as the MVP trajectory
  harness/                   # the official harness's logs and results
  report.md                  # the summary
  results.json               # the summary, for eval-compare
```

### Project layout

New files next to the MVP's:

```
src/coding_agent/
  modes.py         # the Setup and Finish interfaces; CopyInSetup and ReviewAndApply (MVP code moved here)
  eval/
    __init__.py
    dataset.py     # load the dataset, apply the exclusion list
    images.py      # pull or build task images
    mode.py        # ImageSetup and RecordPrediction
    runner.py      # the task loop: labels, resume, budget guard, Ctrl+C (R1–R5)
    grade.py       # call the official harness, read its results
    report.py      # report.md, results.json, eval-compare
  prompts.py       # + the eval prompt
  config.py        # + eval settings, price table, per-model context limits
evals/
  exclusions.txt   # tasks left out, with reasons
```

New dependencies: `swebench` and `datasets`, as an optional `eval` extra (`[project.optional-dependencies]`), installed with `uv sync --extra eval`. The `coding-agent` command alone still depends only on `anthropic` and `pydantic`, and only `coding_agent.eval` imports them. Both are pinned in `uv.lock` and pass the `security-and-hardening` supply-chain check before they are added; CLAUDE.md § Stack is updated in the same commit.

## Testing

The same three levels as the MVP:

| Level | What it uses | Covers (First) | Covers (Later) |
| --- | --- | --- | --- |
| Unit | Fake client, fake sandbox | Prediction format, resume, the budget guard, report maths, every stop reason recording a diff, the MVP's tests passing unchanged through CopyInSetup and ReviewAndApply | A2's pre-send estimate, trial results combined, `eval-compare` |
| Docker | One real task image (a small, fast instance) and a fake client | M1–M7, E5, R5: ImageSetup, `/testbed`, the conda environment, limits, clean history, clean stop | R1: per-run labels, two workers in parallel |
| Live | The real API on 3 tasks with a low call cap; `--live` only | That a real model can finish a task end to end | — |

The key Docker test: a fake model that writes the gold fix into `/testbed` with `run_command`. If the harness resolves it, the pipeline's own diff extraction is correct, not just the harness.

## Acceptance tests

Phase 1 passes when every item holds. The **First** items are the first version; the rest follow after the first real run.

**First**

- [ ] Gold check: every task in the list resolves; exclusions are written down with reasons (Group 2)
- [ ] Fake model writing the gold fixes through `run_command`: every task resolves through the pipeline (Group 2)
- [ ] Fake model doing nothing: every task is an empty patch, none is an error (Group 1)
- [ ] After the baseline, `git -C /testbed log --all` and `git -C /testbed fsck --unreachable` show no commit newer than the task's starting commit (Group 2)
- [ ] Gold fixes still resolve inside the lockdown flags (`--network none`, limits) (Group 2)
- [ ] A full Haiku 5.5 run on Verified Mini finishes within the `--max-cost` budget and writes `report.md` (Group 3)
- [ ] A run stopped with Ctrl+C, or by killing the machine, resumes and skips finished tasks (Group 3)
- [ ] After Ctrl+C, no container from the run is left running (Group 4)

**Later**

- [ ] No request in any trajectory goes over 100,000 input tokens (Group 3; needs A2's pre-send estimate)
- [ ] Two eval workers in parallel don't remove each other's containers (Group 4)
- [ ] `eval-compare` lists the tasks that flipped between two runs (Group 5)
- [ ] The mini-swe-agent baseline is recorded in the first report (Group 5)
- [ ] A full run on an AWS Spot `c7a.2xlarge` completes using prebuilt images (Reference)

## Known limitations

| Limitation | Effect | Response |
| --- | --- | --- |
| Verified is saturated and partly memorized by models | Absolute scores mean little | Compare runs with each other, never with leaderboards; move to SWE-bench Pro when Verified Mini stops separating versions |
| 50 tasks is a small sample | Small differences are noise | Use `--trials` when two versions are close |
| Python only, all bug fixes from GitHub issues | Says little about other languages or kinds of work | Add custom tasks later |
| x86 images on Apple Silicon | Slow, and some ARM builds may fail | Full runs on x86; failed builds go to the exclusion list |
| Until R1 is built, any run's startup (S7) removes every `coding-agent=1` container | Starting a normal `coding-agent` task or a second eval while an eval runs kills the eval's current container; that task is recorded with an empty or partial patch and scored unresolved | Don't run anything else with `coding-agent` while an eval runs; rerun a run that was disturbed |
