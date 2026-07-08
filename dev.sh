#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

SERVICE_NAME="woohelps-publish.service"
OS_TYPE="$(uname -s)"

is_macos() {
    [ "$OS_TYPE" = "Darwin" ]
}

# 杀掉所有手动启动的 web 进程（可能占用 8000 端口导致 systemd 服务起不来）
# 只匹配带 --reload 的手动开发进程，避免误杀 systemd 服务进程
kill_manual_web() {
    local pids
    pids=$(pgrep -f "uvicorn src\.web\.app:app.*--reload" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "Killing manual web processes still holding port 8000..."
        # 不使用 xargs -r，BSD xargs 不支持
        echo "$pids" | xargs kill -TERM 2>/dev/null || true
        sleep 2
        # still alive? force kill
        pids=$(pgrep -f "uvicorn src\.web\.app:app.*--reload" 2>/dev/null || true)
        if [ -n "$pids" ]; then
            echo "$pids" | xargs kill -KILL 2>/dev/null || true
            sleep 1
        fi
    fi
}

start_service() {
    if is_macos; then
        echo "Starting web admin UI on http://127.0.0.1:8000 (macOS dev mode)"
        uv run uvicorn src.web.app:app --host 127.0.0.1 --port 8000 --reload
    else
        sudo systemctl start "$SERVICE_NAME"
        sudo systemctl status "$SERVICE_NAME" --no-pager || true
    fi
}

stop_service() {
    if is_macos; then
        kill_manual_web
        echo "Stopped manual web processes."
    else
        sudo systemctl stop "$SERVICE_NAME" || true
        kill_manual_web
        echo "Stopped $SERVICE_NAME and cleaned up manual web processes."
    fi
}

status_service() {
    if is_macos; then
        local pids
        pids=$(pgrep -f "uvicorn src\.web\.app:app" 2>/dev/null || true)
        if [ -n "$pids" ]; then
            echo "Web process running (PID: $(echo "$pids" | tr '\n' ' '))"
        else
            echo "No web process running."
        fi
    else
        sudo systemctl status "$SERVICE_NAME" --no-pager || true
    fi
}

usage() {
    echo "Usage: ./dev.sh <command>"
    echo ""
    echo "Commands:"
    if is_macos; then
        echo "  start     Start web admin UI (uvicorn --reload on :8000)"
        echo "  stop      Stop manual web process on :8000"
        echo "  restart   Stop then start web process"
        echo "  status    Show web process status"
    else
        echo "  start     Start $SERVICE_NAME"
        echo "  stop      Stop $SERVICE_NAME and kill any manual web process on :8000"
        echo "  restart   Stop service, kill manual web process, then start service"
        echo "  status    Show $SERVICE_NAME status"
    fi
    echo "  install   Install dependencies via uv"
    echo "  setup     Copy .env.example → .env if not exists"
}

case "${1:-}" in
    start)
        start_service
        ;;
    stop)
        stop_service
        ;;
    restart)
        if is_macos; then
            kill_manual_web
            start_service
        else
            sudo systemctl stop "$SERVICE_NAME" || true
            kill_manual_web
            sudo systemctl start "$SERVICE_NAME"
            sudo systemctl status "$SERVICE_NAME" --no-pager || true
        fi
        ;;
    status)
        status_service
        ;;
    install)
        uv sync
        ;;
    setup)
        if [ ! -f .env ]; then
            cp .env.example .env
            echo "Created .env from .env.example — fill in your keys"
        else
            echo ".env already exists, skipping"
        fi
        ;;
    *)
        usage
        exit 1
        ;;
esac
