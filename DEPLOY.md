# Deploying Sanchay API

Two things to provision separately: a **PostgreSQL database** and
somewhere to **run the container**. Some hosts bundle both; some don't.

## Option A: Render (recommended — bundles both, simplest free tier)

1. **New → PostgreSQL** in the Render dashboard. Note the **Internal
   Database URL** it gives you (`postgresql://user:pass@host/db`).
2. **New → Web Service** → connect the `sanchay-api` GitHub repo.
   Render detects the `Dockerfile` automatically — no build command
   needed.
3. Set these environment variables on the web service:

   | Key | Value |
   |---|---|
   | `DATABASE_URL` | the Internal Database URL from step 1 |
   | `JWT_SECRET_KEY` | generate: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
   | `CORS_ORIGINS` | `https://chandramcsr.github.io,http://localhost:5173` |

4. Deploy. The Dockerfile's `CMD` runs `alembic upgrade head` before
   starting `uvicorn` — migrations apply automatically on every
   deploy, no manual step, no drift between what's in `alembic/versions/`
   and what's actually in the database.
5. Render gives you a public URL (`https://sanchay-api-xxxx.onrender.com`).
   That's your `VITE_API_URL` for the frontend.

**Free tier caveat**: Render's free web services spin down after 15
minutes of inactivity and take ~30-60s to wake on the next request —
fine for development and early testing, worth upgrading before this
is the only path into a live app for real users.

## Option B: Database and compute on separate hosts

More portable long-term (swap either piece independently), more setup:

- **Database**: [Neon](https://neon.tech) — serverless Postgres,
  generous free tier, gives you a `DATABASE_URL` directly in its
  dashboard. (Supabase is a similar option if you'd rather have a
  built-in admin UI over the data too.)
- **Compute**: [Railway](https://railway.app) or [Fly.io](https://fly.io)
  — both deploy a `Dockerfile` from a GitHub repo with a few clicks;
  set the same three env vars as above.

## Verifying a deployment

```bash
curl https://<your-deployed-url>/health
# {"status":"ok"}

curl -X POST https://<your-deployed-url>/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"testpass1","display_name":"Test"}'
# 201, returns a token
```

## Wiring the frontend to a live deployment

In the `ledger-app` repo, create `.env.local` (gitignored, never
committed):

```
VITE_API_URL=https://<your-deployed-url>
```

Rebuild (`npm run build`) and redeploy the PWA. The Auth Gate will now
talk to the real server instead of `localhost:8000`.

**Remember to also update `CORS_ORIGINS`** on the backend if the
frontend's origin changes (e.g. adding a custom domain later, or the
Capacitor native app's origin once that's wired) — a request from an
origin not in that list will be silently blocked by the browser, and
it can look like "the API is broken" when it's actually a CORS
mismatch. Check the browser console network tab for a CORS error
specifically before assuming anything else is wrong.

## Secrets hygiene

- Generate a fresh `JWT_SECRET_KEY` per environment (dev, staging,
  prod) — never reuse the value from `.env.example` or from local
  development.
- Rotating `JWT_SECRET_KEY` invalidates every existing session
  (all users get logged out) — expected and fine, but good to know
  before rotating it on a whim in production.
