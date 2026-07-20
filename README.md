# Sanchay API

Identity, encrypted backup, and shared-expense tracking for [Sanchay](https://chandramcsr.github.io/ledger-app/).
Built with FastAPI, SQLAlchemy, Alembic, and PostgreSQL.

**Scope, deliberately narrow:** your *personal* ledger — transactions,
accounts, budgets, recurring rules — never touches this server unless
you turn Sync on, and even then it arrives encrypted, unreadable
without a passphrase this server never sees. This service does store
two other things, on purpose: your identity (email, hashed password,
display name), and shared-expense data for groups you're actually
in — expense descriptions, amounts, and who owes whom, since that
information has to be shared with the other people in the group to
be useful at all. Nothing here is sold, shared with advertisers, or
used for anything beyond making the app work.

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

321 tests across auth, sync, and the shared-expenses subsystem. A few
worth knowing about specifically:

- **Account enumeration**: a wrong password and a nonexistent email
  return the identical error message — telling those apart is itself
  a data leak. Every group/expense-touching endpoint applies the same
  principle: not a member → 404, never 403.
- **No password leakage**: every response is scanned to confirm
  `password` / `hashed_password` never appear, in any endpoint.
- **Rate limiting**: dedicated tests confirm a 429 actually triggers
  on the endpoints that declare a limit, not just that the decorator
  is present.
- **Pending-vs-frozen identity**: a real bug once shipped where a
  never-signed-up participant (added by email, hasn't joined yet) was
  indistinguishable from a genuinely deleted account — both showed
  the same "(account deleted)" label. Regression tests confirm the
  two cases render differently and don't drift back together.

## API

Business endpoints are versioned under `/api/v1`; ops endpoints (`/`, `/health`) deliberately aren't — they're not part of the API surface a client integrates against, and versioning an uptime ping buys nothing.

### Ops

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | — | Liveness check (real DB connectivity check, returns 503 on failure) |
| GET | `/` | — | Service info, mainly for Render's uptime ping |

### Auth & identity

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/v1/auth/signup` | — | Create account, returns a 12-hour access token + a 30-day refresh token |
| POST | `/api/v1/auth/login` | — | Returns a 12-hour access token + a 30-day refresh token |
| POST | `/api/v1/auth/refresh` | Refresh token in body | Trades a valid refresh token for a fresh access+refresh pair. Rotating: the presented token is revoked on use |
| GET | `/api/v1/auth/me` | Bearer token | Current user's profile |
| DELETE | `/api/v1/auth/me` | Bearer token + password | Permanently deletes the account and every table tied to it — immediate, no recovery |
| PUT | `/api/v1/auth/me/avatar` | Bearer token | Upload/replace profile photo |
| DELETE | `/api/v1/auth/me/avatar` | Bearer token | Remove profile photo |
| GET | `/api/v1/auth/login-history` | Bearer token | Recent sign-in attempts (success and failure) against your own account |
| POST | `/api/v1/auth/verify-email` | Token-based | Marks the account verified — soft verification, not required to use the app |
| POST | `/api/v1/auth/resend-verification` | Bearer token | Sends a fresh verification email (no-op if already verified) |
| POST | `/api/v1/auth/resend-verification-by-email` | — | Same, for someone not currently signed in |
| POST | `/api/v1/auth/forgot-password` | — | Request a reset link (always 200, enumeration-safe) |
| POST | `/api/v1/auth/reset-password` | — | Exchange a valid reset token for a new password + JWT |

### Sync (encrypted personal ledger backup)

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/sync/status` | Bearer token | Whether a cloud backup exists, and its version |
| GET | `/api/v1/sync/pull` | Bearer token | The encrypted ledger blob (opaque to this server) |
| PUT | `/api/v1/sync/push` | Bearer token | Replace the encrypted ledger blob — requires `based_on_version` to match current, or returns 409 |

### Shared expenses (groups, splits, settlements)

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/v1/shared-expenses/groups` | Bearer token | Create a group, optionally inviting members by email |
| GET | `/api/v1/shared-expenses/groups` | Bearer token | Groups you're a member of |
| GET / PATCH / DELETE | `/api/v1/shared-expenses/groups/{id}` | Bearer token | Group detail, rename, delete |
| POST / DELETE | `/api/v1/shared-expenses/groups/{id}/members` | Bearer token | Add or remove a member |
| DELETE | `/api/v1/shared-expenses/groups/{id}/pending-invites` | Bearer token | Cancel an invite that hasn't been accepted yet |
| GET | `/api/v1/shared-expenses/invites/{id}` | — | Preview an invite before signing up |
| POST | `/api/v1/shared-expenses/invites/{id}/accept` | Bearer token | Join a group via invite |
| POST / GET | `/api/v1/shared-expenses/groups/{id}/expenses` | Bearer token | Create or list a group's expenses |
| GET / PATCH / DELETE | `/api/v1/shared-expenses/expenses/{id}` | Bearer token | Expense detail, edit, delete |
| GET / POST | `/api/v1/shared-expenses/expenses/{id}/comments` | Bearer token | Comment thread on an expense |
| POST / GET / PATCH / DELETE | `/api/v1/shared-expenses/groups/{id}/recurring`, `/recurring/{id}` | Bearer token | Recurring shared-expense rules |
| GET | `/api/v1/shared-expenses/balances` | Bearer token | Net balance with every person you share a group with, live or frozen (deleted-account) |
| GET | `/api/v1/shared-expenses/balances/{other_user_id}/breakdown` | Bearer token | Itemized history behind one balance |
| GET | `/api/v1/shared-expenses/groups/{id}/simplified-debts` | Bearer token | Greedy debt-simplification — the minimum set of payments that settles a group |
| POST | `/api/v1/shared-expenses/settlements` | Bearer token | Record yourself paying someone back |
| GET | `/api/v1/shared-expenses/settlements/received` | Bearer token | Settlements paid *to* you — lets your own device notice and reconcile money it doesn't know landed yet |

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

Any host that runs a Docker container works, whether it also manages
Postgres for you (Render, Railway, Fly.io) or not (a bare VM — Oracle
Cloud's Always Free ARM tier is genuinely free indefinitely, paired
with Neon or Supabase for the database; see `DEPLOY.md` for the full
walkthrough of that path, including automatic HTTPS via Caddy with no
domain required). None of the code changes either way — set
`DATABASE_URL`, `JWT_SECRET_KEY`, and `CORS_ORIGINS` (to your real
frontend origin, not `*`) as environment variables on the host, and
`alembic upgrade head` runs automatically on container start via
`CMD` in the Dockerfile.

## Error tracking

Optional. Set `SENTRY_DSN` (and `SENTRY_ENVIRONMENT`, e.g.
`production`) to send unhandled exceptions to a Sentry project —
unset by default, and genuinely inert with no DSN configured (no
transport gets created, so `capture_exception` calls have nowhere to
send anything; this is verified directly in
`tests/test_sentry_config.py`, not just assumed from the SDK's docs).
Works correctly alongside this app's own custom `Exception` handler
in `app/core/error_handlers.py` — Sentry still captures the exception
even though that handler returns a normal JSON response instead of
letting it propagate further, confirmed empirically before shipping
this, not assumed from how the integration is documented to behave in
a default FastAPI app.

## What's deliberately not here yet

Everything below was accurate as "not built" at some earlier point and
has since been built — email verification, password reset with real
delivery, rate limiting, refresh tokens, and sync are all real and
covered above. What's actually still out of scope, on purpose:

- **Live multi-device merge sync** — Sync today is whole-blob replace
  with version-conflict detection (see `EncryptedLedger`'s docstring
  in `app/models/encrypted_ledger.py`): a stale push is rejected, not
  silently overwritten, but two devices editing at the same time still
  requires a manual pull-and-retry, not an automatic merge. A real
  merge needs an operation log or CRDT-style structure — meaningfully
  larger scope, not attempted here.
- **Infrastructure scaling** (load balancer, CDN, read replica, object
  storage, connection pooling) — deliberately not built ahead of real
  load. See `architecture/Infrastructure_Architecture.png` for the
  documented target state and the specific traffic threshold that
  would trigger building each piece.
