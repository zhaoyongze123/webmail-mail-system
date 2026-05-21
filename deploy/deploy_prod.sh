#!/bin/sh
set -eu

PROJECT_DIR="${PROJECT_DIR:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}"
COMPOSE_FILE="${COMPOSE_FILE:-$PROJECT_DIR/docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env.prod}"
PROJECT_NAME="${PROJECT_NAME:-webmail-mail-system}"

if [ ! -f "$ENV_FILE" ]; then
  echo "缺少生产环境变量文件: $ENV_FILE" >&2
  exit 1
fi

docker compose \
  --project-name "$PROJECT_NAME" \
  --env-file "$ENV_FILE" \
  -f "$COMPOSE_FILE" \
  pull

docker compose \
  --project-name "$PROJECT_NAME" \
  --env-file "$ENV_FILE" \
  -f "$COMPOSE_FILE" \
  up -d --remove-orphans

docker compose \
  --project-name "$PROJECT_NAME" \
  --env-file "$ENV_FILE" \
  -f "$COMPOSE_FILE" \
  ps
