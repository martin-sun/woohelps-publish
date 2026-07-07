#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"

SERVICE_NAME="woohelps-publish.service"

# 杀掉所有手动启动的 web 进程（可能占用 8000 端口导致 systemd 服务起不来）
# 只匹配带 --reload 的手动开发进程，避免误杀 systemd 服务进程
kill_manual_web() {
    local pids
    pids=$(pgrep -f "uvicorn src\.web\.app:app.*--reload" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "Killing manual web processes still holding port 8000..."
        echo "$pids" | xargs -r kill -TERM 2>/dev/null || true
        sleep 2
        # still alive? force kill
        pids=$(pgrep -f "uvicorn src\.web\.app:app.*--reload" 2>/dev/null || true)
        if [ -n "$pids" ]; then
            echo "$pids" | xargs -r kill -KILL 2>/dev/null || true
            sleep 1
        fi
    fi
}

usage() {
    echo "Usage: ./dev.sh <command>"
    echo ""
    echo "Commands:"
    echo "  start     Start $SERVICE_NAME"
    echo "  stop      Stop $SERVICE_NAME and kill any manual web process on :8000"
    echo "  restart   Stop service, kill manual web process, then start service"
    echo "  status    Show $SERVICE_NAME status"
    echo "  install   Install dependencies via uv"
    echo "  setup     Copy .env.example → .env if not exists"

}

case "${1:-}" in
    start)
        sudo systemctl start "$SERVICE_NAME"
        sudo systemctl status "$SERVICE_NAME" --no-pager || true
        ;;
    stop)
        sudo systemctl stop "$SERVICE_NAME" || true
        kill_manual_web
        echo "Stopped $SERVICE_NAME and cleaned up manual web processes."
        ;;
    restart)
        sudo systemctl stop "$SERVICE_NAME" || true
        kill_manual_web
        sudo systemctl start "$SERVICE_NAME"
        sudo systemctl status "$SERVICE_NAME" --no-pager || true
        ;;
    status)
        sudo systemctl status "$SERVICE_NAME" --no-pager || true
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
