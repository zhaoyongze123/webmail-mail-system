from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
