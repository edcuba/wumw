# `wumw` — Spec

## What it is

A command wrapper that runs any shell command, captures its output, and applies compression before returning it — reducing token cost when the output is being consumed by an LLM.

```bash
wumw rg "pattern" src/
wumw cat bigfile.py
wumw git diff HEAD~1
```

---

## Phase 1: Instrumentation (measure first)

Before compression, `wumw` runs in **passthrough + log mode**.

Logs per invocation:
- command + args
- raw output byte size
- line count
- timestamp
- session ID (env var or auto-generated)

Output: JSONL to `~/.wumw/sessions/<id>.jsonl`

Goal: run a few real coding tasks, then analyze which commands produce the most tokens, how often files are re-read, and where the ceiling on savings is.

---

## Phase 2: Compression

Per-command strategies:

| Command | Strategy |
|---|---|
| `rg` / `grep` | cap matches per file, deduplicate, show only N context lines |
| `cat` / `read` | strip blank lines + comments, truncate past threshold |
| `git diff` | keep hunks, strip metadata noise |
| `git log` | limit entries, strip hash verbosity |
| `bash` (generic) | detect repetitive lines, collapse runs, truncate tail |

Model opts in by prefixing with `wumw`. No change to underlying tool.

---

## Phase 3: Feedback loop (later)

- Compressed output includes a header: `# wumw: 1240 → 312 lines`
- Model can request full output if compression was too aggressive: `wumw --full rg ...`
- Log whether `--full` was requested → signal that compression was too lossy

---

## Non-goals (for now)

- ML-based compression
- Code AST compression
- Automatic injection into model tool calls (model explicitly chooses `wumw`)
- Reverse translation / line number mapping

---

## Open questions

1. How do we define "representative tasks" for the benchmark?
2. Session boundary — how does `wumw` know a new task started?
3. Quality metric for Phase 2 — task success rate, or human eval?
