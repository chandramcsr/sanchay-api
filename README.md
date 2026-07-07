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
| POST | `/auth/signup` | — | Create account, returns a JWT |
| POST | `/auth/login` | — | Returns a JWT |
| GET | `/auth/me` | Bearer token | Current user's profile |

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
- **Password reset** — no "forgot password" flow yet.
- **Rate limiting** — login/signup endpoints have no throttling yet;
  add before this is internet-facing (e.g. slowapi or a reverse-proxy
  rule).
- **Refresh tokens** — access tokens are long-lived (7 days) with no
  refresh/revoke mechanism. Fine for v1; a refresh-token flow is the
  natural next step if that expiry feels wrong in practice.
- **422 validation errors echo the submitted request body**, including
  the plaintext password field — this is default FastAPI/Pydantic
  behavior (the error detail includes `input` for debugging), and it
  means a malformed signup/login request can put a plaintext password
  into a 422 response body, and from there into logs, error trackers,
  or browser devtools network tabs. Not exploitable by an attacker
  (they'd need the password already to trigger it usefully), but worth
  closing before this handles real traffic — either a custom exception
  handler that strips `password` from validation error bodies, or
  switching `password` fields to Pydantic's `SecretStr`.
- **Syncing actual ledger data** — this is Phase 1d, much larger
  (end-to-end encryption, conflict resolution, an op-log), and comes
  after this identity layer proves out.
