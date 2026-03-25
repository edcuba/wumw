import argparse
import sys
from collections import defaultdict
from datetime import datetime

from wumw.session_logs import find_sessions_dir, load_entries


DEFAULT_BYTES_PER_TOKEN = 4.0
DEFAULT_TOP_GROUPS = 20


def effective_lines(entry):
    raw_lines = entry.get("stdout_lines", 0)
    if entry.get("full"):
        return raw_lines
    return entry.get("compressed_lines", raw_lines)


def effective_bytes_estimate(entry):
    raw_bytes = entry.get("stdout_bytes", 0)
    raw_lines = entry.get("stdout_lines", 0)
    eff_lines = effective_lines(entry)
    if raw_lines <= 0:
        return raw_bytes
    return raw_bytes * (eff_lines / raw_lines)


def token_estimate(bytes_value, bytes_per_token):
    return bytes_value / bytes_per_token


def summarize_savings(entries, bytes_per_token=DEFAULT_BYTES_PER_TOKEN):
    summary = {
        "total_calls": len(entries),
        "raw_lines": 0,
        "effective_lines": 0,
        "saved_lines": 0,
        "line_savings_pct": 0.0,
        "raw_bytes": 0,
        "effective_bytes_estimate": 0.0,
        "saved_bytes_estimate": 0.0,
        "byte_savings_pct_estimate": 0.0,
        "raw_tokens_estimate": 0.0,
        "effective_tokens_estimate": 0.0,
        "saved_tokens_estimate": 0.0,
        "full_calls": 0,
        "compressed_calls": 0,
        "by_command": [],
    }
    by_command = defaultdict(
        lambda: {
            "command": "",
            "calls": 0,
            "raw_lines": 0,
            "effective_lines": 0,
            "saved_lines": 0,
            "raw_bytes": 0,
            "effective_bytes_estimate": 0.0,
            "saved_bytes_estimate": 0.0,
            "raw_tokens_estimate": 0.0,
            "effective_tokens_estimate": 0.0,
            "saved_tokens_estimate": 0.0,
            "full_calls": 0,
            "compressed_calls": 0,
        }
    )

    for entry in entries:
        command = entry.get("command", "?")
        raw_lines = entry.get("stdout_lines", 0)
        eff_lines = effective_lines(entry)
        raw_bytes = entry.get("stdout_bytes", 0)
        eff_bytes = effective_bytes_estimate(entry)

        summary["raw_lines"] += raw_lines
        summary["effective_lines"] += eff_lines
        summary["raw_bytes"] += raw_bytes
        summary["effective_bytes_estimate"] += eff_bytes
        if entry.get("full"):
            summary["full_calls"] += 1
        elif eff_lines != raw_lines:
            summary["compressed_calls"] += 1

        bucket = by_command[command]
        bucket["command"] = command
        bucket["calls"] += 1
        bucket["raw_lines"] += raw_lines
        bucket["effective_lines"] += eff_lines
        bucket["raw_bytes"] += raw_bytes
        bucket["effective_bytes_estimate"] += eff_bytes
        if entry.get("full"):
            bucket["full_calls"] += 1
        elif eff_lines != raw_lines:
            bucket["compressed_calls"] += 1

    summary["saved_lines"] = summary["raw_lines"] - summary["effective_lines"]
    summary["saved_bytes_estimate"] = summary["raw_bytes"] - summary["effective_bytes_estimate"]
    if summary["raw_lines"] > 0:
        summary["line_savings_pct"] = 100.0 * summary["saved_lines"] / summary["raw_lines"]
    if summary["raw_bytes"] > 0:
        summary["byte_savings_pct_estimate"] = (
            100.0 * summary["saved_bytes_estimate"] / summary["raw_bytes"]
        )
    summary["raw_tokens_estimate"] = token_estimate(summary["raw_bytes"], bytes_per_token)
    summary["effective_tokens_estimate"] = token_estimate(
        summary["effective_bytes_estimate"], bytes_per_token
    )
    summary["saved_tokens_estimate"] = token_estimate(
        summary["saved_bytes_estimate"], bytes_per_token
    )

    rows = []
    for bucket in by_command.values():
        bucket["saved_lines"] = bucket["raw_lines"] - bucket["effective_lines"]
        bucket["saved_bytes_estimate"] = (
            bucket["raw_bytes"] - bucket["effective_bytes_estimate"]
        )
        if bucket["raw_lines"] > 0:
            bucket["line_savings_pct"] = (
                100.0 * bucket["saved_lines"] / bucket["raw_lines"]
            )
        else:
            bucket["line_savings_pct"] = 0.0
        if bucket["raw_bytes"] > 0:
            bucket["byte_savings_pct_estimate"] = (
                100.0 * bucket["saved_bytes_estimate"] / bucket["raw_bytes"]
            )
        else:
            bucket["byte_savings_pct_estimate"] = 0.0
        bucket["raw_tokens_estimate"] = token_estimate(
            bucket["raw_bytes"], bytes_per_token
        )
        bucket["effective_tokens_estimate"] = token_estimate(
            bucket["effective_bytes_estimate"], bytes_per_token
        )
        bucket["saved_tokens_estimate"] = token_estimate(
            bucket["saved_bytes_estimate"], bytes_per_token
        )
        rows.append(bucket)
    rows.sort(key=lambda row: (-row["saved_tokens_estimate"], row["command"]))
    summary["by_command"] = rows
    return summary


