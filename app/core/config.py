from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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

    # Second, entirely separate database (Sanchaydb on Neon) for the
    # server-authoritative accounts/transactions/budgets work -- NOT the
    # same database as `database_url` (neondb, which holds every
    # existing table: users, sync blobs, shared expenses, health,
    # legal). Keeping them physically separate was a deliberate call:
    # ledger-app's existing routes must keep working against the exact
    # data they work against today, completely unaffected by this new
    # work. SQLite default matches the existing `database_url` pattern
    # -- local dev/tests get a working default without needing a real
    # Sanchaydb provisioned.
    sanchay_app_database_url: str = "sqlite:///./dev_sanchay_app.db"

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

    # Feature switch for the shareable group-invite-link feature
    # (GET /invites/{id}, POST /invites/{id}/accept). Default False —
    # built and fully tested, but deliberately held back until the
    # frontend UI around it (copy-link button, /join/{id} landing
    # flow) is ready to ship alongside it. Both endpoints 404 when
    # this is off, indistinguishable from a genuinely nonexistent
    # route — not a 403 or a "coming soon" message, so a disabled
    # feature reveals nothing about its own existence.
    invite_links_enabled: bool = False

    # Email (password reset). Optional on purpose: if unset, the reset
    # link is logged instead of emailed — keeps local dev/tests working
    # with zero config, same pattern as everything else in this app
    # that degrades gracefully rather than requiring every env var.
    resend_api_key: str | None = None
    reset_email_from: str = "Sanchay <onboarding@resend.dev>"  # resend.dev works without a verified domain
    frontend_url: str = "https://chandramcsr.github.io/ledger-app/"
    password_reset_token_expire_minutes: int = 30

    # Error tracking (Sentry). Optional on purpose, same pattern as
    # resend_api_key above: sentry_sdk.init() with dsn=None is a
    # documented no-op, not a crash, so local dev and tests work with
    # zero config. environment defaults to "development" rather than
    # something that could be mistaken for a real deployment if this
    # is ever left unset somewhere it shouldn't be.
    sentry_dsn: str | None = None
    sentry_environment: str = "development"

    # Clerk verification for the NEW (accounts/transactions/budgets)
    # routes only -- existing routes keep using jwt_secret_key above,
    # completely unrelated to this. clerk_jwt_key is the "JWT
    # Verification Key" from Clerk's dashboard (API Keys page -- it's
    # not one of the two keys shown by default, look for an "Advanced"
    # / "Show JWT public key" expander further down the same page).
    # "Networkless" verification (Clerk's own recommended term): no
    # network call per request to check a token, unlike calling
    # Clerk's API to verify. clerk_authorized_parties guards against
    # token replay from an unauthorized origin (the `azp` claim) --
    # Clerk's own docs are explicit that skipping this check is a real
    # CSRF exposure, not just defense-in-depth.
    #
    # Clerk's dashboard deliberately gives this key WITHOUT PEM
    # header/footer/line-wrapping -- a single base64 line, "for easier
    # setup" per Clerk's own docs (i.e. it pastes into one env var line
    # cleanly). clerk_jwt_key_pem below wraps it into the full PEM form
    # cryptography/jose actually need to parse it -- store whatever
    # Clerk's dashboard actually gives you verbatim in the env var,
    # this handles either form (already-PEM or headerless) so it isn't
    # sensitive to which one gets pasted in.
    clerk_jwt_key: str | None = None
    clerk_authorized_parties: str = ""

    # Ask Sanchay (RAG assistant) -- Claude for generation only, not
    # embeddings (Anthropic doesn't offer an embeddings endpoint).
    # None/empty means the feature is disabled: the router checks this
    # and returns a clear 503 rather than a confusing downstream error
    # when the key isn't configured.
    anthropic_api_key: str | None = None

    @property
    def clerk_authorized_parties_list(self) -> list[str]:
        return [o.strip() for o in self.clerk_authorized_parties.split(",") if o.strip()]

    @property
    def clerk_jwt_key_pem(self) -> str | None:
        if not self.clerk_jwt_key:
            return None
        key = self.clerk_jwt_key.strip()
        if key.startswith("-----BEGIN"):
            return key
        # Headerless single-line form -- wrap into standard PEM: 64
        # chars per line between BEGIN/END markers. Some parsers tolerate
        # unwrapped single-line base64 between the markers, but not all
        # do, and this costs nothing to get exactly right rather than
        # relying on a parser being lenient.
        lines = [key[i : i + 64] for i in range(0, len(key), 64)]
        return "-----BEGIN PUBLIC KEY-----\n" + "\n".join(lines) + "\n-----END PUBLIC KEY-----\n"

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

        Also translates libpq-style query params (which Neon, Supabase,
        and most hosted-Postgres connection strings include) into forms
        asyncpg actually accepts, or drops them if there's no asyncpg
        equivalent at all:
          - ?sslmode=... -> ?ssl=... (asyncpg crashes outright with
            "connect() got an unexpected keyword argument 'sslmode'"
            if left in)
          - ?channel_binding=... -> dropped entirely. SCRAM channel
            binding is a libpq concept with no asyncpg keyword at all —
            not "unsupported value", asyncpg's connect() doesn't
            recognize the parameter name in any form. This one was
            caught by an actual production boot failure against a real
            Neon connection string (Neon includes channel_binding=
            require by default), not found in review — worth being
            deliberately thorough about this translation rather than
            handling only the params in whatever test connection
            string happened to get used during development.

        psycopg2 (the sync engine Alembic migrations run on)
        understands both params natively, which is why migrations
        succeed and then the app crashes on the very same URL — the
        two drivers genuinely speak different query parameters. Only
        the async URL is rewritten; the sync engine keeps the original
        untouched.
        """
        return _to_async_url(self.database_url)

    @property
    def sanchay_app_async_database_url(self) -> str:
        """Same translation as async_database_url, applied to the second
        (Sanchaydb) connection string — see sanchay_app_database_url."""
        return _to_async_url(self.sanchay_app_database_url)


def _to_async_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://") and "+asyncpg" not in url:
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]

        # Proper query-string parsing, not substring replacement — a
        # real Neon connection string surfaced exactly why this
        # matters: it includes channel_binding=require (a libpq/SCRAM
        # parameter with no asyncpg equivalent at all) alongside
        # sslmode=require, and naive string replacement on sslmode
        # alone leaves channel_binding sitting in the URL. SQLAlchemy's
        # asyncpg dialect passes every query param straight through as
        # a keyword arg to asyncpg.connect(), which doesn't recognize
        # channel_binding as a parameter name in any form — not
        # "unsupported value", a hard TypeError, connection refused
        # before the app can even start.
        parsed = urlsplit(url)
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))

        # libpq -> asyncpg SSL param translation. libpq's modes
        # (disable/allow/prefer/require/verify-ca/verify-full) map onto
        # asyncpg's ssl= values; require/verify-* all become
        # ssl=require here — asyncpg's "require" performs certificate
        # verification against the system CA bundle by default when
        # given a hostname, so this doesn't silently weaken
        # verify-full into an unverified connection.
        sslmode = params.pop("sslmode", None)
        if sslmode in ("require", "verify-ca", "verify-full"):
            params["ssl"] = "require"
        elif sslmode in ("prefer", "allow"):
            params["ssl"] = "prefer"
        elif sslmode == "disable":
            params["ssl"] = "disable"

        # channel_binding (SCRAM channel binding, a libpq-only concept)
        # has no asyncpg keyword equivalent — SCRAM channel binding
        # happens transparently as part of asyncpg's own TLS/auth
        # negotiation when ssl=require, nothing to pass explicitly.
        # Dropped, not translated, because there's nothing to translate
        # it to.
        params.pop("channel_binding", None)

        return urlunsplit(parsed._replace(query=urlencode(params)))
    if url.startswith("sqlite://") and "+aiosqlite" not in url:
        return "sqlite+aiosqlite://" + url[len("sqlite://") :]
    return url


settings = Settings()
