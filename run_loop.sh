#!/usr/bin/env bash
# wumw agent loop
# Usage: ./run_loop.sh [max_iterations]

set -euo pipefail

MAX_ITER=${1:-10}
ITER=0
LOOP_PROMPT="${LOOP_PROMPT:-loop.md}"
LOG_DIR="logs"
LOG_FILE="$LOG_DIR/loop_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$LOG_DIR"

log() {
    echo "$1" | tee -a "$LOG_FILE"
}

log "Starting wumw agent loop (max $MAX_ITER iterations) — $LOG_FILE"

while [ $ITER -lt $MAX_ITER ]; do
    ITER=$((ITER + 1))
    log ""
    log "=== Iteration $ITER / $MAX_ITER === $(date)"

    OUTPUT=$(claude --print --dangerously-skip-permissions --model "${WUMW_MODEL:-claude-haiku-4-5-20251001}" < "$LOOP_PROMPT" 2>&1)
    echo "$OUTPUT" | tee -a "$LOG_FILE"

    # Stop conditions
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
