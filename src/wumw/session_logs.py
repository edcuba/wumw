import json
from datetime import datetime

from wumw.state import get_state_dir


def find_sessions_dir():
    return get_state_dir() / "sessions"


def _entry_timestamp(entry):
    value = entry.get("timestamp")
    if not value:
        return datetime.min
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.min


def load_entries(sessions_dir, session_id=None, since=None, until=None):
    entries = []
    if not sessions_dir.exists():
        return entries
    for jsonl_file in sorted(sessions_dir.glob("*.jsonl")):
        if session_id is not None and jsonl_file.stem != session_id:
            continue
        with jsonl_file.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    pass
                else:
                    timestamp = _entry_timestamp(entry)
                    if since is not None and timestamp < since:
                        continue
                    if until is not None and timestamp > until:
                        continue
                    entries.append(entry)
    return entries
