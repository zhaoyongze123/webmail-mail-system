from __future__ import annotations

import importlib
import sys
from types import ModuleType

import fakeredis
import pydantic.networks as pydantic_networks
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from app.observability import get_recent_audit_events


class FakeSettings:
    def __init__(self) -> None:
        self.app_env = "test"
        self.app_name = "webmail-mvp"
        self.app_secret_key = "test-secret"
        self.admin_jwt_secret = "admin-secret"
        self.database_url = "sqlite+pysqlite:///:memory:"
        self.redis_url = "redis://localhost:6379/15"
        self.cors_origins = "http://localhost:5173,http://127.0.0.1:5173"
        self.session_ttl_seconds = 60
        self.session_cookie_name = "webmail_session"
        self.session_cookie_secure = False
        self.login_fail_ttl_seconds = 30
        self.login_fail_limit = 5
        self.mail_imap_host = "imap.test.local"
        self.mail_imap_port = 143
        self.mail_imap_ssl = False
        self.mail_imap_starttls = False
        self.mail_smtp_host = "smtp.test.local"
        self.mail_smtp_port = 25
        self.mail_smtp_ssl = False
        self.mail_smtp_starttls = False
        self.mail_directory_backend = "postgres"
        self.mail_directory_sqlite_path = "/tmp/test-vmail.db"
        self.mail_directory_password_mode = "plain"
        self.admin_access_token_ttl_minutes = 15
        self.admin_refresh_token_ttl_days = 7
        self.admin_bootstrap_username = "admin@example.com"
        self.admin_bootstrap_password = "Admin123456!"
        self.admin_totp_issuer = "Webmail Admin"
        self.mail_quota_enabled = True
        self.mailbox_password_scheme = "SHA512-CRYPT"
        self.admin_ip_allowlist = ""
        self.admin_ip_blocklist = ""
        self.postfix_main_cf_path = "/etc/postfix/main.cf"
        self.dovecot_config_path = "/etc/dovecot/dovecot.conf"
        self.postfix_virtual_aliases_path = "/etc/postfix/virtual"
        self.postfix_system_aliases_path = "/etc/aliases"
        self.admin_config_backup_dir = "/tmp/webmail-admin-backups"
        self.admin_audit_retention_days = 90

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def effective_admin_jwt_secret(self) -> str:
        return self.admin_jwt_secret or self.app_secret_key

    @property
    def effective_admin_bootstrap_username(self) -> str | None:
        return self.admin_bootstrap_username

    @property
    def effective_admin_bootstrap_password(self) -> str | None:
        return self.admin_bootstrap_password

    @property
    def use_sqlite_mail_directory(self) -> bool:
        return self.mail_directory_backend == "sqlite_vmail"


def build_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    fake_redis = fakeredis.FakeRedis(decode_responses=True)
    settings = FakeSettings()

    crypto_module = ModuleType("app.crypto")
    crypto_module.encrypt_text = lambda value: value
    crypto_module.decrypt_text = lambda token: token
    monkeypatch.setitem(sys.modules, "app.crypto", crypto_module)

    email_validator_module = ModuleType("email_validator")

    class EmailNotValidError(ValueError):
        pass

    class _ValidatedEmail:
        def __init__(self, email: str) -> None:
            self.normalized = email.strip().lower()
            self.local_part = self.normalized.split("@", 1)[0]

    def validate_email(value: str, check_deliverability: bool = False):
        return _ValidatedEmail(value)

    email_validator_module.EmailNotValidError = EmailNotValidError
    email_validator_module.validate_email = validate_email
    monkeypatch.setitem(sys.modules, "email_validator", email_validator_module)

    original_version = pydantic_networks.version
    monkeypatch.setattr(
        pydantic_networks,
        "version",
        lambda package_name: "2.0.0" if package_name == "email-validator" else original_version(package_name),
    )

    config_module = importlib.import_module("app.config")
    cache_module = importlib.import_module("app.cache")
    redis_client_module = importlib.import_module("app.redis_client")
    db_module = importlib.import_module("app.db")

    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cache_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cache_module, "get_redis_client", lambda: fake_redis)
    monkeypatch.setattr(redis_client_module, "get_redis_client", lambda: fake_redis)

    for module_name in [
        "app.mail_state",
        "app.mail_preferences",
        "app.mailbox",
        "app.contacts",
        "app.signatures",
        "app.auth",
        "app.main",
        "app.admin_auth",
        "app.admin_api",
    ]:
        sys.modules.pop(module_name, None)

    models_module = importlib.import_module("app.models")
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(db_module, "get_engine", lambda database_url=None: engine)
    models_module.Base.metadata.create_all(engine)

    main_module = importlib.import_module("app.main")
    monkeypatch.setattr(main_module, "get_settings", lambda: settings, raising=False)
    importlib.import_module("app.observability").reset_observability_state()
    return TestClient(main_module.app, raise_server_exceptions=False)


