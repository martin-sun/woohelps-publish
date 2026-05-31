#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

usage() {
    echo "Usage: ./dev.sh <command>"
    echo ""
    echo "Commands:"
    echo "  web       Start web admin UI (FastAPI on :8000)"
    echo "  install   Install dependencies via uv"
    echo "  setup     Copy .env.example → .env if not exists"

}

case "${1:-}" in
    web)
        echo "Starting web admin UI on http://127.0.0.1:8000"
        uv run uvicorn src.web.app:app --host 127.0.0.1 --port 8000 --reload
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
