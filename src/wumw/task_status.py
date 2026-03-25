import argparse
import json
import sys
from pathlib import Path


PHASE_PREFIX = "## "
TASK_PREFIX = "- ["


def parse_tasklist(text):
    phases = []
    current_phase = None

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip()
        if line.startswith(PHASE_PREFIX):
            title = line[len(PHASE_PREFIX):].strip()
            if title.startswith("Phase "):
                current_phase = {
                    "title": title,
                    "line": lineno,
                    "tasks": [],
                }
                phases.append(current_phase)
            continue

        if not line.startswith(TASK_PREFIX) or current_phase is None or len(line) < 7:
            continue

        marker = line[3:4].lower()
        if marker not in {" ", "x"} or line[4:5] != "]" or line[5:6] != " ":
            continue

        title = line[6:].strip()
        if not title:
            continue

        current_phase["tasks"].append(
            {
                "title": title,
                "done": marker == "x",
                "line": lineno,
            }
        )

    phase_summaries = []
    next_task = None

    for index, phase in enumerate(phases, start=1):
        tasks = phase["tasks"]
        completed_tasks = sum(1 for task in tasks if task["done"])
        total_tasks = len(tasks)
        first_incomplete = next((task for task in tasks if not task["done"]), None)
        if next_task is None and first_incomplete is not None:
            next_task = {
                "title": first_incomplete["title"],
                "line": first_incomplete["line"],
                "phase": phase["title"],
                "phase_index": index,
            }

        phase_summaries.append(
            {
                "title": phase["title"],
                "line": phase["line"],
                "phase_index": index,
                "total_tasks": total_tasks,
                "completed_tasks": completed_tasks,
                "pending_tasks": total_tasks - completed_tasks,
                "completed": total_tasks > 0 and completed_tasks == total_tasks,
                "next_task": first_incomplete["title"] if first_incomplete else None,
            }
        )

    current_phase = None
    if next_task is not None:
        current_phase = next(
            phase for phase in phase_summaries if phase["phase_index"] == next_task["phase_index"]
        )

    return {
        "phase_count": len(phase_summaries),
        "all_tasks_done": next_task is None,
        "current_phase": current_phase,
        "next_task": next_task,
        "phases": phase_summaries,
    }


def build_summary(tasklist_path):
    text = tasklist_path.read_text()
    summary = parse_tasklist(text)
    summary["tasklist_path"] = str(tasklist_path.resolve())
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="wumw-task-status",
        description="Summarize tasklist.md progress as JSON.",
    )
    parser.add_argument(
        "tasklist",
        nargs="?",
        default="tasklist.md",
        help="path to the markdown task list (default: tasklist.md)",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="indent JSON output for humans",
    )
    args = parser.parse_args(argv)

    tasklist_path = Path(args.tasklist)
    if not tasklist_path.exists():
        parser.error(f"task list not found: {tasklist_path}")

    summary = build_summary(tasklist_path)
    json.dump(summary, sys.stdout, indent=2 if args.pretty else None)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
