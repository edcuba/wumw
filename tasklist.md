# Task List

## Phase 1 — Instrumentation

- [ ] Set up Python project (`pyproject.toml`, entry point `wumw`)
- [ ] Implement passthrough: run command, capture stdout/stderr, print unchanged
- [ ] Session ID: read from `WUMW_SESSION` env var or generate UUID on first run, persist in `~/.wumw/session`
- [ ] Log per invocation to `~/.wumw/sessions/<session_id>.jsonl`
  - fields: `timestamp`, `session_id`, `command`, `args`, `stdout_bytes`, `stdout_lines`, `stderr_bytes`, `exit_code`
- [ ] CLI: `wumw <command> [args...]`
- [ ] Analysis script: read JSONL, report token spend by command type, re-read frequency

## Phase 2 — Compression

- [ ] Per-command compressor interface
- [ ] `rg`/`grep`: cap matches per file, deduplicate, limit context lines
- [ ] `cat`: strip blank lines + comments, truncate past threshold
- [ ] `git diff`: strip metadata noise, keep hunks
- [ ] `git log`: limit entries
- [ ] Generic: collapse repeated lines, truncate tail
- [ ] Compression header: `# wumw: 1240 → 312 lines`
- [ ] `--full` flag: bypass compression, log that it was used

## Phase 3 — Feedback loop

- [ ] Track `--full` usage per session → compression quality signal
- [ ] Benchmark harness: run task with/without `wumw`, compare token counts + task success
