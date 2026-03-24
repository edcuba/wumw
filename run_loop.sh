#!/usr/bin/env bash
# wumw agent loop
# Usage: ./run_loop.sh [max_iterations]

set -euo pipefail

MAX_ITER=${1:-10}
ITER=0
LOOP_PROMPT="loop.md"

echo "Starting wumw agent loop (max $MAX_ITER iterations)"

while [ $ITER -lt $MAX_ITER ]; do
    ITER=$((ITER + 1))
    echo ""
    echo "=== Iteration $ITER / $MAX_ITER ==="

    OUTPUT=$(claude --print --dangerously-skip-permissions < "$LOOP_PROMPT" 2>&1)
    echo "$OUTPUT"

    # Stop conditions
    if echo "$OUTPUT" | grep -qi "all tasks are done"; then
        echo "All tasks complete. Stopping."
        exit 0
    fi

    if echo "$OUTPUT" | grep -qi "nothing to do"; then
        echo "Nothing to do. Stopping."
        exit 0
    fi

done

echo ""
echo "Reached max iterations ($MAX_ITER). Stopping."
