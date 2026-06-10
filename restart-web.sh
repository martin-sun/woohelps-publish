#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PID_FILE=".web.pid"
LOG_FILE="web.log"

stop_service() {
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "Stopping existing web service (PID: $pid)..."
            kill "$pid"
            sleep 1
        fi
        rm -f "$PID_FILE"
    fi

    # Fallback: kill any remaining uvicorn processes for this app
    pkill -f "uvicorn src.web.app:app" 2>/dev/null || true
}

start_service() {
    echo "Starting web admin UI on http://127.0.0.1:8000"
    nohup uv run uvicorn src.web.app:app --host 127.0.0.1 --port 8000 --reload > "$LOG_FILE" 2>&1 &
    local pid=$!
    echo "$pid" > "$PID_FILE"
    echo "Web service started with PID: $pid"
    echo "Logs: tail -f $LOG_FILE"

    sleep 2
    echo "--- Recent logs ---"
    tail -n 15 "$LOG_FILE" 2>/dev/null || true
}

case "${1:-restart}" in
    start)
        start_service
        ;;
    stop)
        stop_service
        echo "Web service stopped."
        ;;
    restart|*)
        stop_service
        sleep 1
        start_service
        ;;
esac
