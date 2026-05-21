from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Webmail 后端运行配置。

    这里集中定义系统运行时所需的环境变量映射、默认值与少量派生属性，
    以便业务代码只依赖 `get_settings()` 返回的单一配置对象。
    """
    app_env: str = Field(default="development", alias="APP_ENV")
    app_name: str = Field(default="webmail-mvp", alias="APP_NAME")
    app_secret_key: str = Field(default="change-me-in-local-env", alias="APP_SECRET_KEY")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    database_url: str = Field(
        default="postgresql+psycopg://webmail:webmail_dev_password@localhost:5432/webmail",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    session_ttl_seconds: int = Field(default=28800, alias="SESSION_TTL_SECONDS")
    login_fail_ttl_seconds: int = Field(default=900, alias="LOGIN_FAIL_TTL_SECONDS")
    login_fail_limit: int = Field(default=5, alias="LOGIN_FAIL_LIMIT")
    session_cookie_name: str = Field(default="webmail_session", alias="SESSION_COOKIE_NAME")
    session_cookie_secure: bool = Field(default=False, alias="SESSION_COOKIE_SECURE")
    mail_imap_host: str = Field(default="14.103.117.188", alias="MAIL_IMAP_HOST")
    mail_imap_port: int = Field(default=143, alias="MAIL_IMAP_PORT")
    mail_imap_ssl: bool = Field(default=False, alias="MAIL_IMAP_SSL")
    mail_imap_starttls: bool = Field(default=False, alias="MAIL_IMAP_STARTTLS")
    mail_smtp_host: str = Field(default="14.103.117.188", alias="MAIL_SMTP_HOST")
    mail_smtp_port: int = Field(default=25, alias="MAIL_SMTP_PORT")
    mail_smtp_ssl: bool = Field(default=False, alias="MAIL_SMTP_SSL")
    mail_smtp_starttls: bool = Field(default=False, alias="MAIL_SMTP_STARTTLS")
    attachment_max_mb: int = Field(default=9, alias="ATTACHMENT_MAX_MB")
    attachment_ttl_seconds: int = Field(default=3600, alias="ATTACHMENT_TTL_SECONDS")
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="CORS_ORIGINS",
    )
    admin_jwt_secret: str | None = Field(default=None, alias="ADMIN_JWT_SECRET")
    admin_access_token_ttl_minutes: int = Field(default=15, alias="ADMIN_ACCESS_TOKEN_TTL_MINUTES")
    admin_refresh_token_ttl_days: int = Field(default=7, alias="ADMIN_REFRESH_TOKEN_TTL_DAYS")
    admin_bootstrap_username: str | None = Field(default=None, alias="ADMIN_BOOTSTRAP_USERNAME")
    admin_bootstrap_password: str | None = Field(default=None, alias="ADMIN_BOOTSTRAP_PASSWORD")
    admin_totp_issuer: str = Field(default="Webmail Admin", alias="ADMIN_TOTP_ISSUER")
    mail_quota_enabled: bool = Field(default=True, alias="MAIL_QUOTA_ENABLED")
    mailbox_password_scheme: str = Field(default="SHA512-CRYPT", alias="MAILBOX_PASSWORD_SCHEME")
    mail_directory_backend: str = Field(default="postgres", alias="MAIL_DIRECTORY_BACKEND")
    mail_directory_sqlite_path: str = Field(default="/var/vmail/vmail.db", alias="MAIL_DIRECTORY_SQLITE_PATH")
    mail_directory_password_mode: str = Field(default="plain", alias="MAIL_DIRECTORY_PASSWORD_MODE")
    rspamd_enabled: bool = Field(default=True, alias="RSPAMD_ENABLED")
    rspamd_actions_config_path: str = Field(default="/etc/rspamd/local.d/actions.conf", alias="RSPAMD_ACTIONS_CONFIG_PATH")
    rspamd_dkim_key_dir: str = Field(default="/var/lib/rspamd/dkim", alias="RSPAMD_DKIM_KEY_DIR")
    rspamd_default_dkim_selector: str = Field(default="default", alias="RSPAMD_DKIM_SELECTOR")
    tls_enabled: bool = Field(default=True, alias="TLS_ENABLED")
    tls_live_dir: str = Field(default="/etc/letsencrypt/live", alias="TLS_LIVE_DIR")
    tls_certbot_command: str = Field(default="certbot", alias="TLS_CERTBOT_COMMAND")
    postfix_main_cf_path: str = Field(default="/etc/postfix/main.cf", alias="POSTFIX_MAIN_CF_PATH")
    dovecot_config_path: str = Field(default="/etc/dovecot/dovecot.conf", alias="DOVECOT_CONFIG_PATH")
    postfix_virtual_aliases_path: str = Field(default="/etc/postfix/virtual", alias="POSTFIX_VIRTUAL_ALIASES_PATH")
    postfix_system_aliases_path: str = Field(default="/etc/aliases", alias="POSTFIX_SYSTEM_ALIASES_PATH")
    admin_config_backup_dir: str = Field(default="/var/backups/webmail-admin", alias="ADMIN_CONFIG_BACKUP_DIR")
    admin_audit_retention_days: int = Field(default=90, alias="ADMIN_AUDIT_RETENTION_DAYS")
    admin_ip_allowlist: str = Field(default="", alias="ADMIN_IP_ALLOWLIST")
    admin_ip_blocklist: str = Field(default="", alias="ADMIN_IP_BLOCKLIST")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        """返回已清洗的跨域源列表。"""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def effective_admin_jwt_secret(self) -> str:
        """返回后台鉴权使用的实际 JWT 密钥。"""
        return self.admin_jwt_secret or self.app_secret_key

    @property
    def effective_admin_bootstrap_username(self) -> str | None:
        """返回可用于初始化后台管理员的用户名。"""
        if self.admin_bootstrap_username:
            return self.admin_bootstrap_username.strip() or None
        if self.app_env in {"development", "test"}:
            return "admin"
        return None

    @property
    def effective_admin_bootstrap_password(self) -> str | None:
        """返回可用于初始化后台管理员的密码。"""
        if self.admin_bootstrap_password:
            return self.admin_bootstrap_password
        if self.app_env in {"development", "test"}:
            return "Admin123456!"
        return None

    @property
    def admin_ip_allowlist_values(self) -> list[str]:
        """返回后台 IP 白名单列表。"""
        return [item.strip() for item in self.admin_ip_allowlist.split(",") if item.strip()]

    @property
    def admin_ip_blocklist_values(self) -> list[str]:
        """返回后台 IP 黑名单列表。"""
        return [item.strip() for item in self.admin_ip_blocklist.split(",") if item.strip()]

    @property
    def use_sqlite_mail_directory(self) -> bool:
        """返回当前是否启用基于 vmail.db 的邮件目录真源。"""
        return self.mail_directory_backend.strip().lower() == "sqlite_vmail"


@lru_cache
def get_settings() -> Settings:
    """获取进程内缓存的配置实例。"""
    return Settings()
"""应用配置读取与派生配置封装。

该模块统一从环境变量和 `.env` 文件读取运行参数，并提供少量派生属性，
供数据库、认证、邮件协议与管理后台等模块复用。
"""