def build_sqlite_vmail_client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    client = build_client(monkeypatch)
    settings = importlib.import_module("app.config").get_settings()
    settings.mail_directory_backend = "sqlite_vmail"
    settings.mail_directory_sqlite_path = str(tmp_path / "vmail.db")
    settings.mail_directory_password_mode = "plain"
    with importlib.import_module("sqlite3").connect(settings.mail_directory_sqlite_path) as connection:
        connection.execute("CREATE TABLE domains (domain TEXT PRIMARY KEY)")
        connection.execute("CREATE TABLE accounts (username TEXT NOT NULL, domain TEXT NOT NULL, password TEXT, PRIMARY KEY (username, domain))")
        connection.execute(
            "CREATE TABLE aliases (source TEXT PRIMARY KEY, destination TEXT NOT NULL, active INTEGER DEFAULT 1, domain TEXT)"
        )
        connection.execute("INSERT INTO domains(domain) VALUES (?)", ("example.com",))
        connection.execute(
            "INSERT INTO accounts(username, domain, password) VALUES (?, ?, ?)",
            ("alice", "example.com", "Secret123!"),
        )
        connection.commit()
    return client


def admin_login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/admin/auth/login",
        json={"email": "admin@example.com", "password": "Admin123456!"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    return body["data"]


def auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def test_admin_login_and_me(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)

    payload = admin_login(client)
    me_response = client.get("/api/admin/auth/me", headers=auth_headers(payload["access_token"]))

    assert me_response.status_code == 200
    body = me_response.json()
    assert body["success"] is True
    assert body["data"]["email"] == "admin@example.com"
    assert body["data"]["role"] == "superadmin"


def test_admin_refresh_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    payload = admin_login(client)

    response = client.post("/api/admin/auth/refresh", json={"refresh_token": payload["refresh_token"]})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["access_token"]
    assert data["refresh_token"]


def test_admin_overview_domains_and_health(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    payload = admin_login(client)
    headers = auth_headers(payload["access_token"])

    admin_api_module = importlib.import_module("app.admin_api")
    monkeypatch.setattr(
        admin_api_module,
        "list_mail_queue",
        lambda: {
            "status": "ok",
            "detail": "当前检测到 2 条队列邮件",
            "items": [],
            "summary": {"total": 2, "deferred": 2},
            "command_result": {"ok": True},
        },
    )
    monkeypatch.setattr(
        admin_api_module,
        "get_mail_queue_snapshot",
        lambda status_filter=None, q=None: {
            "status": "ok",
            "detail": "当前检测到 2 条队列邮件",
            "items": [],
            "summary": {"total": 2, "deferred": 2, "visible_total": 0, "total_size_bytes": 0},
            "command_result": {"ok": True},
        },
    )
    monkeypatch.setattr(
        admin_api_module,
        "list_service_health",
        lambda: [
            {"name": "postfix", "status": "ok", "detail": "systemctl 显示 postfix.service 正在运行", "source": "systemctl:postfix.service"},
            {"name": "dovecot", "status": "down", "detail": "未检测到 dovecot 相关进程", "source": "pgrep:dovecot"},
            {"name": "rspamd", "status": "unavailable", "detail": "当前环境未安装 pgrep，无法探测服务进程", "source": "none"},
        ],
    )
    monkeypatch.setattr(
        admin_api_module,
        "list_disk_usage",
        lambda: [
            {"name": "/", "mount_point": "/", "filesystem": "/dev/root", "total_gb": 100.0, "used_gb": 42.0, "free_gb": 58.0, "usage_percent": 42.0, "status": "ok", "detail": "/ 已使用 42.0%", "source": "df"},
        ],
    )
    monkeypatch.setattr(
        admin_api_module,
        "list_mail_service_logs",
        lambda: [
            {"key": "postfix", "label": "Postfix 错误日志", "status": "ok", "detail": "已从 /var/log/mail.log 读取最近 2 行日志", "source": "file:/var/log/mail.log", "lines": ["postfix error line 1", "postfix error line 2"], "line_count": 2},
            {"key": "dovecot", "label": "Dovecot 错误日志", "status": "ok", "detail": "已从 /var/log/dovecot.log 读取最近 1 行日志", "source": "file:/var/log/dovecot.log", "lines": ["dovecot warning"], "line_count": 1},
        ],
    )

    create_domain_response = client.post(
        "/api/admin/domains",
        headers=headers,
        json={"name": "example.com", "quota_limit_mb": 2048, "status": "active"},
    )
    assert create_domain_response.status_code == 200

    domains_response = client.get("/api/admin/domains", headers=headers)
    assert domains_response.status_code == 200
    domains_data = domains_response.json()["data"]
    assert domains_data["items"][0]["name"] == "example.com"

    overview_response = client.get("/api/admin/overview", headers=headers)
    assert overview_response.status_code == 200
    overview = overview_response.json()["data"]
    assert overview["mail_domains"] >= 1
    assert overview["queued_jobs"] == 2
    assert "recent_audits" in overview

    health_response = client.get("/api/admin/system-health", headers=headers)
    assert health_response.status_code == 200
    health_data = health_response.json()["data"]
    assert len(health_data["items"]) >= 6
    assert health_data["items"][0]["status"] in {"ok", "down"}
    assert len(health_data["services"]) == 3
    assert health_data["services"][0]["name"] == "postfix"
    assert health_data["disks"][0]["mount_point"] == "/"
    assert health_data["logs"][0]["key"] == "postfix"
    assert health_data["logs"][0]["lines"][0] == "postfix error line 1"
    assert "cpu" in health_data
    assert "memory" in health_data
    assert "queue" in health_data


def test_admin_domain_dns_check(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    payload = admin_login(client)
    headers = auth_headers(payload["access_token"])

    create_domain_response = client.post(
        "/api/admin/domains",
        headers=headers,
        json={"name": "example.com", "quota_limit_mb": 2048, "status": "active"},
    )
    assert create_domain_response.status_code == 200
    domain_id = create_domain_response.json()["data"]["domain"]["id"]

    def fake_dns_check(domain_name: str, *, dkim_selector: str = "default") -> dict[str, object]:
        assert domain_name == "example.com"
        assert dkim_selector == "default"
        return {
            "domain": domain_name,
            "checked_at": 1747641600,
            "status": "warning",
            "checks": [
                {"key": "mx", "label": "MX", "status": "ok", "detail": "检测到 1 条 MX 记录", "records": ["10 mail.example.com."], "backend": "dig", "command_result": {"ok": True}},
                {"key": "spf", "label": "SPF", "status": "ok", "detail": "检测到 SPF 记录", "records": ["v=spf1 mx -all"], "backend": "dig", "command_result": {"ok": True}},
                {"key": "dmarc", "label": "DMARC", "status": "missing", "detail": "未检测到 DMARC 记录", "records": [], "backend": "dig", "command_result": {"ok": True}},
                {"key": "dkim", "label": "DKIM (default)", "status": "missing", "detail": "未检测到 TXT 记录", "records": [], "backend": "dig", "command_result": {"ok": True}},
            ],
        }

    admin_api_module = importlib.import_module("app.admin_api")
    monkeypatch.setattr(admin_api_module, "run_domain_dns_check", fake_dns_check)

    response = client.get(f"/api/admin/domains/{domain_id}/dns-check", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["domain"] == "example.com"
    assert body["data"]["status"] == "warning"
    assert [item["key"] for item in body["data"]["checks"]] == ["mx", "spf", "dmarc", "dkim"]

    assert any(item["event_type"] == "admin.domains.dns_check" for item in get_recent_audit_events())


def test_admin_queue_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    payload = admin_login(client)
    headers = auth_headers(payload["access_token"])

    admin_api_module = importlib.import_module("app.admin_api")

    monkeypatch.setattr(
        admin_api_module,
        "get_mail_queue_snapshot",
        lambda status_filter=None, q=None: {
            "status": "ok",
            "detail": "当前检测到 1 条队列邮件",
            "items": [
                {
                    "id": "ABCD1234",
                    "queue_id": "ABCD1234",
                    "status": "deferred",
                    "queue_name": "deferred",
                    "sender": "sender@example.com",
                    "recipients": ["target@example.com"],
                    "recipient_count": 1,
                    "message_size": 2048,
                    "arrival_time": 1747641600,
                    "created_at": 1747641600,
                    "name": "ABCD1234",
                    "description": "sender@example.com -> target@example.com",
                },
            ],
            "summary": {"total": 1, "deferred": 1, "visible_total": 1, "total_size_bytes": 2048},
            "command_result": {"ok": True},
        },
    )
    monkeypatch.setattr(
        admin_api_module,
        "flush_mail_queue",
        lambda: {
            "status": "ok",
            "detail": "已触发 Postfix 队列 flush",
            "command_result": {"ok": True},
        },
    )
    monkeypatch.setattr(
        admin_api_module,
        "delete_mail_queue_item",
        lambda queue_id: {
            "status": "ok",
            "detail": f"已请求删除队列邮件 {queue_id}",
            "command_result": {"ok": True},
        },
    )
    monkeypatch.setattr(
        admin_api_module,
        "requeue_mail_queue_item",
        lambda queue_id: {
            "status": "ok",
            "detail": f"已请求重新投递队列邮件 {queue_id}",
            "command_result": {"ok": True},
        },
    )
    monkeypatch.setattr(
        admin_api_module,
        "delete_mail_queue_items",
        lambda queue_ids: {
            "status": "ok",
            "detail": f"已删除 {len(queue_ids)} 条队列邮件",
            "deleted_count": len(queue_ids),
            "deleted_ids": queue_ids,
            "errors": [],
            "command_result": {"ok": True},
        },
    )
    monkeypatch.setattr(
        admin_api_module,
        "clear_mail_queue",
        lambda statuses=None: {
            "status": "ok",
            "detail": "已清空指定状态的队列邮件",
            "deleted_count": 1,
            "deleted_ids": ["ABCD1234"],
            "errors": [],
            "command_result": {"ok": True},
        },
    )
    monkeypatch.setattr(
        admin_api_module,
        "get_mail_queue_message",
        lambda queue_id: {
            "status": "ok",
            "detail": "已读取队列正文",
            "queue_id": queue_id,
            "content": "Subject: test\n\nbody",
            "command_result": {"ok": True},
        },
    )

    list_response = client.get("/api/admin/queue", headers=headers)
    assert list_response.status_code == 200
    list_body = list_response.json()
    assert list_body["data"]["summary"]["total"] == 1
    assert list_body["data"]["items"][0]["queue_id"] == "ABCD1234"

    flush_response = client.post("/api/admin/queue/flush", headers=headers)
    assert flush_response.status_code == 200
    assert flush_response.json()["data"]["status"] == "ok"

    delete_response = client.post("/api/admin/queue/delete", headers=headers, json={"queue_id": "ABCD1234"})
    assert delete_response.status_code == 200
    assert delete_response.json()["data"]["queue_id"] == "ABCD1234"
    assert delete_response.json()["data"]["status"] == "ok"

    detail_response = client.get("/api/admin/queue/ABCD1234", headers=headers)
    assert detail_response.status_code == 200
    assert detail_response.json()["data"]["content"].startswith("Subject:")

    requeue_response = client.post("/api/admin/queue/requeue", headers=headers, json={"queue_id": "ABCD1234"})
    assert requeue_response.status_code == 200
    assert requeue_response.json()["data"]["status"] == "ok"

    bulk_delete_response = client.post("/api/admin/queue/bulk-delete", headers=headers, json={"queue_ids": ["ABCD1234"]})
    assert bulk_delete_response.status_code == 200
    assert bulk_delete_response.json()["data"]["deleted_count"] == 1

    clear_response = client.post("/api/admin/queue/clear", headers=headers, json={"statuses": ["deferred"]})
    assert clear_response.status_code == 200
    assert clear_response.json()["data"]["status"] == "ok"

    events = [item["event_type"] for item in get_recent_audit_events()]
    assert "admin.queue.list" in events
    assert "admin.queue.flush" in events
    assert "admin.queue.delete" in events


def test_admin_rspamd_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    payload = admin_login(client)
    headers = auth_headers(payload["access_token"])

    admin_api_module = importlib.import_module("app.admin_api")
    monkeypatch.setattr(
        admin_api_module,
        "get_rspamd_thresholds",
        lambda: {
            "status": "ok",
            "detail": "已从 /etc/rspamd/local.d/actions.conf 读取 Rspamd 阈值",
            "source": "file:/etc/rspamd/local.d/actions.conf",
            "thresholds": {"reject": 15.0, "add_header": 6.0, "greylist": 4.0},
        },
    )
    monkeypatch.setattr(
        admin_api_module,
        "update_rspamd_thresholds",
        lambda thresholds: {
            "status": "ok",
            "detail": "已更新 /etc/rspamd/local.d/actions.conf 中的 Rspamd 阈值",
            "source": "file:/etc/rspamd/local.d/actions.conf",
            "thresholds": thresholds,
        },
    )
    monkeypatch.setattr(
        admin_api_module,
        "rotate_domain_dkim_key",
        lambda domain_name, selector=None: {
            "status": "ok",
            "detail": f"已为 {domain_name} 重新生成 DKIM 私钥",
            "selector": selector or "default",
            "path": f"/var/lib/rspamd/dkim/{domain_name}.default.key",
            "public_key": "-----BEGIN PUBLIC KEY-----\nTEST\n-----END PUBLIC KEY-----",
        },
    )
    monkeypatch.setattr(
        admin_api_module,
        "run_domain_dns_check",
        lambda domain_name, dkim_selector="default": {
            "domain": domain_name,
            "checked_at": 1747641600,
            "status": "warning",
            "checks": [
                {"key": "mx", "label": "MX", "status": "ok", "detail": "检测到 1 条 MX 记录", "records": ["10 mail.example.com."], "backend": "dig", "command_result": {"ok": True}},
                {"key": "spf", "label": "SPF", "status": "ok", "detail": "检测到 SPF 记录", "records": ["v=spf1 mx -all"], "backend": "dig", "command_result": {"ok": True}},
                {"key": "dmarc", "label": "DMARC", "status": "missing", "detail": "未检测到 DMARC 记录", "records": [], "backend": "dig", "command_result": {"ok": True}},
                {"key": "dkim", "label": "DKIM (default)", "status": "missing", "detail": "未检测到 TXT 记录", "records": [], "backend": "dig", "command_result": {"ok": True}},
            ],
        },
    )
    monkeypatch.setattr(
        admin_api_module,
        "get_domain_dkim_info",
        lambda domain_name: {
            "status": "unavailable",
            "detail": f"未找到 DKIM 私钥文件 /var/lib/rspamd/dkim/{domain_name}.default.key",
            "selector": "default",
            "path": f"/var/lib/rspamd/dkim/{domain_name}.default.key",
            "public_key": None,
            "exists": False,
        },
    )

    create_domain_response = client.post(
        "/api/admin/domains",
        headers=headers,
        json={"name": "example.com", "quota_limit_mb": 2048, "status": "active"},
    )
    assert create_domain_response.status_code == 200
    domain_id = create_domain_response.json()["data"]["domain"]["id"]

    overview_response = client.get("/api/admin/rspamd", headers=headers)
    assert overview_response.status_code == 200
    overview_data = overview_response.json()["data"]
    assert overview_data["thresholds"]["thresholds"]["reject"] == 15.0
    assert overview_data["domains"][0]["spf_status"] == "ok"
    assert overview_data["domains"][0]["dkim_local_status"] == "unavailable"

    update_response = client.patch(
        "/api/admin/rspamd/thresholds",
        headers=headers,
        json={"reject": 16, "add_header": 7, "greylist": 5},
    )
    assert update_response.status_code == 200
    assert update_response.json()["data"]["thresholds"]["reject"] == 16

    rotate_response = client.post(
        f"/api/admin/domains/{domain_id}/dkim/rotate",
        headers=headers,
        json={"selector": "default"},
    )
    assert rotate_response.status_code == 200
    assert rotate_response.json()["data"]["status"] == "ok"
    assert rotate_response.json()["data"]["domain"] == "example.com"


def test_admin_tls_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    payload = admin_login(client)
    headers = auth_headers(payload["access_token"])

    admin_api_module = importlib.import_module("app.admin_api")
    monkeypatch.setattr(
        admin_api_module,
        "get_tls_certificates",
        lambda: {
            "status": "ok",
            "detail": "已读取 1 份证书",
            "items": [
                {
                    "name": "mail.example.com",
                    "status": "ok",
                    "detail": "证书将于 Jun 30 23:59:59 2026 GMT 到期",
                    "certificate_path": "/etc/letsencrypt/live/mail.example.com/fullchain.pem",
                    "expires_at": "Jun 30 23:59:59 2026 GMT",
                    "domains": ["mail.example.com", "imap.example.com"],
                }
            ],
        },
    )
    monkeypatch.setattr(
        admin_api_module,
        "renew_tls_certificates",
        lambda: {
            "status": "unavailable",
            "detail": "当前环境未安装 certbot，无法触发续签",
            "command_result": {"ok": False, "exit_code": 127},
        },
    )

    overview_response = client.get("/api/admin/tls", headers=headers)
    assert overview_response.status_code == 200
    overview_data = overview_response.json()["data"]
    assert overview_data["status"] == "ok"
    assert overview_data["items"][0]["name"] == "mail.example.com"

    renew_response = client.post("/api/admin/tls/renew", headers=headers, json={"confirm": True})
    assert renew_response.status_code == 200
    assert renew_response.json()["data"]["status"] == "unavailable"


def test_admin_users_and_aliases_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    payload = admin_login(client)
    headers = auth_headers(payload["access_token"])

    admin_api_module = importlib.import_module("app.admin_api")
    monkeypatch.setattr(
        admin_api_module,
        "get_mailbox_quota_usage",
        lambda email: {
            "status": "ok",
            "detail": "已读取 Dovecot 配额使用量",
            "used_quota_mb": 12.5,
            "usage_source": "doveadm",
            "command_result": {"ok": True},
        },
    )
    monkeypatch.setattr(
        admin_api_module,
        "recalc_mailbox_quota_usage",
        lambda email: {
            "status": "ok",
            "detail": f"已触发 {email} 的配额重算",
            "command_result": {"ok": True},
        },
    )
    monkeypatch.setattr(
        admin_api_module,
        "_detect_alias_cycle",
        lambda graph, source_address, target_addresses: None,
    )

    domain_response = client.post(
        "/api/admin/domains",
        headers=headers,
        json={"name": "mail.test", "quota_limit_mb": 4096, "status": "active"},
    )
    domain_id = domain_response.json()["data"]["domain"]["id"]

    user_response = client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "alice@mail.test",
            "display_name": "Alice",
            "domain_id": domain_id,
            "password": "User123456!",
            "quota_mb": 600,
            "status": "active",
            "is_admin": False,
        },
    )
    assert user_response.status_code == 200
    user_id = user_response.json()["data"]["user"]["id"]

    alias_response = client.post(
        "/api/admin/aliases",
        headers=headers,
        json={
            "domain_id": domain_id,
            "source_address": "sales@mail.test",
            "target_addresses": ["alice@mail.test"],
        },
    )
    assert alias_response.status_code == 200

    quotas_response = client.get("/api/admin/quotas", headers=headers)
    assert quotas_response.status_code == 200
    assert quotas_response.json()["data"]["items"][0]["used_quota_mb"] == 12.5
    assert quotas_response.json()["data"]["items"][0]["usage_source"] == "doveadm"
    assert quotas_response.json()["data"]["user_items"][0]["quota_mb"] == 600
    assert quotas_response.json()["data"]["user_items"][0]["usage_source"] == "doveadm"

    update_quota_response = client.patch(
        f"/api/admin/users/{user_id}/quota",
        headers=headers,
        json={"quota_mb": 700},
    )
    assert update_quota_response.status_code == 200
    assert update_quota_response.json()["data"]["user"]["quota_mb"] == 700

    recalc_response = client.post(
        f"/api/admin/users/{user_id}/quota/recalc",
        headers=headers,
    )
    assert recalc_response.status_code == 200
    assert recalc_response.json()["data"]["result"]["status"] == "ok"

    import_response = client.post(
        "/api/admin/users/import-csv",
        headers=headers,
        json={
            "csv_content": "email,password,display_name,quota_mb,status,is_admin\nnew@mail.test,Import123!,Imported,512,active,false\n",
            "domain_id": domain_id,
        },
    )
    assert import_response.status_code == 200
    assert import_response.json()["data"]["created"] == 1

    catch_all_response = client.post(
        "/api/admin/aliases/catch-all",
        headers=headers,
        json={"domain_id": domain_id, "target_address": "alice@mail.test"},
    )
    assert catch_all_response.status_code == 200
    assert catch_all_response.json()["data"]["alias"]["source_address"] == "@mail.test"


def test_parse_doveadm_quota_output_uses_storage_value_column(monkeypatch: pytest.MonkeyPatch) -> None:
    admin_system_module = importlib.import_module("app.admin_system")

    parsed = admin_system_module._parse_quota_kib(
        """
        Quota name Type    Value Limit  %
        User quota STORAGE 12    500    2
        User quota MESSAGE 1     -      0
        """
    )

    assert parsed == 12


def test_admin_created_user_can_login_with_local_password(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    payload = admin_login(client)
    headers = auth_headers(payload["access_token"])

    domain_response = client.post(
        "/api/admin/domains",
        headers=headers,
        json={"name": "local.test", "quota_limit_mb": 1024, "status": "active"},
    )
    assert domain_response.status_code == 200
    domain_id = domain_response.json()["data"]["domain"]["id"]

    create_response = client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "bob@local.test",
            "display_name": "Bob",
            "domain_id": domain_id,
            "password": "Local123456!",
            "quota_mb": 256,
            "status": "active",
            "is_admin": False,
        },
    )

    assert create_response.status_code == 200
    login_response = client.post(
        "/api/auth/login",
        json={"email": "bob@local.test", "password": "Local123456!", "remember": False},
    )
    assert login_response.status_code == 200
    body = login_response.json()
    assert body["success"] is True
    assert body["data"]["email"] == "bob@local.test"


def test_admin_reset_password_invalidates_old_password(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    payload = admin_login(client)
    headers = auth_headers(payload["access_token"])

    domain_response = client.post(
        "/api/admin/domains",
        headers=headers,
        json={"name": "reset.test", "quota_limit_mb": 1024, "status": "active"},
    )
    assert domain_response.status_code == 200
    domain_id = domain_response.json()["data"]["domain"]["id"]

    create_response = client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "carol@reset.test",
            "display_name": "Carol",
            "domain_id": domain_id,
            "password": "Before123456!",
            "quota_mb": 256,
            "status": "active",
            "is_admin": False,
        },
    )
    assert create_response.status_code == 200
    user_id = create_response.json()["data"]["user"]["id"]

    reset_response = client.post(
        f"/api/admin/users/{user_id}/reset-password",
        headers=headers,
        json={"password": "After123456!"},
    )
    assert reset_response.status_code == 200
    assert reset_response.json()["data"]["password_reset"] is True

    old_login_response = client.post(
        "/api/auth/login",
        json={"email": "carol@reset.test", "password": "Before123456!", "remember": False},
    )
    assert old_login_response.status_code == 401

    new_login_response = client.post(
        "/api/auth/login",
        json={"email": "carol@reset.test", "password": "After123456!", "remember": False},
    )
    assert new_login_response.status_code == 200
    assert new_login_response.json()["data"]["email"] == "carol@reset.test"


