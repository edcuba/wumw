# CLAUDE.md

## Project

`wumw` — a shell command wrapper that compresses tool output before it enters LLM context. Goal: reduce token churn in agentic coding sessions.

See `wumw.md` for full spec.

## Current phase

Phase 1: instrumentation. Build passthrough wrapper that logs command + output size to JSONL. No compression yet.

## Conventions

- Language: Python (CLI entry point via `wumw` in PATH)
- Logs: `~/.wumw/sessions/<session_id>.jsonl`
- Keep it simple — avoid abstractions until Phase 2

## Monorepo layout

```
wumw/
  .venv/          # Python venv (not in git)
  src/            # wumw package source
  benchmarks/     # cloned benchmark repos (not in git)
  tests/
```

## Setup

Always use the local venv:
```bash
source .venv/bin/activate
```

If `.venv` doesn't exist, create it:
```bash
python3 -m venv .venv && source .venv/bin/activate
```

## Task tracking

See `tasklist.md` for current implementation tasks.

## Agent loop

See `loop.md`. Run with: `cat loop.md | claude` (or `claude < loop.md`)

## Key decisions

- Wrapper, not pipe — model explicitly calls `wumw rg` instead of `rg`
- Model decides when to use `wumw`, not the shell
- `--full` flag bypasses compression (Phase 2)
- No ML, no AST compression in Phase 1 or 2
