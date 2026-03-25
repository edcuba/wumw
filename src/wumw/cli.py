import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone

from wumw.compress import compress
from wumw.state import get_state_dir, get_session_info

MIN_HEADER_LINES_SAVED = 5
BYTES_PER_TOKEN_ESTIMATE = 4.0


def _header_min_lines_saved():
    value = os.environ.get("WUMW_HEADER_MIN_SAVED")
    if value is None:
        return MIN_HEADER_LINES_SAVED
    try:
        parsed = int(value)
    except ValueError:
        return MIN_HEADER_LINES_SAVED
    return max(0, parsed)


def estimate_tokens(stdout):
    if not stdout:
        return 0
    return math.ceil(len(stdout) / BYTES_PER_TOKEN_ESTIMATE)


def log_invocation(session_info, command, args, stdout, stderr, exit_code, compressed_lines=None, full=False):
    log_dir = get_state_dir() / "sessions"
    log_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_info["session_id"],
        "session_started_at": session_info.get("started_at"),
        "cwd": session_info.get("cwd"),
        "context_root": session_info.get("context_root"),
        "in_git_repo": session_info.get("in_git_repo"),
        "command": command,
        "args": args,
        "stdout_bytes": len(stdout),
        "stdout_lines": stdout.count(b"\n"),
        "estimated_tokens": estimate_tokens(stdout),
        "stderr_bytes": len(stderr),
        "exit_code": exit_code,
    }
    if compressed_lines is not None:
        entry["compressed_lines"] = compressed_lines
    if full:
        entry["full"] = True
    log_file = log_dir / f"{session_info['session_id']}.jsonl"
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

    session_info = get_session_info()
    command = argv[0]
    args = argv[1:]
    result = subprocess.run([command] + args, capture_output=True)

    if full:
        log_invocation(session_info, command, args, result.stdout, result.stderr, result.returncode, full=True)
        if result.stdout:
            sys.stdout.buffer.write(result.stdout)
        if result.stderr:
            sys.stderr.buffer.write(result.stderr)
        sys.exit(result.returncode)

    cmd_basename = os.path.basename(command)
    compressed_stdout, original_lines, compressed_line_count = compress(cmd_basename, result.stdout, args)
    lines_saved = original_lines - compressed_line_count

    log_invocation(
        session_info, command, args, result.stdout, result.stderr, result.returncode,
        compressed_lines=compressed_line_count if compressed_line_count != original_lines else None,
    )

    if compressed_stdout:
        if lines_saved >= _header_min_lines_saved():
            header = f"# wumw: {original_lines} → {compressed_line_count} lines\n".encode()
            sys.stdout.buffer.write(header)
        sys.stdout.buffer.write(compressed_stdout)
    if result.stderr:
        sys.stderr.buffer.write(result.stderr)

    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