def test_admin_action_history_and_dashboard_trends(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    payload = admin_login(client)
    headers = auth_headers(payload["access_token"])

    admin_api_module = importlib.import_module("app.admin_api")
    monkeypatch.setattr(
        admin_api_module,
        "list_mail_queue",
        lambda: {
            "status": "ok",
            "detail": "当前检测到 2 条队列邮件",
            "items": [],
            "summary": {"total": 2, "deferred": 1, "active": 1},
            "command_result": {"ok": True},
        },
    )
    monkeypatch.setattr(
        admin_api_module,
        "get_online_dovecot_users",
        lambda: {"status": "ok", "detail": "当前在线 3 人", "count": 3},
    )

    history_response = client.get("/api/admin/action-history", headers=headers)
    assert history_response.status_code == 200
    assert history_response.json()["data"]["items"] == []

    overview_response = client.get("/api/admin/overview", headers=headers)
    assert overview_response.status_code == 200
    assert overview_response.json()["data"]["online_users"]["count"] == 3

    trends_response = client.get("/api/admin/dashboard/trends?period=7d", headers=headers)
    assert trends_response.status_code == 200
    assert trends_response.json()["data"]["period"] == "7d"


def test_disabled_mailbox_user_cannot_login(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    payload = admin_login(client)
    headers = auth_headers(payload["access_token"])

    domain_response = client.post(
        "/api/admin/domains",
        headers=headers,
        json={"name": "disabled.test", "quota_limit_mb": 1024, "status": "active"},
    )
    assert domain_response.status_code == 200
    domain_id = domain_response.json()["data"]["domain"]["id"]

    create_response = client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "dave@disabled.test",
            "display_name": "Dave",
            "domain_id": domain_id,
            "password": "Disabled123456!",
            "quota_mb": 256,
            "status": "active",
            "is_admin": False,
        },
    )
    assert create_response.status_code == 200
    user_id = create_response.json()["data"]["user"]["id"]

    disable_response = client.patch(
        f"/api/admin/users/{user_id}",
        headers=headers,
        json={"status": "disabled"},
    )
    assert disable_response.status_code == 200
    assert disable_response.json()["data"]["user"]["status"] == "disabled"

    login_response = client.post(
        "/api/auth/login",
        json={"email": "dave@disabled.test", "password": "Disabled123456!", "remember": False},
    )
    assert login_response.status_code == 403
    assert login_response.json()["error"]["code"] == "AUTH_ACCOUNT_DISABLED"


