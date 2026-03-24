import os
import subprocess
import sys
import uuid
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


def main():
    if len(sys.argv) < 2:
        print("usage: wumw <command> [args...]", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1:]
    result = subprocess.run(command, capture_output=True)

    if result.stdout:
        sys.stdout.buffer.write(result.stdout)
    if result.stderr:
        sys.stderr.buffer.write(result.stderr)

    sys.exit(result.returncode)
