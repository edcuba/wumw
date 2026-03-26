#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, stdev

WUMW_VENV_BIN = Path(__file__).parent.parent / ".venv" / "bin"


_RESEARCH_PROTOCOL = """
Follow this research protocol — do not skip steps:
1. Read the full diff between {base} and HEAD.
2. For each changed Python file, read the file to understand the broader context around the changed lines.
3. Search for callers and tests of any renamed, deprecated, or newly added symbols.
4. Write your findings with file:line references.
"""

DEFAULT_PROMPT = """Review the commit(s) on the current branch against {base}.
Do not make any code changes.
{protocol}
Focus on bugs, risks, behavioral regressions, and missing tests.
If you find no issues, say so explicitly and mention residual risks or testing gaps."""

FORCED_READ_TARGET = "django/db/models/fields/__init__.py"

FORCED_READ_BASELINE_PROMPT = (
    "Ignore any repository guidance about wumw for this run. "
    "Do not invoke `wumw` or `wumw --full`. "
    "Use standard shell commands only.\n\n"
    f"Task: Summarize the purpose and key design decisions of `{FORCED_READ_TARGET}`. "
    f"Read the file using `cat {FORCED_READ_TARGET}`, then write a concise summary covering: "
    "what the module does, its main abstractions, and the most important design choices visible in the code. "
    "Do not make any code changes."
)

FORCED_READ_TREATMENT_PROMPT = (
    "Prefix all file-reading commands with `wumw`. "
    "`wumw` compresses large output to fit in context. "
    "If the compressed output omits lines you need, use `sed -n 'START,ENDp' file` to read that range directly — "
    "the omission notes include line numbers to guide you.\n\n"
    f"Task: Summarize the purpose and key design decisions of `{FORCED_READ_TARGET}`. "
    f"Read the file using `wumw cat {FORCED_READ_TARGET}`, then write a concise summary covering: "
    "what the module does, its main abstractions, and the most important design choices visible in the code. "
    "Do not make any code changes."
)

BASELINE_PREFIX = (
    "Ignore any repository guidance about wumw for this run. "
    "Do not invoke `wumw` or `wumw --full`. "
    "Use standard shell commands: git diff, cat, grep."
)

TREATMENT_PREFIX = (
    "Prefix all file-reading and search commands with `wumw`: "
    "use `wumw git diff base..HEAD`, `wumw cat file`, `wumw grep pattern dir`. "
    "`wumw` compresses large output to fit in context. "
    "If the compressed output omits lines you need, use `sed -n 'START,ENDp' file` to read that range directly — "
    "the omission notes include line numbers to guide you. "
    "Do not skip reading a file because it is large — use `wumw cat` to read it."
)

METRICS = (
    ("input_tokens", "Input Tokens"),
    ("output_tokens", "Output Tokens"),
    ("command_count", "Shell Commands"),
)


@dataclass
class VariantResult:
    name: str
    prompt: str
    command: list[str]
    returncode: int
    thread_id: str | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    command_count: int
    command_counts_by_prog: dict[str, int]
    wumw_command_count: int
    raw_events_path: Path
    final_message_path: Path
    final_message: str
    wumw_session_path: Path | None
    wumw_savings: dict | None


def serialize_result(result: VariantResult) -> dict:
    return {
        "name": result.name,
        "returncode": result.returncode,
        "thread_id": result.thread_id,
        "input_tokens": result.input_tokens,
        "cached_input_tokens": result.cached_input_tokens,
        "output_tokens": result.output_tokens,
        "command_count": result.command_count,
        "command_counts_by_prog": result.command_counts_by_prog,
        "wumw_command_count": result.wumw_command_count,
        "raw_events_path": str(result.raw_events_path),
        "final_message_path": str(result.final_message_path),
        "wumw_session_path": str(result.wumw_session_path) if result.wumw_session_path else None,
        "wumw_savings": result.wumw_savings,
    }


def delta_pct(baseline: int | None, treatment: int | None) -> float | None:
    if baseline in (None, 0) or treatment is None:
        return None
    return 100.0 * (treatment - baseline) / baseline


def summarize_trial_results(results: list[VariantResult]) -> dict:
    by_name = {result.name: result for result in results}
    baseline = by_name["baseline_no_wumw"]
    treatment = by_name["treatment_with_wumw"]
    metrics = {}
    for key, label in METRICS:
        baseline_value = getattr(baseline, key)
        treatment_value = getattr(treatment, key)
        metrics[key] = {
            "label": label,
            "baseline": baseline_value,
            "treatment": treatment_value,
            "delta_pct": delta_pct(baseline_value, treatment_value),
        }
    return {
        "baseline": baseline.name,
        "treatment": treatment.name,
        "metrics": metrics,
    }


