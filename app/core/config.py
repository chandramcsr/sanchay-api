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
    access_token_expire_minutes: int = 60 * 12  # 12 hours — short-lived, renewed via refresh token
    refresh_token_expire_days: int = 30
    cors_origins: str = "http://localhost:5173,https://chandramcsr.github.io"

    # Feature switch for enforcing email verification at login. Default
    # False (today's behavior: unverified accounts can log in freely) —
    # the email service (Resend, sandbox mode) currently can't deliver
    # to arbitrary recipient addresses, only to the account's own
    # verified sender, so flipping this on before that's resolved would
    # lock out real signups. Flip to True once outbound delivery to
    # arbitrary addresses is confirmed working end-to-end.
    require_email_verification: bool = False

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

    @property
    def async_database_url(self) -> str:
        """
        The async driver needs an explicit scheme (postgresql+asyncpg://,
        sqlite+aiosqlite://) — Render's DATABASE_URL doesn't provide one
        (and older Render/Heroku-style URLs use the legacy postgres://
        scheme entirely). Translated here so the raw env var never needs
        to change on the hosting side.

        Also translates libpq-style ?sslmode=... (which Neon, Supabase,
        and most hosted-Postgres connection strings include) into
        asyncpg's ?ssl=... — asyncpg does not accept sslmode as a
        keyword AT ALL and crashes with "connect() got an unexpected
        keyword argument 'sslmode'" if it's left in. psycopg2 (the sync
        engine Alembic migrations run on) understands sslmode natively,
        which is why migrations succeed and then the app crashes on the
        very same URL — the two drivers genuinely speak different query
        parameters. Only the async URL is rewritten; the sync engine
        keeps the original untouched.
        """
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        if url.startswith("postgresql://") and "+asyncpg" not in url:
            url = "postgresql+asyncpg://" + url[len("postgresql://") :]
            # libpq -> asyncpg SSL param translation. libpq's modes
            # (disable/allow/prefer/require/verify-ca/verify-full) map
            # onto asyncpg's ssl= values; require/verify-* all become
            # ssl=require here — asyncpg's "require" performs
            # certificate verification against the system CA bundle by
            # default when given a hostname, so this doesn't silently
            # weaken verify-full into an unverified connection.
            for mode in ("verify-full", "verify-ca", "require", "prefer", "allow"):
                if f"sslmode={mode}" in url:
                    replacement = "ssl=require" if mode in ("require", "verify-ca", "verify-full") else "ssl=prefer"
                    url = url.replace(f"sslmode={mode}", replacement)
                    break
            if "sslmode=disable" in url:
                url = url.replace("sslmode=disable", "ssl=disable")
            return url
        if url.startswith("sqlite://") and "+aiosqlite" not in url:
            return "sqlite+aiosqlite://" + url[len("sqlite://") :]
        return url


settings = Settings()
