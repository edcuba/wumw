# Task List

## Phase 1 — Instrumentation

- [x] Set up Python project (`pyproject.toml`, entry point `wumw`)
- [x] Implement passthrough: run command, capture stdout/stderr, print unchanged
- [x] Session ID: read from `WUMW_SESSION` env var or generate UUID on first run, persist in `~/.wumw/session`
- [x] Log per invocation to `~/.wumw/sessions/<session_id>.jsonl`
  - fields: `timestamp`, `session_id`, `command`, `args`, `stdout_bytes`, `stdout_lines`, `stderr_bytes`, `exit_code`
- [x] CLI: `wumw <command> [args...]`
- [x] Analysis script: read JSONL, report token spend by command type, re-read frequency
- [x] Fix all paths: use repo-local `.wumw/` instead of `~/.wumw/` everywhere in source code

## Phase 2 — Compression

- [x] Per-command compressor interface
- [x] `rg`/`grep`: cap matches per file, deduplicate, limit context lines
- [x] `cat`: strip blank lines + comments, truncate past threshold
- [x] `git diff`: strip metadata noise, keep hunks
- [ ] `git log`: limit entries
- [ ] Generic: collapse repeated lines, truncate tail
- [ ] Compression header: `# wumw: 1240 → 312 lines`
- [ ] `--full` flag: bypass compression, log that it was used

## Phase 3 — Feedback loop

- [ ] Track `--full` usage per session → compression quality signal
- [ ] Benchmark harness: run task with/without `wumw`, compare token counts + task success