def aggregate_trial_summaries(trial_summaries: list[dict]) -> dict:
    aggregate = {}
    for key, label in METRICS:
        deltas = [
            summary["metrics"][key]["delta_pct"]
            for summary in trial_summaries
            if summary["metrics"][key]["delta_pct"] is not None
        ]
        if not deltas:
            stats = {
                "label": label,
                "count": 0,
                "mean_pct": None,
                "median_pct": None,
                "stdev_pct_points": None,
            }
        else:
            stats = {
                "label": label,
                "count": len(deltas),
                "mean_pct": mean(deltas),
                "median_pct": median(deltas),
                "stdev_pct_points": stdev(deltas) if len(deltas) > 1 else 0.0,
            }
        aggregate[key] = stats
    return aggregate


def format_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1f}%"


def _effective_lines(entry: dict) -> int:
    raw_lines = entry.get("stdout_lines", 0)
    if entry.get("full"):
        return raw_lines
    return entry.get("compressed_lines", raw_lines)


def _effective_bytes(entry: dict) -> float:
    raw_bytes = entry.get("stdout_bytes", 0)
    raw_lines = entry.get("stdout_lines", 0)
    eff_lines = _effective_lines(entry)
    if raw_lines <= 0:
        return float(raw_bytes)
    return raw_bytes * (eff_lines / raw_lines)


def summarize_wumw_session(path: Path) -> dict:
    entries = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        entries.append(json.loads(line))
    raw_lines = sum(e.get("stdout_lines", 0) for e in entries)
    eff_lines = sum(_effective_lines(e) for e in entries)
    raw_bytes = sum(e.get("stdout_bytes", 0) for e in entries)
    eff_bytes = sum(_effective_bytes(e) for e in entries)
    return {
        "calls": len(entries),
        "compressed_calls": sum(
            1
            for e in entries
            if (not e.get("full")) and _effective_lines(e) != e.get("stdout_lines", 0)
        ),
        "full_calls": sum(1 for e in entries if e.get("full")),
        "raw_lines": raw_lines,
        "effective_lines": eff_lines,
        "saved_lines": raw_lines - eff_lines,
        "line_savings_pct": (100.0 * (raw_lines - eff_lines) / raw_lines) if raw_lines else 0.0,
        "raw_bytes": raw_bytes,
        "effective_bytes_estimate": eff_bytes,
        "saved_bytes_estimate": raw_bytes - eff_bytes,
        "byte_savings_pct_estimate": (
            100.0 * (raw_bytes - eff_bytes) / raw_bytes if raw_bytes else 0.0
        ),
        "raw_tokens_estimate": raw_bytes / 4.0,
        "effective_tokens_estimate": eff_bytes / 4.0,
        "saved_tokens_estimate": (raw_bytes - eff_bytes) / 4.0,
    }


def parse_events(path: Path) -> dict:
    thread_id = None
    usage = {}
    command_counts = Counter()
    command_count = 0
    wumw_command_count = 0
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        event = json.loads(line)
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
        elif event.get("type") == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "command_execution":
                command_count += 1
                command = item.get("command", "")
                prog = program_name(command)
                command_counts[prog] += 1
                if " wumw " in f" {command} " or command.endswith("/wumw") or "`wumw`" in command:
                    wumw_command_count += 1
                elif "wumw " in command:
                    wumw_command_count += 1
        elif event.get("type") == "turn.completed":
            usage = event.get("usage", {})
    return {
        "thread_id": thread_id,
        "usage": usage,
        "command_count": command_count,
        "command_counts_by_prog": dict(command_counts),
        "wumw_command_count": wumw_command_count,
    }


