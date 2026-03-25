import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from wumw.task_status import build_summary, main, parse_tasklist


def test_parse_tasklist_picks_first_incomplete_task_in_earliest_incomplete_phase():
    summary = parse_tasklist(
        """# Task List

## Phase 1 — Core
- [x] Done task
- [ ] Next up

## Phase 2 — Later
- [ ] Deferred
"""
    )

    assert summary["all_tasks_done"] is False
    assert summary["next_task"] == {
        "title": "Next up",
        "line": 5,
        "phase": "Phase 1 — Core",
        "phase_index": 1,
    }
    assert summary["current_phase"]["title"] == "Phase 1 — Core"


def test_parse_tasklist_marks_all_done_when_every_task_is_checked():
    summary = parse_tasklist(
        """## Phase 1 — Core
- [x] Done

## Phase 2 — Later
- [x] Also done
"""
    )

    assert summary["all_tasks_done"] is True
    assert summary["next_task"] is None
    assert summary["current_phase"] is None


def test_build_summary_includes_resolved_path(tmp_path):
    tasklist = tmp_path / "tasklist.md"
    tasklist.write_text("## Phase 1 — Core\n- [ ] Pending\n")

    summary = build_summary(tasklist)

    assert summary["tasklist_path"] == str(tasklist.resolve())


def test_main_writes_json_to_stdout(tmp_path, capsys):
    tasklist = tmp_path / "tasklist.md"
    tasklist.write_text("## Phase 1 — Core\n- [ ] Pending\n")

    main([str(tasklist)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["next_task"]["title"] == "Pending"
    assert payload["current_phase"]["title"] == "Phase 1 — Core"


def test_main_errors_for_missing_tasklist():
    with pytest.raises(SystemExit):
        main(["does-not-exist.md"])
