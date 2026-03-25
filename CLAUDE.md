# CLAUDE.md

## Project

`wumw` — a shell command wrapper that compresses tool output before it enters LLM context. Goal: reduce token churn in agentic coding sessions.

See `wumw.md` for full spec.

## Current phase

**Experiments complete (E001–E021).** Compression implemented, validated, and tuned. Ready for use.

## What's been built

- `wumw <cmd> [args...]` — passthrough wrapper; model opts in by prefixing commands
- Compressors:
  - `cat` — Python files: class/def outline with line numbers + `sed` navigation hint; other files: first 100 lines + `tail` pagination hint
  - `rg`/`grep` — cap 5 matches/file, deduplicate, limit context lines
  - `git diff` — strip index metadata lines
  - `git log` — cap 20 entries
  - generic fallback — collapse repeated lines, truncate at 200 lines
- `--full` flag — bypasses compression, logged
- Compression header: `# wumw: N → M lines` prepended when output is reduced
- `wumw-analyze` — reads JSONL logs, reports bytes by command, re-read frequency, `--full` rate
- `wumw-bench` — runs commands with/without wumw, prints compression ratio table
- 74 passing tests (`tests/test_wumw.py`)

## Conventions

- Language: Python (CLI entry point via `wumw` in PATH)
- Logs: `.wumw/sessions/<session_id>.jsonl` (repo-local, not in git)
- Session ID: `.wumw/session` (repo-local, or `WUMW_SESSION` env var)
- **Do not read or write files outside this repository.**

## Monorepo layout

```
wumw/
  .venv/              # Python venv (not in git)
  src/wumw/
    cli.py            # entry point, passthrough + logging
    compress.py       # per-command compressors
    analyze.py        # JSONL log analysis
    benchmark.py      # wumw-bench comparison tool
  tests/
    test_wumw.py      # 74 tests
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
Picks tasks from `tasklist.md`. All Phase 1+2 tasks complete.

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
- Repo-local state only (`.wumw/`) — no writes to home dir