def parse_claude_events(path: Path) -> dict:
    session_id = None
    usage = {}
    command_counts = Counter()
    command_count = 0
    wumw_command_count = 0
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = event.get("type")
        if t == "system":
            session_id = event.get("session_id")
        elif t == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") != "tool_use":
                    continue
                tool_name = block.get("name", "")
                if tool_name != "Bash":
                    continue
                command_count += 1
                command = block.get("input", {}).get("command", "")
                prog = program_name(command)
                command_counts[prog] += 1
                if "wumw" in command.split():
                    wumw_command_count += 1
                elif command.startswith("wumw ") or " wumw " in f" {command} ":
                    wumw_command_count += 1
        elif t == "result":
            u = event.get("usage", {})
            usage = {
                "input_tokens": u.get("input_tokens", 0)
                + u.get("cache_read_input_tokens", 0)
                + u.get("cache_creation_input_tokens", 0),
                "output_tokens": u.get("output_tokens", 0),
                "cache_read_input_tokens": u.get("cache_read_input_tokens", 0),
                "cache_creation_input_tokens": u.get("cache_creation_input_tokens", 0),
            }
    return {
        "session_id": session_id,
        "usage": usage,
        "command_count": command_count,
        "command_counts_by_prog": dict(command_counts),
        "wumw_command_count": wumw_command_count,
    }


def program_name(command: str) -> str:
    if not command:
        return "?"
    if "wumw " in command:
        return "wumw"
    if "/bin/zsh -lc " in command:
        payload = command.split("/bin/zsh -lc ", 1)[1].strip()
        if payload.startswith("'") and payload.endswith("'"):
            payload = payload[1:-1]
        if payload.startswith('"') and payload.endswith('"'):
            payload = payload[1:-1]
        return payload.split()[0] if payload else "zsh"
    return command.split()[0]


def run_variant(
    *,
    name: str,
    repo: Path,
    base: str,
    output_dir: Path,
    instruction_prefix: str,
    runner: str = "claude",
    prompt_override: str | None = None,
) -> VariantResult:
    if prompt_override is not None:
        prompt = prompt_override
    else:
        protocol = _RESEARCH_PROTOCOL.format(base=base)
        prompt = f"{instruction_prefix}\n\n{DEFAULT_PROMPT.format(base=base, protocol=protocol)}"
    events_path = output_dir / f"{name}.jsonl"
    last_message_path = output_dir / f"{name}.last.md"

    if runner == "claude":
        session_id = str(uuid.uuid4())
        env = os.environ.copy()
        if WUMW_VENV_BIN.exists():
            env["PATH"] = f"{WUMW_VENV_BIN}:{env.get('PATH', '')}"
        env["WUMW_SESSION"] = session_id
        command = [
            "claude",
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
            "--session-id", session_id,
            prompt,
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            cwd=repo,
            env=env,
        )
        events_path.write_text(completed.stdout)
        event_summary = parse_claude_events(events_path)
        thread_id = event_summary["session_id"]
        # Extract final message from the result event in stream-json
        final_message = ""
        for line in completed.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                if e.get("type") == "result":
                    final_message = e.get("result", "")
            except json.JSONDecodeError:
                pass
        last_message_path.write_text(final_message)
    else:
        command = [
            "codex",
            "exec",
            "--json",
            "--full-auto",
            "--cd",
            str(repo),
            "-o",
            str(last_message_path),
            prompt,
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        events_path.write_text(completed.stdout)
        event_summary = parse_events(events_path)
        thread_id = event_summary["thread_id"]
        final_message = last_message_path.read_text() if last_message_path.exists() else ""

    session_path = None
    savings = None
    if thread_id:
        candidate = repo / ".wumw" / "sessions" / f"{thread_id}.jsonl"
        if candidate.exists():
            session_path = candidate
            savings = summarize_wumw_session(candidate)
    return VariantResult(
        name=name,
        prompt=prompt,
        command=command,
        returncode=completed.returncode,
        thread_id=thread_id,
        input_tokens=event_summary["usage"].get("input_tokens"),
        cached_input_tokens=event_summary["usage"].get("cached_input_tokens")
        if runner == "codex"
        else event_summary["usage"].get("cache_read_input_tokens"),
        output_tokens=event_summary["usage"].get("output_tokens"),
        command_count=event_summary["command_count"],
        command_counts_by_prog=event_summary["command_counts_by_prog"],
        wumw_command_count=event_summary["wumw_command_count"],
        raw_events_path=events_path,
        final_message_path=last_message_path,
        final_message=final_message,
        wumw_session_path=session_path,
        wumw_savings=savings,
    )


def write_report(path: Path, repo: Path, base: str, trials: list[dict]) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": str(repo),
        "base": base,
        "trial_count": len(trials),
        "trials": [
            {
                "index": trial["index"],
                "summary": trial["summary"],
                "results": [serialize_result(result) for result in trial["results"]],
            }
            for trial in trials
        ],
        "aggregate": aggregate_trial_summaries([trial["summary"] for trial in trials]),
    }
    if len(trials) == 1:
        payload["results"] = [serialize_result(result) for result in trials[0]["results"]]
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def print_variant_details(results: list[VariantResult]) -> None:
    print("variant\treturncode\tinput_tokens\tcached_input\toutput_tokens\tcommands\twumw_cmds")
    for r in results:
        print(
            f"{r.name}\t{r.returncode}\t{r.input_tokens}\t{r.cached_input_tokens}\t"
            f"{r.output_tokens}\t{r.command_count}\t{r.wumw_command_count}"
        )
    print()
    for r in results:
        print(f"== {r.name} ==")
        print(f"thread_id: {r.thread_id}")
        print(f"events: {r.raw_events_path}")
        print(f"final_message: {r.final_message_path}")
        print(f"commands_by_prog: {json.dumps(r.command_counts_by_prog, sort_keys=True)}")
        if r.wumw_savings:
            print(
                "wumw_savings: "
                f"saved_tokens_estimate={r.wumw_savings['saved_tokens_estimate']:.2f}, "
                f"compressed_calls={r.wumw_savings['compressed_calls']}, "
                f"full_calls={r.wumw_savings['full_calls']}"
            )
        else:
            print("wumw_savings: none")
        print()


