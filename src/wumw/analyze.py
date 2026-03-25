import sys
from collections import Counter, defaultdict
from wumw.session_logs import find_sessions_dir, load_entries


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

    # --full usage per session → compression quality signal
    full_by_session = defaultdict(int)
    total_by_session = defaultdict(int)
    full_by_cmd = defaultdict(int)
    compressed_by_cmd = defaultdict(int)
    lines_saved_by_cmd = defaultdict(int)
    for e in entries:
        sid = e.get("session_id", "?")
        cmd = e.get("command", "?")
        total_by_session[sid] += 1
        if e.get("full"):
            full_by_session[sid] += 1
            full_by_cmd[cmd] += 1
        elif e.get("compressed_lines") is not None:
            compressed_by_cmd[cmd] += 1
            lines_saved_by_cmd[cmd] += e.get("stdout_lines", 0) - e["compressed_lines"]

    print(f"\n=== --full usage per session (compression quality signal) ===")
    sessions_with_full = {s for s, n in full_by_session.items() if n > 0}
    if not sessions_with_full:
        print("  No --full usage recorded.")
    else:
        for sid in sorted(full_by_session, key=full_by_session.get, reverse=True):
            n_full = full_by_session[sid]
            n_total = total_by_session[sid]
            pct = 100 * n_full / n_total if n_total else 0
            print(f"  {sid}  {n_full}/{n_total} calls used --full ({pct:.0f}%)")

    print(f"\n=== --full rate by command (high rate = compression quality issue) ===")
    all_cmds = set(full_by_cmd) | set(compressed_by_cmd)
    if not all_cmds:
        print("  No data.")
    else:
        rows = []
        for cmd in all_cmds:
            n_full = full_by_cmd.get(cmd, 0)
            n_comp = compressed_by_cmd.get(cmd, 0)
            n_total = count_by_cmd.get(cmd, 0)
            saved = lines_saved_by_cmd.get(cmd, 0)
            full_rate = n_full / n_total if n_total else 0
            rows.append((cmd, n_full, n_comp, n_total, saved, full_rate))
        rows.sort(key=lambda r: -r[5])
        for cmd, n_full, n_comp, n_total, saved, full_rate in rows:
            saved_str = f"  saved {saved} lines" if saved > 0 else ""
            print(f"  {cmd:<20} --full {n_full}/{n_total} ({100*full_rate:.0f}%)  compressed {n_comp}x{saved_str}")

    print(f"\nTotal invocations: {len(entries)}")
