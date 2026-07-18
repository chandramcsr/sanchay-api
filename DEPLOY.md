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
   | `RESEND_API_KEY` | from resend.com dashboard — enables real password-reset emails; omit to keep the dev fallback (logs the link instead) |
   | `FRONTEND_URL` | `https://chandramcsr.github.io/ledger-app/` — where reset links point |

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

## Option C: Oracle Cloud (Always Free tier)

More setup than Render, but genuinely free indefinitely (not a
trial) — OCI's Always Free tier includes an ARM compute instance
(4 OCPUs, 24GB RAM) that comfortably runs this. Database stays on
Neon; only the API itself moves.

### 1. Create the compute instance

In the OCI Console: **Compute → Instances → Create Instance**.
- **Image**: Canonical Ubuntu (22.04 or newer)
- **Shape**: `VM.Standard.A1.Flex` — this is the Always Free ARM
  shape. Set it to 2 OCPUs / 12GB RAM (leaves room to run a second
  free instance later if you ever want one; the free tier's total
  budget is 4 OCPUs / 24GB across all A1 instances combined).
- **Networking**: use an existing VCN or let it create one. Make sure
  **"Assign a public IPv4 address"** is checked.
- **SSH keys**: add your own public key (or generate a new pair and
  download the private key — you'll need it to connect).

Once it's running, note the **public IP** shown on the instance
details page.

### 2. Open the right ports

By default OCI only allows inbound SSH (22). You need 80 and 443 open
too, for Caddy's HTTP→HTTPS redirect and the actual HTTPS traffic.

Instance details page → the VCN's **Security List** (under
"Primary VNIC") → **Add Ingress Rules**, twice:
- Source CIDR `0.0.0.0/0`, IP Protocol TCP, Destination Port 80
- Source CIDR `0.0.0.0/0`, IP Protocol TCP, Destination Port 443

Ubuntu's own firewall (`ufw`) is inactive by default on the stock OCI
image, so nothing else to open there — but if you've enabled it
yourself, `sudo ufw allow 80,443/tcp` too.

### 3. Get a hostname without owning a domain

Let's Encrypt (what Caddy uses for automatic HTTPS) can't issue a
certificate for a bare IP address — it needs a real, resolvable
hostname. [sslip.io](https://sslip.io) solves this with zero setup:
`<your-ip-with-dashes>.sslip.io` automatically resolves to that IP,
no registration needed. If your instance's public IP is
`123.45.67.89`, your hostname is `123-45-67-89.sslip.io`.

(This works fine indefinitely, but if you buy a real domain later,
switching is a one-line change — see step 6.)

### 4. SSH in and install Docker

```bash
ssh -i /path/to/your/private-key ubuntu@<your-public-ip>

# Docker's official convenience script — installs Docker Engine +
# the Compose plugin together
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# log out and back in for the group change to take effect
exit
ssh -i /path/to/your/private-key ubuntu@<your-public-ip>
docker --version && docker compose version   # confirm both installed
```

### 5. Clone the repo and configure

```bash
git clone https://github.com/chandramcsr/sanchay-api.git
cd sanchay-api
cp .env.prod.example .env.prod
nano .env.prod   # fill in DATABASE_URL (from Neon), JWT_SECRET_KEY, CORS_ORIGINS, etc.
nano Caddyfile    # replace the placeholder with your real sslip.io hostname from step 3
```

`CORS_ORIGINS` in `.env.prod` should list the frontend's real URL
(`https://chandramcsr.github.io`) — requests from anywhere else are
rejected.

### 6. Bring it up

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f   # watch it start; Ctrl-C to stop watching (containers keep running)
```

First boot takes a little longer than usual — Caddy is requesting a
real certificate from Let's Encrypt for your hostname. Once that
settles:

```bash
curl https://123-45-67-89.sslip.io/health
# {"status":"ok"}
```

That URL — with `https://`, not `http://` — is your new
`VITE_API_URL` for the frontend (see "Wiring the frontend" below).

`restart: unless-stopped` in `docker-compose.prod.yml` means both
containers restart automatically if they crash, or after the VM
itself reboots (as long as Docker's own systemd service is enabled
on boot, which the install script above does automatically).

### Updating after a code change

```bash
cd sanchay-api
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

Migrations run automatically on every container start (same as
Render), so a `git pull` + rebuild is the entire deploy step — no
separate migration command.

### Switching to a real domain later

Point the domain's DNS A record at your instance's public IP, then
just change the hostname at the top of `Caddyfile` to the real domain
and re-run step 6's `up -d --build`. Caddy handles getting a new
certificate for it automatically.

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