def test_sqlite_vmail_directory_sync_and_login(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client = build_sqlite_vmail_client(monkeypatch, tmp_path)

    payload = admin_login(client)
    headers = auth_headers(payload["access_token"])

    domains_response = client.get("/api/admin/domains", headers=headers)
    assert domains_response.status_code == 200
    domains = domains_response.json()["data"]["items"]
    assert domains[0]["name"] == "example.com"
    assert domains[0]["user_count"] == 1

    users_response = client.get("/api/admin/users", headers=headers)
    assert users_response.status_code == 200
    users = users_response.json()["data"]["items"]
    assert users[0]["email"] == "alice@example.com"

    login_response = client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "Secret123!", "remember": False},
    )
    assert login_response.status_code == 200
    assert login_response.json()["data"]["email"] == "alice@example.com"

    reset_response = client.post(
        f"/api/admin/users/{users[0]['id']}/reset-password",
        headers=headers,
        json={"password": "NewSecret123!"},
    )
    assert reset_response.status_code == 200

    old_login = client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "Secret123!", "remember": False},
    )
    assert old_login.status_code == 401

    new_login = client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "NewSecret123!", "remember": False},
    )
    assert new_login.status_code == 200


def test_sqlite_vmail_directory_create_and_delete_user(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client = build_sqlite_vmail_client(monkeypatch, tmp_path)
    payload = admin_login(client)
    headers = auth_headers(payload["access_token"])

    domains_response = client.get("/api/admin/domains", headers=headers)
    assert domains_response.status_code == 200
    domain_id = domains_response.json()["data"]["items"][0]["id"]

    create_response = client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "bob@example.com",
            "display_name": "Bob",
            "domain_id": domain_id,
            "password": "Bob123456!",
            "quota_mb": 256,
            "status": "active",
            "is_admin": False,
        },
    )
    assert create_response.status_code == 200
    user_id = create_response.json()["data"]["user"]["id"]

    login_response = client.post(
        "/api/auth/login",
        json={"email": "bob@example.com", "password": "Bob123456!", "remember": False},
    )
    assert login_response.status_code == 200

    delete_response = client.delete(f"/api/admin/users/{user_id}", headers=headers)
    assert delete_response.status_code == 200

    deleted_login_response = client.post(
        "/api/auth/login",
        json={"email": "bob@example.com", "password": "Bob123456!", "remember": False},
    )
    assert deleted_login_response.status_code == 401


