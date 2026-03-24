import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def find_sessions_dir():
    return Path.home() / ".wumw" / "sessions"


def load_entries(sessions_dir):
    entries = []
    if not sessions_dir.exists():
        return entries
    for jsonl_file in sessions_dir.glob("*.jsonl"):
        with jsonl_file.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return entries


def main():
    sessions_dir = find_sessions_dir()
    entries = load_entries(sessions_dir)

    if not entries:
        print(f"No data found in {sessions_dir}")
        sys.exit(0)

    # Token spend by command (stdout_bytes as proxy)
    bytes_by_cmd = defaultdict(int)
    lines_by_cmd = defaultdict(int)
    count_by_cmd = Counter()
    for e in entries:
        cmd = e.get("command", "?")
        bytes_by_cmd[cmd] += e.get("stdout_bytes", 0)
        lines_by_cmd[cmd] += e.get("stdout_lines", 0)
        count_by_cmd[cmd] += 1

    print("=== Token spend by command (stdout bytes, proxy for tokens) ===")
    for cmd in sorted(bytes_by_cmd, key=bytes_by_cmd.get, reverse=True):
        print(f"  {cmd:<20} {bytes_by_cmd[cmd]:>10} bytes  {lines_by_cmd[cmd]:>8} lines  ({count_by_cmd[cmd]} calls)")

    # Re-read frequency: same (command, args) repeated
    invocation_counts = Counter()
    for e in entries:
        key = (e.get("command", "?"), tuple(e.get("args", [])))
        invocation_counts[key] += 1

    repeated = [(key, n) for key, n in invocation_counts.items() if n > 1]
    repeated.sort(key=lambda x: -x[1])

    print(f"\n=== Re-read frequency (repeated invocations, top 20) ===")
    if not repeated:
        print("  No repeated invocations found.")
    else:
        for (cmd, args), n in repeated[:20]:
            args_str = " ".join(args) if args else "(no args)"
            print(f"  {n:>4}x  {cmd} {args_str}")

    print(f"\nTotal invocations: {len(entries)}")
