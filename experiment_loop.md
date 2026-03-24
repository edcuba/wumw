# wumw — Experiment Loop Instructions

You are an autonomous research agent working on the `wumw` project. Each run, you execute one experiment. Follow these steps exactly.

## 1. Check for incomplete work first

Run `git status`. If there are uncommitted changes:
- Understand what was partially done
- Either complete and commit, or revert if broken
- Stop — let the next iteration pick up

## 2. Pick the next experiment

Read `experiments.md`. Find the first experiment with status `[ ]` that has no unmet blockers (i.e. experiments it depends on are marked `[x]`).

If all experiments are done, output the exact phrase "All experiments are done." and stop.

## 3. Run the experiment

Follow the **Method** exactly. Use subagents where the method calls for running an agent. Log sessions with the experiment ID as `WUMW_SESSION` (e.g. `WUMW_SESSION=e001_raw`).

Benchmark repos live in `benchmarks/` — clone there if needed (not tracked in git).

## 4. Record the result

Update `experiments.md`:
- Change status to `[x]`
- Fill in the **Result** field with concrete numbers and a 1-sentence interpretation
- Add new experiment ideas to the **Ideas / follow-ups** section if findings suggest them

## 5. Commit

Stage only `experiments.md` and any new files in `benchmarks/` that should be tracked (none — benchmarks are gitignored). Commit with message: `experiment: E00N — one-line summary of finding`

## 6. Stop

One experiment per loop. Do not start the next one.
