#!/usr/bin/env bash
# Codex task loop for the wumw backlog.
# Usage: ./run_task_loop.sh [max_iterations]

set -euo pipefail

MAX_ITER=${1:-10}
ITER=0
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOOP_PROMPT="${LOOP_PROMPT:-loop.md}"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs}"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$LOG_DIR/task_loop_${STAMP}.log"
MODEL="${WUMW_MODEL:-}"
CODEX_ARGS=(
  exec
  --json
  --full-auto
  --cd "$ROOT_DIR"
)

mkdir -p "$LOG_DIR"

log() {
    echo "$1" | tee -a "$LOG_FILE"
}

task_status_json() {
    PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}" \
        python3 -m wumw.task_status "$ROOT_DIR/tasklist.md"
}

describe_next_task() {
    python3 -c '
import json
import sys

data = json.load(sys.stdin)
task = data.get("next_task")
if task is None:
    print("All tasks are done.")
else:
    print("{phase}: {title}".format(phase=task["phase"], title=task["title"]))
' <<<"$1"
}

run_codex_once() {
    local prompt_file=$1
    local iter=$2
    local msg_file="$LOG_DIR/task_loop_${STAMP}_iter${iter}.last.md"
    local json_file="$LOG_DIR/task_loop_${STAMP}_iter${iter}.jsonl"
    local -a cmd=("codex")

    cmd+=("${CODEX_ARGS[@]}")
    if [ -n "$MODEL" ]; then
        cmd+=(--model "$MODEL")
    fi
    cmd+=(-o "$msg_file" "$(cat "$prompt_file")")

    "${cmd[@]}" | tee "$json_file" >> "$LOG_FILE"
    if [ -f "$msg_file" ]; then
        cat "$msg_file" >> "$LOG_FILE"
        printf '\n' >> "$LOG_FILE"
        cat "$msg_file"
    fi
}

log "Starting Codex task loop (max $MAX_ITER iterations) — $LOG_FILE"
log "Loop prompt: $LOOP_PROMPT"

while [ "$ITER" -lt "$MAX_ITER" ]; do
    ITER=$((ITER + 1))
    log ""
    log "=== Iteration $ITER / $MAX_ITER === $(date)"

    TASK_STATUS_JSON="$(task_status_json)"
    log "Task status: $TASK_STATUS_JSON"
    NEXT_TASK="$(describe_next_task "$TASK_STATUS_JSON")"
    log "Next task: $NEXT_TASK"
    if echo "$TASK_STATUS_JSON" | python3 -c 'import json, sys; sys.exit(0 if json.load(sys.stdin)["all_tasks_done"] else 1)'; then
        log "All tasks complete. Stopping."
        exit 0
    fi

    OUTPUT="$(run_codex_once "$ROOT_DIR/$LOOP_PROMPT" "$ITER" || true)"
    echo "$OUTPUT" | tee -a "$LOG_FILE"

    if echo "$OUTPUT" | grep -qi "all tasks are done"; then
        log "All tasks complete. Stopping."
        exit 0
    fi

    if echo "$OUTPUT" | grep -qi "nothing to do"; then
        log "Nothing to do. Stopping."
        exit 0
    fi
done

log ""
log "Reached max iterations ($MAX_ITER). Stopping."
