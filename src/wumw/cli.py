import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


def get_session_id():
    if session := os.environ.get("WUMW_SESSION"):
        return session
    session_file = Path.home() / ".wumw" / "session"
    if session_file.exists():
        return session_file.read_text().strip()
    session_id = str(uuid.uuid4())
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(session_id)
    return session_id


def log_invocation(session_id, command, args, stdout, stderr, exit_code):
    log_dir = Path.home() / ".wumw" / "sessions"
    log_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "command": command,
        "args": args,
        "stdout_bytes": len(stdout),
        "stdout_lines": stdout.count(b"\n"),
        "stderr_bytes": len(stderr),
        "exit_code": exit_code,
    }
    log_file = log_dir / f"{session_id}.jsonl"
    with log_file.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def main():
    if len(sys.argv) < 2:
        print("usage: wumw <command> [args...]", file=sys.stderr)
        sys.exit(1)

    session_id = get_session_id()
    command = sys.argv[1]
    args = sys.argv[2:]
    result = subprocess.run([command] + args, capture_output=True)

    log_invocation(session_id, command, args, result.stdout, result.stderr, result.returncode)

    if result.stdout:
        sys.stdout.buffer.write(result.stdout)
    if result.stderr:
        sys.stderr.buffer.write(result.stderr)

    sys.exit(result.returncode)
