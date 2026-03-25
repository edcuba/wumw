# wumw — Why Use Many Words

A shell command wrapper that compresses tool output before it enters LLM context, reducing token churn in agentic coding sessions.

The model opts in by prefixing commands with `wumw`. Output is compressed and a `# wumw: N → M lines` header is prepended when reduction occurs.

## What it does

| Command | Compression strategy |
|---|---|
| `wumw cat file.py` | Python files: emit class/def outline with line numbers; other files: first 100 lines + `tail` hint |
| `wumw rg pattern src/` | Cap 5 matches/file, deduplicate, limit context lines |
| `wumw git diff` | Strip index metadata lines |
| `wumw git log` | Cap at 20 entries |
| anything else | Collapse repeated lines, truncate at 200 lines |

`wumw --full <cmd>` bypasses compression and logs the bypass.

## Install

```bash
git clone git@github.com:edcuba/wumw.git
cd wumw
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Usage

```bash
# drop-in prefix for any command
wumw cat src/main.py
wumw rg "TODO" src/
wumw git log --oneline
wumw git diff HEAD~1

# bypass compression when you need the full output
wumw --full cat src/bigfile.py

# analyze session logs
wumw-analyze

# benchmark compression ratio on a codebase
wumw-bench
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
Prefer `wumw cat`, `wumw rg`, and `wumw git` over bare commands to reduce context size.
```

Claude will prefix commands with `wumw` and navigate Python files via `sed -n 'N,Mp'` using the outline hints.

## Use with Codex (OpenAI)

Add to your repo's `AGENTS.md`:

```markdown
## Tool usage
Prefix file reads and searches with `wumw` to reduce context size:
- `wumw cat file.py` instead of `cat file.py`
- `wumw rg pattern src/` instead of `rg pattern src/`
- `wumw git diff` instead of `git diff`

For Python files, `wumw cat` returns a class/method outline with line numbers.
Use `sed -n 'START,ENDp' file.py` to read specific sections.
```

## Session logs

wumw logs every invocation to `.wumw/sessions/<session_id>.jsonl` (gitignored).

```bash
wumw-analyze              # summary: bytes by command, re-read rate, --full rate
wumw-analyze --session X  # specific session
```

## Benchmark

```bash
wumw-bench                # runs commands with/without wumw, prints compression ratio table
```
