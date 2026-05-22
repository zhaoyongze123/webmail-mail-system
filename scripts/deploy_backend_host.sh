#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/opt/webmail/backend}"
REPO_ROOT="${REPO_ROOT:-/root/webmail-mail-system}"
ENV_SOURCE_FILE="${ENV_SOURCE_FILE:-/root/webmail-mail-system/.env.prod}"
SYSTEMD_UNIT_NAME="${SYSTEMD_UNIT_NAME:-webmail-backend.service}"
SYSTEMD_UNIT_SOURCE_FILE="${SYSTEMD_UNIT_SOURCE_FILE:-/root/webmail-mail-system/deploy/systemd/webmail-backend.service}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
POSTFIX_MAIN_CF="${POSTFIX_MAIN_CF:-/etc/postfix/main.cf}"
RSPAMD_LOCAL_DIR="${RSPAMD_LOCAL_DIR:-/etc/rspamd/local.d}"
RSPAMD_REDIS_CONF="${RSPAMD_REDIS_CONF:-$RSPAMD_LOCAL_DIR/redis.conf}"
RSPAMD_MILTER_HEADERS_CONF="${RSPAMD_MILTER_HEADERS_CONF:-$RSPAMD_LOCAL_DIR/milter_headers.conf}"

if [[ ! -d "$REPO_ROOT/backend" ]]; then
  echo "缺少后端源码目录: $REPO_ROOT/backend" >&2
  exit 1
fi

mkdir -p "$PROJECT_ROOT"
rsync -a --delete \
  --exclude ".venv" \
  --exclude "__pycache__" \
  --exclude ".pytest_cache" \
  "$REPO_ROOT/backend/" "$PROJECT_ROOT/"

if [[ ! -f "$ENV_SOURCE_FILE" ]]; then
  echo "缺少环境变量文件: $ENV_SOURCE_FILE" >&2
  exit 1
fi

cp "$ENV_SOURCE_FILE" "$PROJECT_ROOT/.env.prod"

if [[ ! -f "$SYSTEMD_UNIT_SOURCE_FILE" ]]; then
  echo "缺少 systemd unit 模板: $SYSTEMD_UNIT_SOURCE_FILE" >&2
  exit 1
fi

cp "$SYSTEMD_UNIT_SOURCE_FILE" "/etc/systemd/system/$SYSTEMD_UNIT_NAME"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y postfix postfix-sqlite rspamd dovecot-sieve rsync curl python3-venv

mkdir -p "$RSPAMD_LOCAL_DIR"
cat > "$RSPAMD_REDIS_CONF" <<'EOF'
servers = "127.0.0.1:6379";
EOF

cat > "$RSPAMD_MILTER_HEADERS_CONF" <<'EOF'
use = ["x-spamd-bar", "x-spam-level", "x-spam-status", "x-virus", "authentication-results", "spam-header"];
extended_spam_headers = true;
skip_local = false;
skip_authenticated = false;
local_headers = ["x-spamd-bar", "x-spam-level", "x-spam-status", "authentication-results", "spam-header"];
authenticated_headers = ["x-spamd-bar", "x-spam-level", "x-spam-status", "authentication-results", "spam-header"];
routines {
  spam-header = {
    header = "X-Spam";
    value = "Yes";
    remove = 0;
  }
}
EOF

postconf -e "smtp_tls_security_level = none"
postconf -e "virtual_alias_maps = sqlite:/etc/postfix/sql/aliases.cf"
postconf -e "smtpd_milters = inet:127.0.0.1:11332"
postconf -e 'non_smtpd_milters = $smtpd_milters'
postconf -e "milter_protocol = 6"
postconf -e "milter_default_action = accept"
newaliases || true
rspamadm configtest
postfix check

if [[ ! -d "$PROJECT_ROOT/.venv" ]]; then
  "$PYTHON_BIN" -m venv "$PROJECT_ROOT/.venv"
fi

"$PROJECT_ROOT/.venv/bin/pip" install --upgrade pip
"$PROJECT_ROOT/.venv/bin/pip" install -r "$PROJECT_ROOT/requirements.txt"

cd "$PROJECT_ROOT"
set -a
. "$PROJECT_ROOT/.env.prod"
set +a
"$PROJECT_ROOT/.venv/bin/alembic" upgrade head

systemctl daemon-reload
systemctl enable postfix rspamd
systemctl restart dovecot
systemctl restart rspamd
for _ in $(seq 1 20); do
  if ss -lnt | grep -q '127.0.0.1:11332'; then
    break
  fi
  sleep 1
done
systemctl restart postfix
systemctl enable "$SYSTEMD_UNIT_NAME"
systemctl restart "$SYSTEMD_UNIT_NAME"
systemctl --no-pager --full status "$SYSTEMD_UNIT_NAME"

curl -fsS "http://${BACKEND_HOST:-127.0.0.1}:${BACKEND_PORT:-8000}/api/health"