def print_summary(trials: list[dict]) -> None:
    aggregate = aggregate_trial_summaries([trial["summary"] for trial in trials])
    for trial in trials:
        print(f"== Trial {trial['index']} ==")
        print_variant_details(trial["results"])

    print("Per-trial deltas (treatment vs baseline):")
    header = f"{'trial':<7} {'input_tokens':>13} {'output_tokens':>14} {'shell_commands':>16}"
    print(header)
    print("-" * len(header))
    for trial in trials:
        metrics = trial["summary"]["metrics"]
        print(
            f"{trial['index']:<7}"
            f" {format_pct(metrics['input_tokens']['delta_pct']):>13}"
            f" {format_pct(metrics['output_tokens']['delta_pct']):>14}"
            f" {format_pct(metrics['command_count']['delta_pct']):>16}"
        )

    print()
    print("Aggregate deltas:")
    for key, _ in METRICS:
        stats = aggregate[key]
        stdev_display = (
            "n/a"
            if stats["stdev_pct_points"] is None
            else f"{stats['stdev_pct_points']:.1f}pp"
        )
        print(
            f"- {stats['label']}: mean {format_pct(stats['mean_pct'])}, "
            f"median {format_pct(stats['median_pct'])}, "
            f"stdev {stdev_display}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--base", default="main")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--runner", choices=["claude", "codex"], default="claude")
    parser.add_argument(
        "--task",
        choices=["pr-review", "forced-read"],
        default="pr-review",
        help=(
            "Task type: 'pr-review' (default) reviews commits on the branch; "
            "'forced-read' asks both agents to summarize a large file "
            f"({FORCED_READ_TARGET}) — compression is the only variable."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/tmp")
        / f"pr_review_ab_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
    )
    args = parser.parse_args()

    if args.trials < 1:
        parser.error("--trials must be >= 1")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    trials = []
    for index in range(1, args.trials + 1):
        trial_dir = args.output_dir / f"trial_{index:02d}" if args.trials > 1 else args.output_dir
        trial_dir.mkdir(parents=True, exist_ok=True)
        if args.task == "forced-read":
            baseline_kwargs = dict(
                prompt_override=FORCED_READ_BASELINE_PROMPT,
                instruction_prefix="",
            )
            treatment_kwargs = dict(
                prompt_override=FORCED_READ_TREATMENT_PROMPT,
                instruction_prefix="",
            )
        else:
            baseline_kwargs = dict(instruction_prefix=BASELINE_PREFIX)
            treatment_kwargs = dict(instruction_prefix=TREATMENT_PREFIX)
        results = [
            run_variant(
                name="baseline_no_wumw",
                repo=args.repo,
                base=args.base,
                output_dir=trial_dir,
                runner=args.runner,
                **baseline_kwargs,
            ),
            run_variant(
                name="treatment_with_wumw",
                repo=args.repo,
                base=args.base,
                output_dir=trial_dir,
                runner=args.runner,
                **treatment_kwargs,
            ),
        ]
        trials.append(
            {
                "index": index,
                "summary": summarize_trial_results(results),
                "results": results,
            }
        )

    write_report(args.output_dir / "summary.json", args.repo, args.base, trials)
    print_summary(trials)
    return 0 if all(result.returncode == 0 for trial in trials for result in trial["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
