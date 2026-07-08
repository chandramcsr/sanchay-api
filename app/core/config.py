from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    All configuration comes from environment variables (.env locally,
    real env vars in production). Nothing here is a secret default —
    JWT_SECRET_KEY has no fallback on purpose, so a misconfigured
    deployment fails loudly at startup instead of silently signing
    tokens with a well-known default.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./dev.db"
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days
    cors_origins: str = "http://localhost:5173,https://chandramcsr.github.io"

    # Email (password reset). Optional on purpose: if unset, the reset
    # link is logged instead of emailed — keeps local dev/tests working
    # with zero config, same pattern as everything else in this app
    # that degrades gracefully rather than requiring every env var.
    resend_api_key: str | None = None
    reset_email_from: str = "Sanchay <onboarding@resend.dev>"  # resend.dev works without a verified domain
    frontend_url: str = "https://chandramcsr.github.io/ledger-app/"
    password_reset_token_expire_minutes: int = 30

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
