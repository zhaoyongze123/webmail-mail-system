#!/bin/sh
set -eu

MODE="${1:-dev}"
shift || true

case "$MODE" in
  dev)
    exec docker compose -f docker-compose.yml "$@"
    ;;
  prod)
    exec docker compose -f docker-compose.prod.yml "$@"
    ;;
  *)
    echo "用法: $0 [dev|prod] <docker compose 参数...>" >&2
    exit 1
    ;;
esac
