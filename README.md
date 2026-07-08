# Sanchay API

Identity and authentication service for [Sanchay](https://chandramcsr.github.io/ledger-app/)
— signup, login, JWT-based auth. Built with FastAPI, SQLAlchemy, Alembic,
and PostgreSQL.

**Scope, deliberately narrow:** this service stores user identity
(email, hashed password, display name) — nothing else. It does **not**
store transactions, accounts, budgets, or any financial data; that
still lives entirely on-device in the Sanchay app. This is the first
building block toward multi-device sync (Phase 1d in the architecture
doc), not the sync itself. Keeping that boundary explicit is what keeps
Sanchay's "no data collected" privacy story true as this grows.

## Stack

- **FastAPI** — async Python web framework
- **PostgreSQL** — via SQLAlchemy 2.0 (SQLite is used only for local
  tests, so the test suite runs without a database server)
- **Alembic** — schema migrations, versioned like the frontend's own
  IndexedDB schema
- **bcrypt** — password hashing (used directly, not via passlib —
  see the comment in `app/core/security.py` for why)
- **python-jose** — JWT signing/verification

## Local development

### Option A: Docker Compose (recommended — Postgres + API together)

```bash
cp .env.example .env
# Edit .env: generate a real JWT_SECRET_KEY with:
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

docker compose up
```

API is live at `http://localhost:8000`. Interactive docs at
`http://localhost:8000/docs`.

### Option B: Local Python, SQLite (no Docker, quick iteration)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

export DATABASE_URL="sqlite:///./dev.db"
export JWT_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"

alembic upgrade head
uvicorn app.main:app --reload
```

## Running tests

```bash
pip install -r requirements-dev.txt
export JWT_SECRET_KEY=test-secret-key-not-for-production-use
export DATABASE_URL=sqlite:///:memory:
pytest tests/ -v
```

22 tests: signup/login flows, JWT round-trip and tamper/expiry
rejection, password hashing correctness, and two security-specific
checks worth knowing about:

- **Account enumeration**: a wrong password and a nonexistent email
  return the identical error message — telling those apart is itself
  a data leak.
- **No password leakage**: every response is scanned to confirm
  `password` / `hashed_password` never appear, in any endpoint.

## API

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | — | Liveness check |
| GET | `/` | — | Service info, mainly for Render's uptime ping |
| POST | `/auth/signup` | — | Create account, returns a JWT |
| POST | `/auth/login` | — | Returns a JWT |
| GET | `/auth/me` | Bearer token | Current user's profile (includes `last_login_at`) |
| GET | `/auth/login-history` | Bearer token | Recent login attempts (success and failure) against your own account |
| GET | `/sync/status` | Bearer token | Whether a cloud backup exists, and its version |
| GET | `/sync/pull` | Bearer token | The encrypted ledger blob (opaque to this server) |
| PUT | `/sync/push` | Bearer token | Replace the encrypted ledger blob — requires `based_on_version` to match current, or returns 409 |
| DELETE | `/auth/me` | Bearer token + password | Permanently deletes the account and all associated data (sync backup, login history, reset tokens) — immediate, no recovery |
| POST | `/auth/verify-email` | None (token-based) | Marks the account verified. Soft verification — not required to sign in or use the app |
| POST | `/auth/resend-verification` | Bearer token | Sends a fresh verification email (no-op if already verified) |
| POST | `/auth/forgot-password` | — | Request a reset link (always 200, enumeration-safe) |
| POST | `/auth/reset-password` | — | Exchange a valid reset token for a new password + JWT |

Full interactive docs (Swagger UI) at `/docs` once running.

## Database migrations

Never use `Base.metadata.create_all()` against Postgres in production —
that's a SQLite-only dev convenience with no version history. Always:

```bash
# After changing a model in app/models/:
alembic revision --autogenerate -m "describe the change"
# Review the generated file in alembic/versions/ before committing —
# autogenerate is a good first draft, not a guarantee.
alembic upgrade head
```

## Deploying

Any host that runs a Docker container and gives you a managed Postgres
works: Render, Railway, Fly.io, or a Postgres add-on wherever you land.
None of the code changes — set `DATABASE_URL`, `JWT_SECRET_KEY`, and
`CORS_ORIGINS` (to your real frontend origin, not `*`) as environment
variables on the host, and `alembic upgrade head` runs automatically on
container start via `CMD` in the Dockerfile.

## What's deliberately not here yet

- **Email verification** — signup is currently email+password only,
  no confirmation email. Fine for early testing; add before any real
  public signup.
- ~~No password reset~~ — **fixed**: full forgot-password/reset-password flow (`app/routers/auth.py`), single-use SHA-256-hashed tokens with 30-minute expiry (`app/models/password_reset_token.py`), rate limited (3 requests/hour to request, 5/hour to reset), and real email delivery via Resend (`app/core/email.py`) once `RESEND_API_KEY` is set — falls back to logging the link when it isn't, so local dev/tests need zero config.
- **Rate limiting** — login/signup endpoints have no throttling yet;
  add before this is internet-facing (e.g. slowapi or a reverse-proxy
  rule).
- **Refresh tokens** — access tokens are long-lived (7 days) with no
  refresh/revoke mechanism. Fine for v1; a refresh-token flow is the
  natural next step if that expiry feels wrong in practice.
- ~~No password reset~~ — **built, but not fully wired**: the
  request/confirm flow, token generation (hashed, single-use,
  30-minute expiry), and rate limiting are all real and tested. What's
  missing is an actual email provider — `app/core/email.py` currently
  logs the reset link to stdout instead of sending it (clearly marked
  `LoggingEmailSender`, DEV-ONLY in the code). Swapping in a real
  provider (Resend, SendGrid, Postmark) is a one-file change behind
  the `EmailSender` interface; see the extension-point example in that
  file. **Do not consider this feature complete for real users until
  a real sender is wired in** — right now, anyone who forgets their
  password has no way to actually receive the link.
- **No email verification** at signup — someone could sign up with an
  email they don't own. Combined with the point above, wiring a real
  email provider would let both gaps be closed together (verification
  link + reset link use the same underlying "send an email" plumbing).
- ~~422 validation errors echo the submitted password~~ — **fixed**:
  a custom exception handler (`app/core/error_handlers.py`) redacts
  any `password` field from validation error responses before they
  leave the server.
- ~~No rate limiting~~ — **fixed**: login is limited to 5 attempts/min
  and signup to 10/min per IP, via `slowapi` (`app/core/limiter.py`).
  Worth revisiting the exact numbers once there's real traffic to
  learn from; these are reasonable starting guesses, not tuned values.
- **Syncing actual ledger data** — this is Phase 1d, much larger
  (end-to-end encryption, conflict resolution, an op-log), and comes
  after this identity layer proves out.
