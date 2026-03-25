# wumw — Why Use Many Words

A shell command wrapper that compresses tool output before it enters LLM context, reducing token churn in agentic coding sessions.

The model opts in by prefixing commands with `wumw`. Output is compressed and a `# wumw: N → M lines` header is prepended when reduction occurs.

In a representative coding session, `wumw` cut tool output by about 54% overall, roughly halving token spend. The biggest savings came from large file reads and noisy searches; `git diff` usually changed much less because the hunks are still preserved.

## What it does

| Command | Compression strategy |
|---|---|
| `wumw cat file.py` | Python files: emit class/def outline with line numbers; other files: first 100 lines + `tail` hint |
| `wumw rg pattern src/` | Cap 5 matches/file, deduplicate, limit context lines |
| `wumw git diff` | Strip index metadata lines |
| `wumw git log` | Cap at 20 entries |
| anything else | Collapse repeated lines, truncate at 200 lines |

`wumw --full <cmd>` bypasses compression and logs the bypass.
The built-in thresholds are runtime-configurable via env vars such as `WUMW_RG_CAP`, `WUMW_CAT_LINES`, `WUMW_GIT_LOG_ENTRIES`, `WUMW_LISTING_MAX_ENTRIES`, `WUMW_GENERIC_LINES`, and `WUMW_HEADER_MIN_SAVED`.

When running under Codex, `wumw` uses `CODEX_THREAD_ID` automatically so each Codex thread gets its own savings bucket. Outside Codex, it auto-rotates its session id after 30 minutes of inactivity so reports map more closely to distinct coding sessions. Override either mode with `WUMW_SESSION`, or tune the fallback idle split with `WUMW_SESSION_IDLE_TIMEOUT_SECONDS`.

## Install

```bash
git clone git@github.com:edcuba/wumw.git
cd wumw
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Usage

```bash
# use wumw for commands that tend to produce noisy output
wumw cat src/main.py
wumw rg "TODO" src/
wumw git log --oneline
wumw git diff HEAD~1

# bypass compression when you need the full output
wumw --full cat src/bigfile.py

# analyze session logs
wumw-analyze

# estimate token savings from those logs
wumw-savings
wumw-savings --by-session --by-day

# benchmark compression ratio on a codebase
wumw-bench
```

Use `wumw` selectively, not as a blanket prefix for every command.

- Prefer `wumw cat`, `wumw rg`, `wumw git diff`, and `wumw git log` when output may be large, repetitive, or mostly navigational.
- Skip `wumw` for short exact reads where compression adds no value.
- For Python files, start with `wumw cat file.py`, then jump to exact sections with `sed -n 'START,ENDp' file.py`.
- If compression hides needed detail, rerun the same command with `wumw --full ...`.
- In sandboxes or read-only environments, set `WUMW_HOME` to a writable directory such as `/tmp/wumw`.

Threshold overrides are read from the environment on each invocation, for example:

```bash
WUMW_RG_CAP=8 WUMW_RG_CONTEXT_LINES=1 wumw rg TODO src/
WUMW_CAT_LINES=60 wumw cat src/main.py
WUMW_GIT_LOG_ENTRIES=50 wumw git log --oneline
```

## Use with Claude Code

Add to your project's `.claude/settings.json` to let Claude use wumw automatically:

```json
{
  "env": {
    "PATH": "/path/to/wumw/.venv/bin:${PATH}"
  },
  "permissions": {
    "allow": [
      "Bash(wumw:*)"
    ]
  }
}
```

Then instruct Claude in `CLAUDE.md`:

```markdown
Prefer `wumw cat`, `wumw rg`, and `wumw git diff` / `wumw git log` when output is likely to be large.
Do not force `wumw` onto every command; skip it for short exact reads.
For Python files, start with `wumw cat file.py`, then use `sed -n 'START,ENDp' file.py` for exact sections.
If compression hides needed detail, rerun with `wumw --full ...`.
```

Claude will prefix commands with `wumw` and navigate Python files via `sed -n 'N,Mp'` using the outline hints.

## Use with Codex (OpenAI)

Add to your repo's `AGENTS.md`:

```markdown
## Tool usage
Use `wumw` selectively for large file reads and searches:
- `wumw cat file.py` when a file may be long
- `wumw rg pattern src/` when search output may be noisy
- `wumw git diff` / `wumw git log` when git output may be large

Do not force `wumw` onto every command; skip it for short exact reads.

For Python files, `wumw cat` returns a class/method outline with line numbers.
Use `sed -n 'START,ENDp' file.py` to read specific sections.
If compression hides needed detail, rerun with `wumw --full ...`.
If `wumw` cannot write session state in a sandbox, set `WUMW_HOME` to a writable directory such as `/tmp/wumw`.
```

## Session logs

wumw logs every invocation to `.wumw/sessions/<session_id>.jsonl` (gitignored).
Each entry includes the session id, session start time, cwd, and repo context so savings can be grouped later without timestamp forensics.

```bash
wumw-analyze              # summary: bytes by command, re-read rate, --full rate
wumw-savings              # estimated lines/bytes/tokens saved from logged sessions
wumw-savings --session X  # same estimate, filtered to one session
wumw-savings --by-session --by-day
wumw-savings --since 2026-03-25T00:00:00+00:00 --by-session
```

## Benchmark

```bash
wumw-bench                # runs commands with/without wumw, prints compression ratio table
```

Repeated A/B notes for a real-world PR review task are in `pr_review_benchmark.md`.

## Codex Task Loop

Use the task loop when you want Codex to pick exactly one backlog item from `tasklist.md`, implement it, commit it, and then stop so the next loop iteration can reassess repo state.

Prerequisites:

- `codex` CLI installed and authenticated
- repo dependencies installed if the selected task needs them
- clean working tree before starting the loop

Run a few iterations like this:

```bash
./run_task_loop.sh 5
```

What it does:

- reads the agent instructions from `loop.md`
- checks `git status` first for partial work recovery
- picks the highest-priority incomplete task from `tasklist.md`
- runs one task per Codex invocation, then stops when all tasks are done or the iteration cap is reached

Useful overrides:

```bash
WUMW_MODEL=gpt-5.4 ./run_task_loop.sh 5
LOOP_PROMPT=loop.md ./run_task_loop.sh 5
LOG_DIR=/tmp/wumw-loop ./run_task_loop.sh 5
```

To inspect the next queued task without starting Codex:

```bash
wumw-task-status tasklist.md
```

Each run writes a timestamped log to `logs/task_loop_*.log`, plus per-iteration JSONL and final-message snapshots that make it easier to inspect failures or interrupted work.
