# PR Review A/B Benchmark

This note records a repeated A/B benchmark of `wumw` on a real-world pull-request review task.

The goal was not to measure raw compression on isolated shell commands. The goal was to test the stronger claim:

- Does `wumw` reduce end-to-end review token usage?
- Does it cause the reviewer to make more tool calls?
- Does the review still look substantive?

The target repository and review contents are intentionally omitted here. This is a public repo; the benchmark was run on a private codebase and the findings below are sanitized.

## Setup

- Agent: Codex non-interactive review workflow
- Task: review one branch against its base branch
- Prompt: bug/risk/regression-focused review, no code changes
- Arms:
  - baseline: explicitly do not use `wumw`
  - treatment: use `wumw` selectively for large reads, searches, and git output
- Measurement source: Codex JSON event stream (`input_tokens`, `output_tokens`, command execution events)
- Repetitions: 3 full A/B trials on the same hidden review task

## Results

Per-trial deltas are reported as treatment vs baseline.

| Trial | Input Tokens | Output Tokens | Shell Commands |
|---|---:|---:|---:|
| 1 | -36.5% | -29.1% | -38.4% |
| 2 | -3.0% | +43.3% | +16.2% |
| 3 | -0.9% | +2.4% | -3.2% |

Aggregate summary:

- Input tokens: mean `-13.5%`, median `-3.0%`, stdev `20.0pp`
- Output tokens: mean `+5.5%`, median `+2.4%`, stdev `36.3pp`
- Shell commands: mean `-8.5%`, median `-3.2%`, stdev `27.7pp`

## Interpretation

The positive result from the first trial was real, but it was not stable enough to claim a large persistent win from only one run.

What did persist:

- `wumw` did not consistently increase loop length.
- Input-token usage tended to go down, but the size of the win varied a lot.
- The treatment reviews still produced concrete multi-finding outputs rather than collapsing into shallow summaries.

What did not persist:

- Large token savings were not repeatable across all trials.
- Output-token savings were noisy and sometimes reversed.
- Command-count savings were also noisy; one trial used more commands with `wumw`.

The likely reason is straightforward: PR review is path-dependent. If `wumw` lets the agent stay at the "coarse diff triage" level longer, it saves a lot. If compression causes extra targeted follow-up reads, the gain shrinks or disappears.

## Quality Proxies

This benchmark did not score true bug correctness against a labeled ground truth. Instead, it used lightweight review-quality proxies and manual inspection.

Across the 3 trials:

- Every baseline review produced 2-3 findings.
- Every `wumw` review also produced 2-3 findings.
- Every review cited concrete evidence such as file/line references.
- Baseline runs were more consistent about explicitly reporting verification steps.
- `wumw` runs still surfaced substantive issues, but were less consistent about documenting tests or validation in the final write-up.

Takeaway: on this task, `wumw` did not obviously destroy review quality, but it also did not show a strong enough repeated advantage to claim "same quality, much lower cost" from this benchmark alone.

## Conclusion

For PR review, the best current claim is:

- `wumw` can reduce review input tokens materially.
- The win is real but high-variance.
- It does not inherently force more tool calls, but it can in some runs.
- Review quality appears broadly preserved by simple structural proxies, with some evidence that verification reporting becomes less consistent.

That is a useful result, but it is weaker than "wumw reliably cuts PR review cost by X%."

## Reproducing

The helper used for this experiment is:

```bash
python3 scripts/pr_review_ab.py --repo /path/to/repo --base main
```

Notes:

- The script compares a baseline arm against a `wumw` treatment arm.
- It captures Codex JSON events and final review messages under a fresh `/tmp/pr_review_ab_*` directory.
- It is intended for repeated trials; do not rely on a single run.

## Limitations

- Single hidden review task
- Single agent workflow
- Small sample size (`n=3`)
- Quality measured by proxies and manual reading, not labeled bug ground truth
- Results may differ by repo shape, diff size, model behavior, and prompt style