def entry_timestamp(entry):
    value = entry.get("timestamp")
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def group_entries(entries, key_fn):
    grouped = defaultdict(list)
    for entry in entries:
        grouped[key_fn(entry)].append(entry)
    return grouped


def summarize_groups(entries, key_fn, bytes_per_token):
    rows = []
    for key, group_entries_list in group_entries(entries, key_fn).items():
        summary = summarize_savings(group_entries_list, bytes_per_token=bytes_per_token)
        timestamps = [ts for ts in (entry_timestamp(entry) for entry in group_entries_list) if ts]
        rows.append(
            {
                "key": key,
                "start": min(timestamps) if timestamps else None,
                "end": max(timestamps) if timestamps else None,
                "summary": summary,
            }
        )
    rows.sort(
        key=lambda row: (
            row["start"] is None,
            row["start"] or datetime.min,
            row["key"],
        )
    )
    return rows


def parse_datetime(value):
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid ISO-8601 datetime: {value}"
        ) from exc


def format_int(value):
    return f"{int(round(value)):,}"


def format_time(value):
    if value is None:
        return "-"
    return value.isoformat(timespec="seconds")


def format_pct(numerator, denominator):
    if denominator <= 0:
        return "0.0%"
    return f"{100.0 * numerator / denominator:.1f}%"


def print_summary(summary, bytes_per_token):
    print("=== Savings Estimate ===")
    print(
        "  "
        f"calls {summary['total_calls']}, "
        f"compressed {summary['compressed_calls']}, "
        f"--full {summary['full_calls']}"
    )
    print(
        "  "
        f"lines {format_int(summary['raw_lines'])} -> {format_int(summary['effective_lines'])}  "
        f"saved {format_int(summary['saved_lines'])} ({summary['line_savings_pct']:.1f}%)"
    )
    print(
        "  "
        f"bytes {format_int(summary['raw_bytes'])} -> {format_int(summary['effective_bytes_estimate'])} est  "
        f"saved {format_int(summary['saved_bytes_estimate'])} ({summary['byte_savings_pct_estimate']:.1f}%)"
    )
    print(
        "  "
        f"tokens est raw {format_int(summary['raw_tokens_estimate'])}  "
        f"effective {format_int(summary['effective_tokens_estimate'])}  "
        f"saved {format_int(summary['saved_tokens_estimate'])}  "
        f"(@ {bytes_per_token:g} bytes/token)"
    )

    print("\n=== Savings By Command ===")
    if not summary["by_command"]:
        print("  No data.")
        return
    for row in summary["by_command"]:
        print(
            "  "
            f"{row['command']:<12} "
            f"raw {format_int(row['raw_tokens_estimate']):>8} tok est  "
            f"saved {format_int(row['saved_tokens_estimate']):>8} tok est  "
            f"{row['line_savings_pct']:>5.1f}% lines  "
            f"{row['byte_savings_pct_estimate']:>5.1f}% bytes est  "
            f"({row['calls']} calls)"
        )


