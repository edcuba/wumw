# wumw — Why Use Many Words

A shell command wrapper that compresses tool output before it enters LLM context, reducing token churn in agentic coding sessions.

```bash
wumw rg "pattern" src/
wumw cat bigfile.py
wumw git diff HEAD~1
```

The model opts in by prefixing commands with `wumw`. Output is passed through unchanged in Phase 1 (instrumentation). Phase 2 adds compression. Phase 3 adds a feedback loop.

See [wumw.md](wumw.md) for full spec.

## Status

Phase 1 — instrumentation (in progress)

## Usage

```bash
# install
pip install -e .

# use in place of any command
wumw rg "TODO" src/
wumw cat src/main.py
wumw git log --oneline

# new session
export WUMW_SESSION=$(wumw session new)

# analyze a session
wumw session report
```
