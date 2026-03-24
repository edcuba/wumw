# Task List

## Phase 1 — Instrumentation

- [x] Set up Python project (`pyproject.toml`, entry point `wumw`)
- [x] Implement passthrough: run command, capture stdout/stderr, print unchanged
- [x] Session ID: read from `WUMW_SESSION` env var or generate UUID on first run, persist in `.wumw/session`
- [x] Log per invocation to `.wumw/sessions/<session_id>.jsonl`
- [x] CLI: `wumw <command> [args...]`
- [x] Analysis script: read JSONL, report token spend by command type, re-read frequency
- [x] Fix all paths: use repo-local `.wumw/` instead of `~/.wumw/`

## Phase 2 — Compression

- [x] Per-command compressor interface
- [x] `rg`/`grep`: cap matches per file, deduplicate, limit context lines
- [x] `cat`: strip blank lines + comments, truncate past threshold
- [x] `git diff`: strip metadata noise, keep hunks
- [x] `git log`: limit entries
- [x] Generic: collapse repeated lines, truncate tail
- [x] Compression header: `# wumw: N → M lines`
- [x] `--full` flag: bypass compression, log that it was used

## Phase 3 — Feedback loop

- [x] Track `--full` usage per session → compression quality signal
- [x] Benchmark harness: run task with/without `wumw`, compare token counts + task success

## Phase 4 — Compressor improvements (driven by experiment results)

- [ ] Token estimation: add `estimated_tokens` field to JSONL log (use tiktoken or simple word/4 heuristic)
- [ ] `fd`/`find`/`ls` compressor: deduplicate, group by extension, cap at N entries
- [ ] Configurable thresholds via env vars (`WUMW_RG_CAP`, `WUMW_CAT_LINES`, etc.) — no hardcoded magic numbers
- [ ] Binary output detection: skip compression for non-text stdout
- [ ] Streaming output: compress line-by-line instead of buffering full output (needed for long-running commands)
- [ ] `wumw session new` subcommand: explicitly rotate to a fresh session ID

## Phase 5 — Testing improvements

- [ ] Edge cases: empty stdout, single line, binary-looking content, very large file (>10k lines)
- [ ] Integration test: actually invoke `wumw echo hello` as subprocess, verify JSONL written
- [ ] Test `wumw-analyze` output format with a known fixture JSONL
- [ ] Test `wumw-bench` runs and produces correct ratio table
- [ ] Fuzz compressors: random inputs shouldn't crash or produce more lines than input
