"""
Benchmark harness: run commands with and without wumw, compare token counts.

Input: JSONL file (or stdin), one command per line:
  {"command": "rg", "args": ["-r", "TODO", "src/"]}

Output: table comparing raw vs compressed line/byte counts and exit codes.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path


def run_raw(command, args):
    result = subprocess.run([command] + args, capture_output=True)
    return result.stdout, result.returncode


def run_wumw(command, args):
    wumw_bin = shutil.which("wumw")
    if not wumw_bin:
        raise RuntimeError("wumw not found in PATH")
    result = subprocess.run([wumw_bin, command] + args, capture_output=True)
    return result.stdout, result.returncode


def count_lines(data: bytes) -> int:
    return data.count(b"\n")


def format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    return f"{n/1024:.1f}K"


def main():
    if len(sys.argv) > 1 and sys.argv[1] not in ("-", "--help"):
        source = Path(sys.argv[1]).open()
    else:
        if "--help" in sys.argv:
            print("usage: wumw-bench [file.jsonl]")
            print("  Each line: {\"command\": \"rg\", \"args\": [...]}")
            sys.exit(0)
        source = sys.stdin

    commands = []
    with source:
        for line in source:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"skipping bad JSON: {e}", file=sys.stderr)
                continue
            commands.append(entry)

    if not commands:
        print("No commands to benchmark.", file=sys.stderr)
        sys.exit(1)

    results = []
    for entry in commands:
        cmd = entry.get("command", "")
        args = entry.get("args", [])
        if not cmd:
            continue
        label = " ".join([cmd] + args)
        if len(label) > 60:
            label = label[:57] + "..."

        raw_out, raw_rc = run_raw(cmd, args)
        wumw_out, wumw_rc = run_wumw(cmd, args)

        raw_lines = count_lines(raw_out)
        wumw_lines = count_lines(wumw_out)
        raw_bytes = len(raw_out)
        wumw_bytes = len(wumw_out)

        line_ratio = wumw_lines / raw_lines if raw_lines else 1.0
        byte_ratio = wumw_bytes / raw_bytes if raw_bytes else 1.0
        success = raw_rc == wumw_rc

        results.append({
            "label": label,
            "raw_lines": raw_lines,
            "wumw_lines": wumw_lines,
            "raw_bytes": raw_bytes,
            "wumw_bytes": wumw_bytes,
            "line_ratio": line_ratio,
            "byte_ratio": byte_ratio,
            "raw_rc": raw_rc,
            "wumw_rc": wumw_rc,
            "success": success,
        })

    # Print table
    header = f"{'command':<62} {'raw':>6} {'wumw':>6} {'lines%':>7} {'raw':>7} {'wumw':>7} {'bytes%':>7} {'ok':>4}"
    print(header)
    print("-" * len(header))
    for r in results:
        ok = "yes" if r["success"] else f"no({r['raw_rc']}!={r['wumw_rc']})"
        print(
            f"{r['label']:<62}"
            f" {r['raw_lines']:>6}"
            f" {r['wumw_lines']:>6}"
            f" {100*r['line_ratio']:>6.0f}%"
            f" {format_bytes(r['raw_bytes']):>7}"
            f" {format_bytes(r['wumw_bytes']):>7}"
            f" {100*r['byte_ratio']:>6.0f}%"
            f" {ok:>4}"
        )

    print()
    if results:
        avg_lines = sum(r["line_ratio"] for r in results) / len(results)
        avg_bytes = sum(r["byte_ratio"] for r in results) / len(results)
        n_ok = sum(1 for r in results if r["success"])
        print(f"Summary: {len(results)} commands, avg lines {100*avg_lines:.0f}%, avg bytes {100*avg_bytes:.0f}%, {n_ok}/{len(results)} exit codes match")
