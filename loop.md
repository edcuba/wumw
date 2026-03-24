# wumw — Agent Loop Instructions

You are an autonomous coding agent working on the `wumw` project. Follow these steps exactly each run.

## 1. Check for failure recovery first

Run `git status`. If there are unstaged or uncommitted changes:
- Do NOT pick up a new task
- Understand what was partially done (read the changed files)
- Either complete it and commit, or revert if it's broken
- Then stop — let the next loop iteration pick up a new task

## 2. Pick the next task

Read `tasklist.md`. Find the highest-priority incomplete task (top of the earliest incomplete phase). Do not work on Phase 2 tasks if any Phase 1 tasks remain incomplete.

If all tasks are done, say so and stop.

## 3. Do the work

Implement the task. Follow conventions in `CLAUDE.md`. Keep it simple — no over-engineering, no extras beyond what the task requires.

## 4. Update tasklist.md

Mark the task as `[x]` done. Add any newly discovered subtasks if needed.

## 5. Commit

Stage only relevant files. Commit with a short message describing what was done. No --no-verify.

## 6. Stop

One task per loop. Do not start the next task. The loop runner will invoke you again.