def test_sqlite_vmail_directory_quota_sync(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client = build_sqlite_vmail_client(monkeypatch, tmp_path)
    payload = admin_login(client)
    headers = auth_headers(payload["access_token"])

    sqlite3_module = importlib.import_module("sqlite3")
    settings = importlib.import_module("app.config").get_settings()
    with sqlite3_module.connect(settings.mail_directory_sqlite_path) as connection:
        connection.execute("ALTER TABLE accounts ADD COLUMN quota_mb INTEGER NOT NULL DEFAULT 500")
        connection.execute(
            "UPDATE accounts SET quota_mb = ? WHERE username = ? AND domain = ?",
            (640, "alice", "example.com"),
        )
        connection.commit()

    users_response = client.get("/api/admin/users", headers=headers)
    assert users_response.status_code == 200
    user = users_response.json()["data"]["items"][0]
    assert user["quota_mb"] == 640

    update_response = client.patch(
        f"/api/admin/users/{user['id']}/quota",
        headers=headers,
        json={"quota_mb": 768},
    )
    assert update_response.status_code == 200
    assert update_response.json()["data"]["user"]["quota_mb"] == 768

    with sqlite3_module.connect(settings.mail_directory_sqlite_path) as connection:
        row = connection.execute(
            "SELECT quota_mb FROM accounts WHERE username = ? AND domain = ?",
            ("alice", "example.com"),
        ).fetchone()
    assert row is not None
    assert int(row[0]) == 768
