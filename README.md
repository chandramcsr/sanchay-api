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

## Architecture

Three layers, each with one job:

- **`routers/`** — HTTP only. Parse the request, call one service
  function, return the result. Rate-limit decorators live here (they
  need `request: Request` and hook in at the ASGI level, so they
  can't move to the service layer even though everything else does).
- **`services/`** — business logic. Enumeration protection, token
  lifecycle, cascade-delete ordering, transaction boundaries
  (`db.commit()` is called here, never in a repository — a service
  often needs several repository calls to succeed together as one
  unit of work).
- **`repositories/`** — data access only. One file per model, no
  business logic, no commits.

Why: business logic that lives directly in route handlers can only be
tested by spinning up the full HTTP stack (TestClient, a request, a
response to parse). `tests/test_auth_service.py` calls
`auth_service.signup()` etc. directly — plain Python function calls,
no HTTP involved, meaningfully faster and a more precise way to test
a business rule than asserting on a JSON response body for everything.
The existing HTTP-level tests didn't go away — they prove the routes
are *wired* correctly, which the service-layer tests alone can't; the
two are complementary, not a replacement for each other.

### The shared-expenses module is deliberately isolated

`Group`, `GroupMember`, `SharedExpense`, `SharedExpenseSplit`,
`SharedExpenseComment`, `Settlement` (in `app/models/`) and
`app/services/shared_expense_service.py` form a genuinely separate
domain, built to be extractable into its own service later without a
rewrite. The only thing this module depends on elsewhere in the
codebase is `users.id` — no foreign keys or joins into
`encrypted_ledgers`, `login_events`, or anything ledger-specific. The
only thing elsewhere in the codebase depends on it back is one
function call: `auth_service.delete_account()` calls
`freeze_user_references()` before deleting a user row.

**Why nullable `user_id`, no enforced foreign key, everywhere in this
module**: account deletion in this app is permanent and cascades
everything — except a real, bilateral debt between two people doesn't
stop existing just because one side deleted their account. Every
group/expense/split/comment/settlement row stores a `name_snapshot`
alongside a *nullable* `user_id`. `freeze_user_references()` copies
the current display name into every row referencing that user, then
sets `user_id` to `NULL` — the historical record survives as "Name
(account deleted)"; the account and everything else about that person
does not. A real foreign-key constraint here would have either
blocked deletion outright or cascade-deleted the history, matching
neither the intended policy.

**Splitting math** (`split_evenly()`) uses `Decimal` throughout, never
float, and the largest-remainder method — $100 split 3 ways is
$33.33/$33.33/$33.34, deterministic, always summing exactly to the
total.

**Reconnection**: every user-referencing row stores both a nullable
`user_id` (the live account link) and an `email_ref` — a SHA-256 hash
of the person's normalized email, reusing `jwt-library`'s existing
token-hashing primitive. `user_id` is "who's currently active,"
nulled on deletion; `email_ref` is the durable identity anchor that
never changes. If someone signs up again with the same email,
`reconnect_by_email()` (called from `auth_service.signup()`) finds
every frozen row matching that hash and re-populates `user_id` —
their old shared history becomes live again automatically, without
this module ever storing or exposing anyone's actual email address.

**The API** (`/api/v1/shared-expenses/...`) is deliberately narrow:
groups, expenses, comments, settlements, and a balance summary. Two
design choices worth knowing:
- Every group/expense-touching endpoint checks membership FIRST and
  returns 404 (not 403) for a group/expense you're not in —
  enumeration-safe, same principle already used for login/signup.
- `BalanceOut` is two separate non-negative fields (`you_owe_them`,
  `they_owe_you`), never one signed number. A real sign-confusion bug
  was found and fixed elsewhere in this app (credit card debt counted
  as an asset instead of a liability) shortly before this API was
  built — this shape makes the equivalent mistake structurally
  impossible for whatever reads the response.

## Stack

- **FastAPI** — genuinely async now, not just async-capable. Every route
  is `async def`, and SQLAlchemy runs on its async engine (`asyncpg` for
  Postgres, `aiosqlite` for tests) — a request waiting on the database
  yields the event loop instead of blocking a worker thread, which is
  the actual performance property FastAPI's "async" claim depends on
- **PostgreSQL** — via SQLAlchemy 2.0's async engine. `DATABASE_URL` is
  provided in the normal `postgresql://...` form (what Render gives
  you); `Settings.async_database_url` translates it to the
  `postgresql+asyncpg://` scheme the app actually connects with.
  Connection pool hardened with `pool_pre_ping` (a connection Render's
  Postgres silently dropped after sitting idle fails the *next*
  request that draws it from the pool without this) and `pool_recycle`
  (proactively discards connections before they go stale)
- **Alembic** — schema migrations, versioned like the frontend's own
  IndexedDB schema. Runs on a separate, plain *sync* engine
  (`psycopg2-binary`) — migrations are one-shot scripts, not part of
  the request-serving hot path where async actually matters, so there's
  no reason to complicate them with the async driver too
- **[jwt-library](https://github.com/chandramcsr/jwt-library)** —
  JWT, password hashing (bcrypt, called directly rather than via
  passlib — see that repo's docs for why), and single-use token
  generation, extracted into a shared package once a second service
  needing the same primitives was actually planned (not built
  speculatively ahead of that need). `app/core/security.py` and
  `app/core/reset_tokens.py` stay as thin, sanchay-api-specific
  wrappers — they build the library's `JWTConfig` from this
  service's own settings and re-export everything under the exact
  names every existing caller already uses, so adopting the shared
  library required zero changes anywhere else in this codebase
- Password-reset and verification emails send via `BackgroundTasks` —
  the HTTP response returns the moment the database write succeeds,
  not after waiting on Resend's API latency

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

Business endpoints are versioned under `/api/v1`; ops endpoints (`/`, `/health`) deliberately aren't — they're not part of the API surface a client integrates against, and versioning an uptime ping buys nothing.

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | — | Liveness check (real DB connectivity check, returns 503 on failure) |
| GET | `/` | — | Service info, mainly for Render's uptime ping |
| POST | `/api/v1/auth/signup` | — | Create account, returns a 12-hour access token + a 30-day refresh token |
| POST | `/api/v1/auth/login` | — | Returns a 12-hour access token + a 30-day refresh token |
| POST | `/api/v1/auth/refresh` | — (refresh token in body) | Trades a valid refresh token for a fresh access+refresh pair. Rotating: the presented token is revoked on use, so it can never be replayed |
| GET | `/api/v1/auth/me` | Bearer token | Current user's profile (includes `last_login_at`) |
| GET | `/api/v1/auth/login-history` | Bearer token | Recent login attempts (success and failure) against your own account |
| GET | `/api/v1/sync/status` | Bearer token | Whether a cloud backup exists, and its version |
| GET | `/api/v1/sync/pull` | Bearer token | The encrypted ledger blob (opaque to this server) |
| PUT | `/api/v1/sync/push` | Bearer token | Replace the encrypted ledger blob — requires `based_on_version` to match current, or returns 409 |
| DELETE | `/api/v1/auth/me` | Bearer token + password | Permanently deletes the account and all associated data (sync backup, login history, reset tokens) — immediate, no recovery |
| POST | `/api/v1/auth/verify-email` | None (token-based) | Marks the account verified. Soft verification — not required to sign in or use the app |
| POST | `/api/v1/auth/resend-verification` | Bearer token | Sends a fresh verification email (no-op if already verified) |
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
