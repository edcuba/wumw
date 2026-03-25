# CLAUDE.md

## Project

`wumw` — a shell command wrapper that compresses tool output before it enters LLM context. Goal: reduce token churn in agentic coding sessions.

See `wumw.md` for full spec.

## Current phase

**Experiments complete (E001–E021).** Compression implemented, validated, and tuned. Ready for use.

## What's been built

- `wumw <cmd> [args...]` — passthrough wrapper; model opts in by prefixing commands
- Compressors:
  - `cat` — Python files: class/def/async-def outline with decorators, docstring hints, line numbers + `sed` navigation hint; other files: first 100 lines + `tail` pagination hint
  - `rg`/`grep` — cap 5 matches/file, deduplicate, per-file omission summary
  - `git diff` — strip index metadata lines, compress oversized unchanged hunk spans
  - `git log` — cap 20 entries
  - `fd`/`find`/`ls` — deduplicate and group large directory listings by extension
  - generic fallback — collapse repeated lines, truncate at 200 lines
- Binary-detection short-circuit: binary stdout is never compressed
- `--full` flag — bypasses compression, logged
- Compression header: `# wumw: N → M lines` prepended when output is reduced (configurable via `WUMW_HEADER_MIN_SAVED`)
- All thresholds runtime-configurable via env vars: `WUMW_RG_CAP`, `WUMW_CAT_LINES`, `WUMW_GIT_LOG_ENTRIES`, `WUMW_LISTING_MAX_ENTRIES`, `WUMW_GENERIC_LINES`, etc.
- `wumw-analyze` — reads JSONL logs, reports bytes by command, re-read frequency, `--full` rate
- `wumw-bench` — runs commands with/without wumw, prints compression ratio table
- `wumw-savings` — token savings estimate; supports `--by-session`, `--by-day`, `--since`, `--until`
- `wumw-task-status` — parses `tasklist.md`, emits JSON progress summary (used by `run_task_loop.sh`)
- 124 passing tests (`tests/test_wumw.py`, `tests/test_task_status.py`)

## Recommended usage

- Use `wumw` selectively for commands with potentially large or repetitive output: `cat`, `rg`, `git diff`, `git log`.
- Do not prefix every command with `wumw`; short exact reads are often better without compression.
- For Python files, start with `wumw cat file.py`, then navigate with `sed -n 'START,ENDp' file.py`.
- If compression hides detail, rerun the same command with `wumw --full ...`.
- In sandboxes or read-only environments, set `WUMW_HOME` to a writable directory such as `/tmp/wumw`.

## Conventions

- Language: Python (CLI entry point via `wumw` in PATH)
- Logs: `.wumw/sessions/<session_id>.jsonl` when repo-local state is writable; otherwise state can live under `WUMW_HOME` or XDG/user state.
- Session ID: `.wumw/session` stores a JSON record (session id, started_at, context_root); auto-rotates after 30 min idle (`WUMW_SESSION_IDLE_TIMEOUT_SECONDS`); `CODEX_THREAD_ID` env var is used automatically under Codex; `WUMW_SESSION` overrides both.

## Monorepo layout

```
wumw/
  .venv/              # Python venv (not in git)
  src/wumw/
    cli.py            # entry point, passthrough + logging
    compress.py       # per-command compressors
    state.py          # state dir resolution, session management
    session_logs.py   # JSONL log loading with filtering
    analyze.py        # JSONL log analysis
    savings.py        # token savings estimator
    benchmark.py      # wumw-bench comparison tool
    task_status.py    # tasklist.md parser / JSON status
  scripts/
    pr_review_ab.py   # A/B benchmark runner for PR review task
  tests/
    test_wumw.py      # main test suite (124 tests)
    test_task_status.py
    test_pr_review_ab.py
  benchmarks/         # cloned benchmark repos (not in git)
  logs/               # agent loop logs (not in git)
```

## Setup

```bash
source .venv/bin/activate
# or if .venv doesn't exist:
python3 -m venv .venv && source .venv/bin/activate && pip install -e .
```

## Loops

Two agent loops:

**Implementation loop** (`loop.md`):
```bash
LOOP_PROMPT=loop.md ./run_loop.sh 10
```
Picks tasks from `tasklist.md`.

**Codex task loop** (`loop.md`):
```bash
./run_task_loop.sh 10
```
Picks tasks from `tasklist.md` using Codex non-interactive runs.

**Experiment loop** (`experiment_loop.md`):
```bash
LOOP_PROMPT=experiment_loop.md ./run_loop.sh 10
```
Picks experiments from `experiments.md`. E001–E021 complete.

Monitor: `tail -f logs/loop_*.log`

## Key decisions

- Wrapper, not pipe — model explicitly calls `wumw rg` instead of `rg`
- Model decides when to use `wumw`, not the shell
- `--full` flag bypasses compression; high `--full` rate signals compressor needs tuning
- No ML, no AST compression in Phase 1 or 2
- Prefer repo-local state when writable; otherwise fall back to `WUMW_HOME` or XDG/user state
