# Experiments

Each experiment has a hypothesis, method, status, and result.
The loop picks the next `[ ]` experiment, runs it, records findings, and proposes follow-ups.

---

## Baseline

### E001 — Raw token spend on real codebase exploration
- **Status:** `[ ]`
- **Hypothesis:** On a medium-sized OSS repo (~50k LOC), an agent exploring to answer a coding question consumes >10k tokens of tool output.
- **Method:** Clone `django/django` into `benchmarks/django`. Run a subagent with the question: *"How does Django's ORM handle database transactions?"*. Agent uses raw `rg`, `cat`, `git log`. Measure: total stdout bytes from tool calls (log via wumw passthrough with `WUMW_SESSION=e001_raw`).
- **Metric:** total stdout bytes, lines, call count by command
- **Result:** _pending_

### E002 — wumw compression on same task
- **Status:** `[ ]` (blocked on E001)
- **Hypothesis:** wumw reduces tool output tokens by >30% with no loss in answer quality.
- **Method:** Same task as E001 but agent uses `wumw rg`, `wumw cat`, etc. Compare session logs E001 vs E002.
- **Metric:** stdout bytes ratio E002/E001, answer quality (human eval: same facts covered?)
- **Result:** _pending_

### E003 — Re-read frequency on a real coding task
- **Status:** `[ ]` (blocked on E001)
- **Hypothesis:** Agents re-read the same files 2-3x per session, accounting for >20% of total token spend.
- **Method:** Run `wumw-analyze` on E001 session. Check re-read frequency table.
- **Metric:** % of total bytes from repeated invocations
- **Result:** _pending_

---

## Compression quality

### E004 — cat compressor: comment stripping breaks model reasoning?
- **Status:** `[ ]`
- **Hypothesis:** Stripping comments from source files does not reduce answer quality for "how does X work" questions.
- **Method:** Ask agent to explain a function from django that has meaningful docstrings/comments. Compare answers: raw cat vs wumw cat.
- **Metric:** human eval: key facts preserved?
- **Result:** _pending_

### E005 — rg compressor: 5 matches/file cap causes missed results?
- **Status:** `[ ]`
- **Hypothesis:** Capping at 5 matches per file causes the agent to miss relevant results in <10% of queries.
- **Method:** Run 10 targeted `rg` queries on django where ground truth is known. Compare wumw rg vs raw rg outputs. Count misses.
- **Metric:** miss rate (%)
- **Result:** _pending_

---

## Ideas / follow-ups
- Test on a larger repo (CPython, Linux kernel headers)
- Measure impact of compression on agentic loop length (# of tool calls to complete task)
- Try adaptive truncation: compress less aggressively for files the model has flagged as important
