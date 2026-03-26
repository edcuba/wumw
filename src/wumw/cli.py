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


_HELP = """\
usage: wumw [--full] <command> [args...]

Wrap a shell command and compress its stdout before it enters LLM context.

Options:
  --full    Bypass compression; output is passed through unchanged.
  --help    Show this message and exit.

Environment variables:
  WUMW_HEADER_MIN_SAVED          Minimum lines saved before the
                                 '# wumw: N → M lines' header is emitted.
                                 Default: 5. Set to 0 to always show the header.
  WUMW_RG_CAP                    Max grep/rg matches per file. Default: 5.
  WUMW_RG_CONTEXT_LINES          Context lines kept around each match. Default: 2.
  WUMW_CAT_LINES                 Lines shown for non-Python files. Default: 100.
  WUMW_CAT_OUTLINE_THRESHOLD     Python file line count above which the
                                 outline compressor is used. Default: 100.
  WUMW_GIT_LOG_ENTRIES           Max git log entries shown. Default: 20.
  WUMW_GIT_DIFF_MIN_HUNK_LINES   Unchanged-hunk span compressed when larger
                                 than this value. Default: 20.
  WUMW_GIT_DIFF_CONTEXT_LINES    Context lines kept in compressed hunks. Default: 3.
  WUMW_GIT_DIFF_MULTIFILE_THRESHOLD  File count above which per-file header
                                 blocks are summarised. Default: 3.
  WUMW_LISTING_MAX_ENTRIES       Max entries before directory listings are
                                 grouped by extension. Default: 40.
  WUMW_GENERIC_LINES             Truncation limit for generic output. Default: 200.
  WUMW_GENERIC_REPEAT_THRESHOLD  Consecutive identical lines collapsed when
                                 the run exceeds this count. Default: 3.
  WUMW_SESSION                   Override the session id written to logs.
  WUMW_SESSION_IDLE_TIMEOUT_SECONDS  Idle gap that triggers a new session id.
                                 Default: 1800 (30 min).
  WUMW_HOME                      Override the state directory root (useful in
                                 sandboxes where the repo root is read-only).
"""


def main():
    if len(sys.argv) < 2:
        print("usage: wumw [--full] <command> [args...]", file=sys.stderr)
        sys.exit(1)

    full = False
    argv = sys.argv[1:]
    if argv[0] in ("--help", "-h"):
        print(_HELP, end="")
        sys.exit(0)
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
