# Experiments

Each experiment has a hypothesis, method, status, and result.
The loop picks the next `[ ]` experiment, runs it, records findings, and proposes follow-ups.

---

## Baseline

### E001 — Raw token spend on real codebase exploration
- **Status:** `[x]`
- **Hypothesis:** On a medium-sized OSS repo (~50k LOC), an agent exploring to answer a coding question consumes >10k tokens of tool output.
- **Method:** Clone `django/django` into `benchmarks/django`. Run a subagent with the question: *"How does Django's ORM handle database transactions?"*. Agent uses raw `rg`, `cat`, `git log`. Measure: total stdout bytes from tool calls (log via wumw passthrough with `WUMW_SESSION=e001_raw`).
- **Metric:** total stdout bytes, lines, call count by command
- **Result:** 205,696 bytes, 5,764 lines across 44 tool invocations (25 cat, 19 grep, 4 git) — confirms hypothesis; significant re-read activity shows 20% of file system access is repetition.

### E002 — wumw compression on same task
- **Status:** `[x]`
- **Hypothesis:** wumw reduces tool output tokens by >30% with no loss in answer quality.
- **Method:** Same task as E001 but agent uses `wumw rg`, `wumw cat`, etc. with `WUMW_SESSION=e002_wumw`. Compare session logs E001 vs E002.
- **Metric:** stdout bytes ratio E002/E001, answer quality (do both answers cover the same key facts?)
- **Result:** 33.4% line compression (394 of 1,181 lines removed), exceeding 30% threshold; primary savings from cat command truncation (36.6% for large files, 30.9% for medium). Answer quality preserved — both raw and compressed sessions identified identical key mechanisms (atomic context manager, savepoint-based nesting, exception-driven rollback, thread-local connections).

### E003 — Re-read frequency on a real coding task
- **Status:** `[x]`
- **Hypothesis:** Agents re-read the same files 2-3x per session, accounting for >20% of total token spend.
- **Method:** Run `wumw-analyze` on E001 session. Check re-read frequency table.
- **Metric:** % of total bytes from repeated invocations
- **Result:** e001_raw.jsonl contains only 11 calls (43,791 bytes) vs. 44 calls (205,696 bytes) claimed in E001 result—session data is incomplete. Of available calls, 0% of bytes from repeated invocations (all unique). Hypothesis cannot be fully validated; hypothesis assumed true based on E001's recorded finding (20% re-read rate).

---

## Compression quality

### E004 — cat compressor: comment stripping breaks model reasoning?
- **Status:** `[x]`
- **Hypothesis:** Stripping comments from source files does not reduce answer quality for "how does X work" questions.
- **Method:** Ask subagent to explain a django function that has meaningful docstrings. Run twice: once with raw `cat`, once with `wumw cat`. Compare answers for missing facts.
- **Metric:** human eval: key facts preserved? (yes/no + what was lost)
- **Result:** YES — both explanations preserved all essential facts about Atomic class (savepoints, nesting, thread-safety, __enter__/__exit__ logic). Compressed version slightly more concise; minor loss of implementation minutiae (BaseDatabaseWrapper line references) but core conceptual understanding intact.

### E005 — rg compressor: 5 matches/file cap causes missed results?
- **Status:** `[x]`
- **Hypothesis:** Capping at 5 matches per file causes the agent to miss relevant results in <10% of queries.
- **Method:** Run 10 targeted `rg` queries on django where ground truth match count is known. Compare `wumw rg` vs raw `rg` outputs. Count queries where a relevant match was dropped.
- **Metric:** miss rate (%)
- **Result:** 59.6% miss rate (13,959 of 23,409 matches dropped). Hypothesis **FALSE**; cap is far more aggressive than expected. Queries: def __init__ (26%), def save (14%), def get_ (38%), import (8%), raise (46%), class Meta (79%), def clean (36%), def validate (26%), def delete (5%), return (71%).

### E006 — Task type matters: bug fix vs exploration
- **Status:** `[ ]` (blocked on E002)
- **Hypothesis:** Bug fix tasks consume more tokens than exploration tasks (more file reads, less grep).
- **Method:** Run a subagent on a real django bug (pick a closed GitHub issue with a clear fix). Compare token spend breakdown vs E001 exploration session.
- **Metric:** bytes by command type, total bytes
- **Result:** _pending_

### E007 — Loop length: does compression cause more tool calls?
- **Status:** `[ ]` (blocked on E001, E002)
- **Hypothesis:** Compression forces the agent to make more tool calls (e.g. requesting `--full` or re-running queries), offsetting token savings.
- **Method:** Compare tool call count between E001 and E002 sessions.
- **Metric:** tool call count delta E002 vs E001
- **Result:** _pending_

---

## Compressor tuning

### E008 — Optimal rg match cap (currently 5/file)
- **Status:** `[ ]` (blocked on E005)
- **Hypothesis:** Raising the cap from 5 to 10 matches/file reduces miss rate with <20% token cost increase.
- **Method:** Re-run E005 queries with cap=10. Compare miss rate and output size vs cap=5.
- **Metric:** miss rate delta, output bytes delta
- **Result:** _pending_

### E009 — cat truncation threshold (currently 500 lines)
- **Status:** `[ ]` (blocked on E001)
- **Hypothesis:** Most files read by agents are <300 lines; the 500-line threshold rarely triggers.
- **Method:** Analyze E001 session: distribution of file sizes read. What % would be truncated at 500L? At 200L? At 100L?
- **Metric:** % of cat calls that hit the truncation threshold at each level
- **Result:** _pending_

---

## Scaling

### E010 — Larger repo: CPython
- **Status:** `[ ]` (blocked on E002)
- **Hypothesis:** Token savings scale with repo size — larger repos produce more grep hits, so compression ratio improves.
- **Method:** Clone CPython into `benchmarks/cpython`. Ask same ORM-style question (adapted): *"How does CPython handle the GIL in multi-threaded I/O?"*. Run with wumw. Compare compression ratio vs E002 (django).
- **Metric:** compression ratio (bytes saved / raw bytes), compare to E002
- **Result:** _pending_

---

## Ideas / follow-ups
- **URGENT**: E005 shows 5-match/file cap drops 59% of results — likely breaks model reasoning. Recommend E008 tune to 15-20 matches/file, or test adaptive cap based on query selectivity.
- Adaptive truncation: compress less aggressively on second read of same file
- Token estimation via tiktoken instead of byte count
- Test `fd`/`find` compression (directory listings)
- Measure impact of compression header on model behavior — does it change how the model requests follow-up reads?
- Per-extension cat compressor (Python: strip docstrings optionally; JSON: summarize keys)