def print_group_table(title, rows, *, top, bytes_per_token):
    print(f"\n=== {title} ===")
    if not rows:
        print("  No data.")
        return

    ordered = sorted(
        rows,
        key=lambda row: (
            -row["summary"]["saved_tokens_estimate"],
            row["start"] or datetime.min,
            row["key"],
        ),
    )
    for row in ordered[:top]:
        summary = row["summary"]
        print(
            "  "
            f"{row['key']:<36} "
            f"raw {format_int(summary['raw_tokens_estimate']):>8}  "
            f"saved {format_int(summary['saved_tokens_estimate']):>8}  "
            f"{format_pct(summary['saved_bytes_estimate'], summary['raw_bytes']):>6}  "
            f"calls {summary['total_calls']:>4}  "
            f"--full {summary['full_calls']:>3}  "
            f"{format_time(row['start'])} -> {format_time(row['end'])}"
        )

    if len(rows) > top:
        print(f"  ... {len(rows) - top} more")
    print(f"  token heuristic: {bytes_per_token:g} bytes/token")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="wumw-savings",
        description="Estimate token savings from wumw session logs.",
    )
    parser.add_argument(
        "--session",
        help="Analyze only one session id (matches .jsonl filename stem).",
    )
    parser.add_argument(
        "--bytes-per-token",
        type=float,
        default=DEFAULT_BYTES_PER_TOKEN,
        help="Heuristic used for token estimate (default: 4).",
    )
    parser.add_argument(
        "--since",
        type=parse_datetime,
        help="Only include entries at or after this ISO-8601 timestamp.",
    )
    parser.add_argument(
        "--until",
        type=parse_datetime,
        help="Only include entries at or before this ISO-8601 timestamp.",
    )
    parser.add_argument(
        "--by-session",
        action="store_true",
        help="Show savings broken down by wumw session id.",
    )
    parser.add_argument(
        "--by-day",
        action="store_true",
        help="Show savings broken down by UTC day.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP_GROUPS,
        help=f"Maximum rows to print for grouped views (default: {DEFAULT_TOP_GROUPS}).",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    sessions_dir = find_sessions_dir()
    entries = load_entries(
        sessions_dir,
        session_id=args.session,
        since=args.since,
        until=args.until,
    )
    if not entries:
        target = f"session {args.session}" if args.session else "sessions"
        print(f"No data found for {target} in {sessions_dir}")
        return 0

    summary = summarize_savings(entries, bytes_per_token=args.bytes_per_token)
    print_summary(summary, args.bytes_per_token)

    if args.by_session:
        session_rows = summarize_groups(
            entries,
            key_fn=lambda entry: entry.get("session_id", "?"),
            bytes_per_token=args.bytes_per_token,
        )
        print_group_table(
            "Savings By Session",
            session_rows,
            top=args.top,
            bytes_per_token=args.bytes_per_token,
        )

    if args.by_day:
        day_rows = summarize_groups(
            entries,
            key_fn=lambda entry: (
                entry_timestamp(entry).date().isoformat()
                if entry_timestamp(entry) is not None
                else "unknown"
            ),
            bytes_per_token=args.bytes_per_token,
        )
        print_group_table(
            "Savings By Day (UTC)",
            day_rows,
            top=args.top,
            bytes_per_token=args.bytes_per_token,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
