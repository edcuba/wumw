import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from wumw.compress import compress


def find_repo_root():
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("wumw must be run inside a git repository")
    return Path(result.stdout.strip())


def get_session_id():
    if session := os.environ.get("WUMW_SESSION"):
        return session
    session_file = find_repo_root() / ".wumw" / "session"
    if session_file.exists():
        return session_file.read_text().strip()
    session_id = str(uuid.uuid4())
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(session_id)
    return session_id


def log_invocation(session_id, command, args, stdout, stderr, exit_code, compressed_lines=None, full=False):
    log_dir = find_repo_root() / ".wumw" / "sessions"
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
    if compressed_lines is not None:
        entry["compressed_lines"] = compressed_lines
    if full:
        entry["full"] = True
    log_file = log_dir / f"{session_id}.jsonl"
    with log_file.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def main():
    if len(sys.argv) < 2:
        print("usage: wumw [--full] <command> [args...]", file=sys.stderr)
        sys.exit(1)

    full = False
    argv = sys.argv[1:]
    if argv[0] == "--full":
        full = True
        argv = argv[1:]

    if not argv:
        print("usage: wumw [--full] <command> [args...]", file=sys.stderr)
        sys.exit(1)

    session_id = get_session_id()
    command = argv[0]
    args = argv[1:]
    result = subprocess.run([command] + args, capture_output=True)

    if full:
        log_invocation(session_id, command, args, result.stdout, result.stderr, result.returncode, full=True)
        if result.stdout:
            sys.stdout.buffer.write(result.stdout)
        if result.stderr:
            sys.stderr.buffer.write(result.stderr)
        sys.exit(result.returncode)

    cmd_basename = os.path.basename(command)
    compressed_stdout, original_lines, compressed_line_count = compress(cmd_basename, result.stdout)

    log_invocation(
        session_id, command, args, result.stdout, result.stderr, result.returncode,
        compressed_lines=compressed_line_count if compressed_line_count != original_lines else None,
    )

    if compressed_stdout:
        if compressed_line_count != original_lines:
            header = f"# wumw: {original_lines} → {compressed_line_count} lines\n".encode()
            sys.stdout.buffer.write(header)
        sys.stdout.buffer.write(compressed_stdout)
    if result.stderr:
        sys.stderr.buffer.write(result.stderr)

    sys.exit(result.returncode)
