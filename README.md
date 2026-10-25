# Rental Management App

A rental management application for small landlords, with a FastAPI backend, a React
web app, a packaged Capacitor iPhone client, and a read-only MCP server for AI agents. In production it runs as a Docker
Compose stack behind nginx with HTTPS (Let's Encrypt), Postgres, and automatic
database backups.

- [Features](#features)
- [Architecture](#architecture)
- [Local development](#local-development)
- [iPhone client](#iphone-client)
- [Production deployment](#production-deployment)
- [Configuration reference](#configuration-reference)
- [Administration](#administration)
- [Database encryption](#database-encryption)
- [Billing guide](#billing-guide)
- [Google Calendar exports](#google-calendar-exports)
- [MCP server for AI agents](#mcp-server-for-ai-agents)
- [API](#api)
- [Testing](#testing)

## Features

**Landlords** (web app, iPhone client, and API)

- Properties, with any number of units per property
- Managed tenant records, with contact details
- Leases that bind one or more tenants to a property or unit, with each tenant's
  rent share and deposit
- Monthly rent bills: generated automatically on the 1st of each month, prorated for
  mid-month starts and ends
- Property bills that the landlord pays (electricity, water, mortgage, insurance,
  property tax, maintenance, other). They can be one-time, repeat every 1, 2, 3, 6
  or 12 months, or cover a custom period. Repeated series can be extended later.
- Property bill costs split into tenant charges, with a preview before confirming
- Manual bills charged to the tenants of a lease
- Payments recorded against bills; each payment recalculates the bill's balance and
  status (open, partial or paid)
- Property bill filters, lease details and profile and password settings
- Optional Google Calendar export of newly generated rent bills, off by default per landlord

**Super admin**

- Invites landlords with single-use links (sign-up is invitation only)
- Lists landlords with their status, last login, last activity and active sessions
- Deactivates or reactivates landlords and resets their passwords, with an audit log

**AI agents**

- A read-only MCP server that answers questions about one landlord's data, using
  that landlord's own access token

**Tenants**: tenant accounts can only be created when `OPEN_REGISTRATION=true`
(local development). Tenant bills are available from the API at `GET /tenant/bills`;
the web app's tenant portal is only a placeholder.

## Architecture

```text
                   ┌────────────── frontend (nginx) ──────────────┐
 browser ─HTTPS──▶ │ /          React app (static)                │
 MCP client ─────▶ │ /api/...   ──▶ api  (FastAPI, :8000)         │──▶ db (Postgres 16)
                   │ /mcp       ──▶ mcp  (MCP server, :8001)      │        ▲
                   └──────────────────────────────────────────────┘        │
                                                         backup (pg_dump ──▶ ./backups, S3)
```

| Service    | Built from                    | Role                                                              |
| ---------- | ----------------------------- | ----------------------------------------------------------------- |
| `frontend` | `frontend/Dockerfile`         | Serves the built React app; in production also does TLS and proxies `/api` and `/mcp` |
| `api`      | `Dockerfile`                  | FastAPI app: REST API, JWT/OAuth tokens, and the monthly rent bill scheduler |
| `mcp`      | `Dockerfile` (`python -m app.mcp_server`) | Read-only MCP server; validates tokens issued by `api`   |
| `db`       | `postgres:16-alpine`          | Database                                                          |
| `backup`   | `postgres:16-alpine`          | Periodic `pg_dump` (production only)                              |

The API creates new database tables at startup. Existing plaintext databases require
the offline [encryption migration](#database-encryption) before upgrading. `mcp` waits
for the `api` health check so the two never create the schema at the same time.

Repository layout:

```text
app/                     FastAPI backend and MCP server
  main.py                REST endpoints, startup and the monthly bill scheduler
  admin.py, invites.py   super-admin endpoints and landlord invitations
  billing.py, bill_extensions.py, tenant_charges.py   billing logic
  login_throttle.py, account_access.py, security.py   authentication and sessions
  mcp_server.py          MCP server (stdio, streamable HTTP, SSE)
frontend/                Shared React + Vite web/iPhone UI, nginx configs, Dockerfile
  ios/                   Capacitor iOS project with Keychain session storage
  IOS.md                 iPhone build, configuration, signing, and testing guide
scripts/                 init_env.py, db_status.py, backup_db.sh, test_*.py
docker-compose.yml       local stack
docker-compose.prod.yml  production overrides (HTTPS, closed ports, backups)
```

## iPhone client

The iPhone client uses the existing backend and accounts, with mobile record cards,
bottom navigation, touch-sized controls, and Keychain-backed sessions. Desktop
tables and browser behavior remain available. No data migration is required.

See [the iPhone setup guide](frontend/IOS.md) for HTTPS configuration, CORS,
`npm run ios:sync`, and Xcode installation/signing. Full Xcode and Apple signing
are required to run on a device; no signed IPA is included.

## Local development

### Backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/init_env.py
# Optional: needed before landlords can enable encryption or connect Google Calendar.
# python -m scripts.manage_encryption init-key
uvicorn app.main:app --reload
```

`scripts/init_env.py` copies `.env.example` to a private, git-ignored `.env` and
puts a random `JWT_SECRET_KEY` in it. It never overwrites an existing `.env` or
rotates its key. The API and the MCP server both load `.env` from the project root,
even when started from another directory, and environment variables take
precedence over it.

- API docs: [http://localhost:8000/docs](http://localhost:8000/docs)
- Data is stored in SQLite at `data/rental_mgmt.db` by default. Relative SQLite paths
  resolve against the project root, and startup prints the resolved
  `Rental Management database: ...`. `python scripts/db_status.py` shows which
  database file is in use and how many records it holds.
- The first startup creates the super admin and prints its initial password once
  (see [Super admin](#super-admin)).
- Landlords can only sign up with an invitation link. To skip invitations locally,
  set `OPEN_REGISTRATION=true` in `.env`. Never set it in production.
- `.env.example` lists every backend setting with a short comment; see the
  [configuration reference](#configuration-reference) for details.

### Frontend

In a second terminal:

```bash
cd frontend
cp .env.example .env      # VITE_API_BASE_URL=http://127.0.0.1:8000
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). The Vite dev server also proxies
`/api` to `http://127.0.0.1:8000`, so `VITE_API_BASE_URL=/api` works too.

### Docker Compose (local)

```bash
export JWT_SECRET_KEY="$(openssl rand -hex 32)"   # or put it in .env
docker compose up -d --build
```

| URL                                                  | Service                |
| ---------------------------------------------------- | ---------------------- |
| [http://localhost:5173](http://localhost:5173)       | web app                |
| [http://localhost:8000](http://localhost:8000)       | API (`/docs` for Swagger) |
| [http://localhost:8001/mcp](http://localhost:8001/mcp) | MCP (streamable HTTP)  |
| `localhost:5432`                                     | Postgres (`rental` / `rental`) |

Compose reads `JWT_SECRET_KEY` from your shell or from `.env`. Its placeholder
fallback is rejected at startup. `api` and `mcp` share the database URL and JWT
secret through the `x-backend-env` anchor in `docker-compose.yml`, because the MCP
server validates tokens that the API issues.

Every service uses `restart: unless-stopped`; stop the stack with
`docker compose stop` or `docker compose down`.

## Production deployment

Production uses `docker-compose.yml` with `docker-compose.prod.yml` layered on top,
on a single host (an AWS EC2 instance) with a DNS name pointing at it.

What the production override changes:

- nginx (`frontend`) is the only published service, on ports 80 and 443. Port 80
  redirects to HTTPS and serves Let's Encrypt challenges.
- The API is reachable only at `https://<DOMAIN>/api`, MCP only at
  `https://<DOMAIN>/mcp`, and Postgres only from the other containers.
- The frontend is built with `VITE_API_BASE_URL=/api`, so the browser only talks to
  its own HTTPS origin.
- `CORS_ORIGINS=https://<DOMAIN>,capacitor://localhost` and `TRUST_PROXY_HEADERS=true` are set on the API,
  and MCP advertises `https://<DOMAIN>` as its public URL.
- A `backup` service dumps the database on a schedule.

### First deployment

1. **Network.** In the EC2 security group, open only 80 and 443, plus SSH from your
   own IP. Close 8000, 8001 and 5432 if an older deployment opened them.
2. **Configuration.** Clone the repository on the host and create `.env`:

   ```bash
   python3 scripts/init_env.py              # random JWT_SECRET_KEY
   cat >> .env <<EOF
   DOMAIN=<your-domain>
   COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml
   SUPER_ADMIN_INITIAL_PASSWORD=<at least 12 characters>
   BACKUP_S3_URI=s3://<bucket>/rental-mgmt
   EOF
   ```

   With `COMPOSE_FILE` set, plain `docker compose ...` commands on this host use both
   files. Without it, pass `-f docker-compose.yml -f docker-compose.prod.yml` every
   time. `SUPER_ADMIN_INITIAL_PASSWORD` and `BACKUP_S3_URI` are optional.

3. **First certificate.** nginx needs the certificate before it starts, so get the
   first one in standalone mode while port 80 is still free:

   ```bash
   sudo certbot certonly --standalone -d <your-domain>
   ```

4. **Start the stack.**

   ```bash
   docker compose up -d --build
   docker compose ps
   ```

5. **Secure the admin account.** Sign in at `https://<your-domain>` with the
   username `admin` and the initial password (from `.env`, or
   `docker compose logs api | grep "'admin' account"`), and set a new password.
   Until then, anyone who can read the API logs can sign in as admin.
6. **Certificate renewal.** nginx now holds port 80, so switch renewals to the
   webroot method once and add a hook that reloads nginx:

   ```bash
   sudo mkdir -p /var/www/certbot
   sudo certbot certonly --webroot -w /var/www/certbot -d <your-domain> --force-renewal
   printf '#!/bin/sh\ncd %s && docker compose exec -T frontend nginx -s reload\n' "$PWD" \
     | sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
   sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
   sudo certbot renew --dry-run
   ```

7. **Backups.** Set up off-instance backups (see [Database backups](#database-backups)),
   then invite your first landlord (see [Inviting landlords](#inviting-landlords)).

### Updating

```bash
git pull
docker compose up -d --build
```

Restart both `api` and `mcp` after any upgrade so that both enforce the same checks;
`up -d --build` does this. Keep `JWT_SECRET_KEY` unchanged unless you intend to sign
everyone out: changing it invalidates every issued token, for the web app and for
MCP clients.

### HTTPS and security headers

The `frontend` image serves HTTPS when both `NGINX_SSL_CERTFILE` and
`NGINX_SSL_KEYFILE` are set (the production file points them at
`/etc/letsencrypt/live/<DOMAIN>/`), and plain HTTP otherwise. Setting only one of
the two is an error.

nginx sends HSTS, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, and
`Referrer-Policy` on every response. On the web app it also sends a
`Content-Security-Policy` that allows only same-origin scripts, styles and requests.
The CSP also blocks the CDN assets of the Swagger page at `/api/docs`, so use
`/api/openapi.json` or a local backend for interactive docs.

nginx rate-limits per client IP:

- `/api/auth/login`, `/api/auth/register` and `/api/oauth/token`: 10 requests per
  minute (burst 5)
- `/mcp` and its OAuth discovery paths: 10 requests per second (burst 40)

### Database backups

The production `backup` service runs `pg_dump` at startup and then every
`BACKUP_INTERVAL_SECONDS` (default 86400, one day). Dumps are written to `BACKUP_DIR`
(default `./backups`), and dumps older than `BACKUP_RETENTION_DAYS` (default 14) are
deleted.

Local dumps sit on the same disk as the database, so they are lost along with the
EC2 instance or its EBS volume. To keep copies elsewhere:

- Set `BACKUP_S3_URI` (for example `s3://my-bucket/rental-mgmt`) to copy each dump to
  S3. The container uses the EC2 instance role, which needs `s3:PutObject` on that
  prefix. Containers can reach the role only if the instance metadata hop limit is
  at least 2:

  ```bash
  aws ec2 modify-instance-metadata-options --instance-id <id> \
    --http-put-response-hop-limit 2 --http-tokens required
  ```

- Add an S3 lifecycle rule to expire old dumps.
- Turn on EBS encryption and add a Data Lifecycle Manager snapshot policy for the
  volume.

To restore a dump (this replaces the current data):

```bash
docker compose exec -T db pg_restore -U rental -d rental_mgmt --clean --if-exists \
  < backups/rental_mgmt-<timestamp>.dump
docker compose restart api mcp
```

Test a restore before you rely on the backups.

### Other platforms

The images also run on other container platforms (ECS, Cloud Run, Kubernetes, and
so on). Run the backend image twice: once as the API (default command) and once as
MCP (`python -m app.mcp_server`) with the same `DATABASE_URL` and `JWT_SECRET_KEY`.
Build the frontend with `VITE_API_BASE_URL` set to the API's public URL, and set the
variables in the [configuration reference](#configuration-reference). The schema is
created at startup; add migrations before making schema changes on a production
database.

## Configuration reference

Backend (`api` and `mcp`; read from the environment or the project-root `.env`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./data/rental_mgmt.db` | SQLAlchemy URL, e.g. `postgresql+psycopg://user:pass@host:5432/rental_mgmt` |
| `JWT_SECRET_KEY` | — (required) | Token signing secret, at least 32 random characters (`openssl rand -hex 32`). Must be identical for `api` and `mcp`. Placeholders are rejected. Never give it to clients. |
| `JWT_ALGORITHM` | `HS256` | Must match between `api` and `mcp` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | Token lifetime |
| `SUPER_ADMIN_INITIAL_PASSWORD` | random | Initial `admin` password, at least 12 characters; used only when the account is created |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated browser origins allowed to call the API |
| `OPEN_REGISTRATION` | `false` | `true` lets anyone register a landlord or tenant without an invitation. Local development only. |
| `TRUST_PROXY_HEADERS` | `false` | Use nginx's `X-Real-IP` as the client IP. Enable only when the API is reachable solely through nginx. |

MCP server (`mcp` only):

| Variable | Default | Purpose |
| --- | --- | --- |
| `RENTAL_MGMT_MCP_TRANSPORT` | `stdio` | `stdio`, `streamable-http` or `sse` |
| `RENTAL_MGMT_MCP_HOST` / `_PORT` | `127.0.0.1` / `8001` | Listen address for HTTP transports |
| `RENTAL_MGMT_MCP_STREAMABLE_HTTP_PATH` / `_SSE_PATH` | `/mcp` / `/sse` | Endpoint paths |
| `RENTAL_MGMT_MCP_PUBLIC_BASE_URL` | `http://<host>:<port>` | Externally reachable MCP URL without `/mcp`, advertised in OAuth metadata |
| `RENTAL_MGMT_MCP_AUTH_SERVER_URL` | `http://localhost:8000` | Public API URL that issues tokens (`https://<DOMAIN>/api` in production) |
| `RENTAL_MGMT_MCP_SSL_CERTFILE` / `_KEYFILE` | — | Serve HTTP transports over TLS directly (both required) |
| `RENTAL_MGMT_MCP_TOKEN` | — | Landlord access token for the stdio transport |
| `RENTAL_MGMT_MCP_AUTH_ENABLED` | `true` | Cannot be turned off; `false` is rejected |

Compose (`.env` next to the compose files):

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOMAIN` | — (required for production) | Public host name; selects the certificate and sets CORS and MCP URLs |
| `COMPOSE_FILE` | — | `docker-compose.yml:docker-compose.prod.yml` to make production the default on the host |
| `BACKUP_INTERVAL_SECONDS` | `86400` | Time between dumps |
| `BACKUP_RETENTION_DAYS` | `14` | Local dump retention |
| `BACKUP_DIR` | `./backups` | Host directory for dumps |
| `BACKUP_S3_URI` | — | Optional S3 destination for each dump |

Frontend build:

| Variable | Purpose |
| --- | --- |
| `VITE_API_BASE_URL` | API URL the browser calls: `http://127.0.0.1:8000` locally, `/api` behind nginx |

## Administration

### Super admin

API startup creates one super-admin account with the username **admin**. Its initial
password is `SUPER_ADMIN_INITIAL_PASSWORD` if that is at least 12 characters and not
`admin`. Otherwise startup generates a random password and prints it once to the API
log. Sign in on the normal login page with `admin` in the **Email or admin username**
field. The first sign-in requires a new password of at least eight characters before
anything else is available.

The setting is used only when the account is created. Restarting never resets the
password. One exception protects older deployments: if the admin password is still
the old default `admin` and was never changed, startup replaces it with the
configured or generated password and revokes its sessions. Public registration
cannot create super admins.

### Inviting landlords

Landlord accounts are created by invitation only. Sign-ups without an invitation are
rejected, and the Register option appears only when the page is opened from an
invitation link. Tenants cannot self-register either, because landlords create
tenant records.

1. In the admin dashboard's **Landlord Invitations** card, optionally enter the
   landlord's email (then only that email can use the link).
2. Choose how long it stays valid (1–30 days, default 7) and select
   **Create Invite Link**.
3. Copy the link and send it yourself; the app does not send email. The link is
   shown only once.

The link (`https://<DOMAIN>/#invite=...`) opens the Register form with the
invitation applied and the role picker hidden. Signing up creates an active landlord
account and signs it in. Each link works once, and a failed sign-up (for example, an
email that is already registered) does not use it up. The card lists invitations as
Unused, Used (with who used it), Expired or Revoked, and unused links can be revoked.

The server stores only a SHA-256 hash of each code. The code is in the URL fragment,
which browsers do not send to the server, so it stays out of access logs.

### Managing landlords

The admin dashboard lists landlords with their active or deactivated status, last
login, last activity and active session count, with search, filters and ten entries
per page. Admins cannot see landlord properties, tenants, leases, bills, payments or
MCP data.

- **Deactivate** blocks new sign-ins and revokes existing API and MCP tokens.
  Reactivating allows new sign-ins but does not restore old tokens.
- **Reset password** revokes all of the landlord's sessions and does not reactivate a
  deactivated account.
- No rental data is deleted. Passwords are hashed and never returned. Admin actions
  record the actor, target, action and time in `admin_audit_events`, without
  passwords.

"Signed in" means an unexpired, unrevoked session exists, not that a browser is open.
Last activity updates at most once a minute on authenticated API or MCP requests;
refresh the dashboard to see new values. Logging out revokes the current session,
and changing your own password revokes all earlier sessions.

### Sign-in throttling

After 5 failed password attempts within 15 minutes, a login name is locked for 15
minutes from that client IP. Locked attempts return HTTP 429 with `Retry-After`, even
with the correct password. Failures are counted per lowercased login name and client
IP on both `/auth/login` and `/oauth/token`, including names with no account, so
lockouts do not reveal which emails are registered. A successful sign-in clears the
count. Because the count includes the IP, someone who knows a login name such as
`admin` can only lock out their own IP, not the account owner.

The client IP comes from `X-Real-IP` only when `TRUST_PROXY_HEADERS=true`; otherwise
the TCP peer address is used, because direct clients could forge the header.

Landlords create tenant records with `POST /tenants`, which does not accept a
`user_id`, so landlords cannot link or claim existing tenant user accounts.

## Billing guide

### Rent bills

- Rent bills cover the current month only. The API checks immediately at startup
  and every hour, including after downtime later in the month. No manual
  generation button is needed. The server's calendar/time zone determines the month.
- Each lease is committed separately; a failed lease or database scan is logged
  and retried on the next check. Generation runs outside the API event loop.
  Multiple workers and manual API generation serialize on a database lease lock
  to prevent duplicate automatic rent.
- A durable `rent_billing_runs` completion marker is committed with the bills.
  Completed months are not regenerated on restart, including rent intentionally
  deleted afterward. Creating/updating a lease still generates its current rent.
  Existing bills and payment balances are preserved.
- Restart the API after deploying this change; startup creates the additive
  completion table. On the first run, existing current-month rent is recognized
  and missing rent is generated. Historical deleted bills cannot be distinguished
  from missing bills before completion tracking existed.
- This does not backfill previous months or generate future-month bills. If the
  service was unavailable for a whole month, that older month needs explicit
  reconciliation. The existing owner-scoped
  `POST /bills/generate-current-month` and `POST /leases/{id}/generate-bills`
  endpoints remain for integrations. Monitor API logs for `Automatic rent failed`
  and `Automatic rent scan failed`; retries cannot fix persistent database or
  encryption-key configuration errors without operator intervention.
- When a lease starts or ends mid-month, the bill is prorated by the number of lease
  days in that month.
- A lease can have several tenants. The landlord sets each tenant's monthly rent share
  and deposit, and the shares must add up to the lease's monthly rent.
- Each payment records an amount and time. The bill's balance and status (open,
  partial or paid) are recalculated after every payment.

### Property bills and recurrence

Property bills are costs the landlord pays, and they are marked unpaid or paid.
**Create Property Bill** supports these recurrence options (API values in brackets):

| Option | `recurrence` | Bills created |
| --- | --- | --- |
| One-time bill | `one_time` | One bill |
| Repeated monthly bill | `monthly` | Every month in the period |
| Repeated bi-monthly bill (every two months) | `bi_monthly` | Every second month |
| Repeated quarterly bill (every three months) | `quarterly` | Every third month |
| Repeated bill every six months | `semi_annually` | Every sixth month |
| Repeated annual bill | `annually` | Every year |
| Bill for a specified period | `custom_period` | One bill covering the period |

Repeated bills need a period start and end. All bills are created upfront, starting in
the period's first month; the schedule is anchored to that period, not to calendar
quarters. The due date sets the day of the month, using the last day in shorter
months. The amount is charged once per generated bill. A period can span at most ten
years. The **Filter Property Bills** dialog narrows the list (by default, to bills due this month).

Manual bills (`POST /bills/manual`) charge the tenants of a lease and support the same
recurrence options.

### Extending repeated property bills

Use **Extend** on a monthly, bi-monthly, quarterly, six-monthly or annual property
bill. Choose an **Extend through** date and select **Preview Bills**. The preview lists
existing bills in that series due today or later, the proposed new bills with due
dates and amounts, and their total. An optional due day overrides the series' due day
for the new bills only.

**Confirm & Generate** creates only the previewed bills; cancelling or previewing
changes nothing. New bills copy the selected bill's title, category, amount and notes,
start unpaid, and join the same series. Their billing period runs from the day after
the series' previous end through the new end. Existing bills, payments and tenant
charges are untouched, and tenant charges for new bills must be generated separately.

- New dates keep the original cadence and fall after the series end, up to the new
  end. Months that already have a bill are skipped; deleted older occurrences are not
  recreated.
- You can extend at most ten years at a time. Any change after the preview requires a
  new preview, and concurrent confirmations cannot create the same occurrence twice.
- Bills created before series tracking existed are matched by property, title,
  category, recurrence, original period and creation time. The preview shows a warning
  so you can check the match and the inferred due day, and ambiguous overlapping
  batches are rejected. The first confirmation records the series for later
  extensions.

API: `POST /property-bills/{id}/extension-preview` with `through` and an optional
`due_day`, then `POST /property-bills/{id}/extend` with the same fields plus the
returned `preview_token`.

### Tenant charges

On a property bill, the **Generate tenant bills** icon button previews the eligible tenants, each
tenant's equal share, and the due date: the last day of the property bill's due month
(for example, November 1 becomes November 30). Tenants whose leases overlap the bill's
period are included once each; for bills without a period, the due date decides
eligibility. Rounding remainders are distributed one cent at a time, so the shares
always add up to the bill amount.

**Confirm & Generate** creates one bill per tenant; **Cancel** creates nothing. The
generated bills show the tenant's name, the month-end due date and a **Tenant charge**
label, separate from rent. A property bill can be split only once; deleting individual
charges does not reset it.

API: `GET /property-bills/{id}/tenant-bills-preview`, then
`POST /property-bills/{id}/generate-tenant-bills` with its `preview_token` in the JSON
body. If the bill or allocation changed in between, the API returns HTTP 409; fetch a
new preview and confirm again. Bill responses include `tenant_name`,
`is_tenant_charge` and `source_property_bill_id`.

## MCP server for AI agents

The MCP server gives AI agents read-only access to one landlord's data. It
authenticates with that landlord's MCP API key (recommended) or existing JWT access
token and resolves the landlord from the credential, never from tool arguments, so supplying another landlord's ID or email is
rejected. MCP sessions cannot be reused by another account. Admin and tenant tokens
are refused.

Tools:

| Tool | Returns |
| --- | --- |
| `database_status` | Confirms database access without exposing the URL or credentials |
| `list_landlords` | The signed-in landlord's profile |
| `list_properties` | Properties and units |
| `list_tenants` | Tenants with rent and deposit totals |
| `list_leases` | Leases with tenants, rent shares and deposits |
| `list_property_bills` | Landlord-paid property bills |
| `list_rental_bills` | Tenant bills and balances |
| `list_payments` | Tenant payments |
| `get_tenant_payment_history` | One tenant's bills and payments |
| `get_property_overview` | One property's leases, bills and rentals |

### Connect to the production server

Sign in as a landlord, open the profile menu, and choose **MCP API Keys**.
Give the key an agent name, choose an optional expiration, and confirm your current
password. Copy the key immediately: its full value is shown only once. The server
stores only a SHA-256 hash of the randomly generated 256-bit secret.

Configure your agent with the key instead of a login token:

```bash
# Set MCP_API_KEY securely in your shell first; do not commit it to source control.
claude mcp add --transport http rental-management https://<DOMAIN>/mcp \
  --header "Authorization: Bearer $MCP_API_KEY"
```

`X-API-Key: <key>` is also supported on HTTP and SSE. Send only one credential
header; duplicate headers or both header types together are rejected. Keys in URL
query parameters are not accepted. Clients that only support interactive OAuth
must continue using JWT authentication or use a connector supporting custom headers.

- Each key is restricted to read-only MCP access to its owner's data. It cannot
  authenticate to the regular REST API, create keys, or perform administration.
- By default it does not expire; you can choose 30, 90, or 365 days in the UI.
- Normal login/logout does not change it. Revoke it from **MCP API Keys** whenever
  needed. Revocation is checked on subsequent requests and tool calls, including
  existing MCP sessions; it cannot recall data already returned to an agent.
- Password changes, admin password resets, and account deactivation revoke all
  of that landlord's keys. Reactivation does not restore revoked keys.
- JWT signing-secret rotation does not revoke API keys. Revoke keys separately
  during credential rotation. Treat them like passwords and use HTTPS remotely.
- Key metadata includes creation, expiration, and last-use time (updated at most
  once per minute). API key usage is separate from browser login-session counts.

Deploy/restart both API and MCP from this version. Startup creates the new
`mcp_api_keys` table without rewriting rental records or changing encryption
preferences. No API keys are created automatically for existing landlords.

Management endpoints (landlord JWT required):
`GET /me/mcp-api-keys`, `POST /me/mcp-api-keys` with
`{"name":"My agent","current_password":"...","expires_in_days":null}`, and
`DELETE /me/mcp-api-keys/{id}`. The creation response returns `api_key` once with
`Cache-Control: no-store`; lists contain only metadata.

### Existing JWT connections

JWT authentication remains supported. Get a token with the landlord's own email and password (set `LANDLORD_EMAIL` and
`LANDLORD_PASSWORD` first), then register the server with Claude Code:

```bash
TOKEN=$(curl --fail-with-body -sS -X POST https://<DOMAIN>/api/oauth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "grant_type=password" \
  --data-urlencode "username=$LANDLORD_EMAIL" \
  --data-urlencode "password=$LANDLORD_PASSWORD" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

claude mcp add --transport http rental-management https://<DOMAIN>/mcp \
  --header "Authorization: Bearer $TOKEN"
```

For the local Compose stack, use `http://localhost:8000/oauth/token` and
`http://localhost:8001/mcp`.

OAuth discovery metadata is served at `/.well-known/oauth-protected-resource/mcp`
and `/.well-known/oauth-authorization-server`, and 401 responses carry a
`WWW-Authenticate` header that points to it.

Tokens:

- Treat a token like a password: while it is valid, anyone who can reach the endpoint
  can read that landlord's data.
- Each sign-in issues a new token (with its own `jti`) for the same landlord account.
- Tokens expire after `ACCESS_TOKEN_EXPIRE_MINUTES`. Deactivating the landlord or
  resetting their password revokes them; rotating `JWT_SECRET_KEY` revokes all tokens.

### Run the MCP server yourself

**stdio**, for an MCP client on the same host:

```bash
RENTAL_MGMT_MCP_API_KEY="$MCP_API_KEY" python -m app.mcp_server
```

The repository's `.mcp.json` registers this stdio server for Claude Code started in
the project root. It reads `RENTAL_MGMT_MCP_API_KEY` from the shell that launches
`claude` (or the legacy `RENTAL_MGMT_MCP_TOKEN`) and `RENTAL_MGMT_PYTHON`, a Python interpreter
with the requirements installed (default: `python` on `PATH`):

```bash
export RENTAL_MGMT_MCP_API_KEY="$MCP_API_KEY"
export RENTAL_MGMT_PYTHON="$PWD/.venv/bin/python"
claude
```

For other MCP clients:

```json
{
  "mcpServers": {
    "rental-management": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/path/to/rental-mgmt",
      "env": { "RENTAL_MGMT_MCP_API_KEY": "<landlord MCP API key>" }
    }
  }
}
```

For stdio, the API-key variable takes precedence over the legacy token variable.
HTTP/SSE never fall back to process environment credentials: each request must
carry its own key or token.

**Streamable HTTP** (endpoint `/mcp`) or **SSE** (endpoint `/sse`):

```bash
RENTAL_MGMT_MCP_TRANSPORT=streamable-http \
RENTAL_MGMT_MCP_HOST=0.0.0.0 \
RENTAL_MGMT_MCP_PORT=8001 \
python -m app.mcp_server
```

Authentication is always required. To serve TLS directly instead of behind nginx, also
set `RENTAL_MGMT_MCP_SSL_CERTFILE` and `RENTAL_MGMT_MCP_SSL_KEYFILE`. When the server
is reached from other machines, set `RENTAL_MGMT_MCP_PUBLIC_BASE_URL` and
`RENTAL_MGMT_MCP_AUTH_SERVER_URL` to their public URLs; the default public URL
(`http://0.0.0.0:8001` inside Docker) is not reachable by clients. Keep remote
endpoints on HTTPS and behind a firewall, because they expose rental, lease, tenant,
bill and payment data.

## Google Calendar exports

In **Edit Profile > Google Calendar**, each landlord can connect Google and separately
enable **Automatically sync new rental bills to Google Calendar**. Both new and
existing accounts start **off**. Connecting alone does not enable exports.

This is one-way creation of all-day due-date events in an app-created **Rental Due
Dates** calendar. Events contain the tenant and property names, unit, original rent
amount, billing month, bill ID, and a link to the rental website. They are private,
do not block time, and do not invite tenants or configure reminders.

- Only newly generated `rent` bills created while enabled are queued, including
  rent generated on lease creation, manual current-month generation, and the monthly scheduler.
- Existing rent is not backfilled. Manual charges, utility charges, and property bills are excluded.
- Later payments, edits, and bill deletions do not update or remove exported events.
- Turning off or disconnecting cancels pending exports and keeps Google events.
  Re-enabling starts with subsequently generated bills; cancelled exports are not replayed.
- Deleting a bill before delivery removes its pending export.
- The worker runs every minute, processes up to 100 pending exports per pass, and
  retries temporary failures with backoff up to an hour. Persistent event IDs
  prevent duplicate events on retries or concurrent API workers.
- Check pending exports, last successful export, and errors in the profile.
  Revoked Google authorization disables exports and requires reconnecting and enabling again.

### Server setup

1. In Google Cloud Console, enable the **Google Calendar API**, configure the OAuth
   consent screen, and create an OAuth client of type **Web application**.
2. Request only `https://www.googleapis.com/auth/calendar.app.created`. This limits
   calendar access to secondary calendars created by this app, not the user's primary calendar.
3. Add an exact authorized redirect URI:
   `https://YOUR_DOMAIN/api/google-calendar/callback` in production, or
   `http://localhost:8000/google-calendar/callback` for direct local API development.
4. Set these **backend-only** environment variables in `.env`:

   ```dotenv
   GOOGLE_CALENDAR_CLIENT_ID=your-web-client-id
   GOOGLE_CALENDAR_CLIENT_SECRET=your-web-client-secret
   GOOGLE_CALENDAR_REDIRECT_URI=https://YOUR_DOMAIN/api/google-calendar/callback
   GOOGLE_CALENDAR_APP_URL=https://YOUR_DOMAIN
   ```

5. Configure the existing `DATA_ENCRYPTION_MASTER_KEY` or `DATA_ENCRYPTION_KEY_FILE`.
   Google refresh/access tokens are always encrypted independently of the landlord's
   rental-data encryption preference. For a fresh local key, use
   `python -m scripts.manage_encryption init-key`. Never replace a key used by an
   existing deployment. Keep the key separate from database backups.
6. Rebuild/restart the API and frontend. Compose passes the Google settings only
   to the API. Two integration tables are created automatically at startup; no
   existing account is opted in.
7. Sign in on the website, open Edit Profile, and allow the Google connection popup.
   Grant permission, close the completion tab, click **Refresh Calendar Status**,
   then enable automatic exports. The first connection creates the dedicated calendar.

Use the same hostname throughout local testing (`localhost` or `127.0.0.1`, not
both) because OAuth is bound to a short-lived HttpOnly browser cookie. Production
requires HTTPS. CORS must allow the actual frontend origin. The API uses one-time
OAuth state, PKCE, and account-version checks; Google secrets are never returned
to the frontend.

For the packaged iPhone client, connect Google on the website in a normal browser
first; embedded native webviews are not used for Google authorization. Refresh
status in the app to enable, disable, or disconnect the integration.

Google projects in **Testing** mode require approved test users and may issue
refresh tokens that expire after seven days. Complete Google's publishing and,
where required, verification process before offering this to production users.
Disconnect removes locally stored credentials but keeps the calendar; revoke the
app's Google grant in Google Account permissions to remove Google's authorization
as well (this may affect other landlords connected using the same Google account).

Calendar endpoints (landlord authentication required except the OAuth callback):
`GET/PUT/DELETE /me/google-calendar`, `POST /me/google-calendar/connect`, and
`GET /google-calendar/callback`.

## API

Interactive docs are at `/docs` on a local backend. All endpoints except
registration, sign-in and `/health` require `Authorization: Bearer <token>`. Behind
nginx, prefix every path with `/api`.

### Walkthrough

```bash
API=http://localhost:8000          # production: https://<DOMAIN>/api

# 1. As admin, create a landlord invitation (returns the code once)
ADMIN=$(curl -sS -X POST $API/auth/login -H "Content-Type: application/json" \
  -d '{"email":"admin","password":"<admin password>"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
curl -sS -X POST $API/admin/invites -H "Authorization: Bearer $ADMIN" \
  -H "Content-Type: application/json" -d '{"email":"owner@example.com","expires_in_days":7}'

# 2. Register the landlord with the invitation code
curl -X POST $API/auth/register -H "Content-Type: application/json" \
  -d '{"email":"owner@example.com","full_name":"Owner","password":"password123","role":"landlord","invite_code":"<code>"}'

# 3. Sign in and keep the access_token
TOKEN=$(curl -sS -X POST $API/auth/login -H "Content-Type: application/json" \
  -d '{"email":"owner@example.com","password":"password123"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
AUTH="Authorization: Bearer $TOKEN"

# 4. Create a property, a tenant and a lease
curl -X POST $API/properties -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"name":"Unit 1","address":"123 Main St","description":"Two bedroom apartment"}'
curl -X POST $API/tenants -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"full_name":"Tenant One","email":"tenant@example.com","phone":"+1-555-0100"}'
curl -X POST $API/leases -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"property_id":1,"tenants":[{"tenant_id":1,"monthly_rent":"1500.00","deposit":"1500.00"}],"start_date":"2026-10-01","end_date":"2027-09-30","monthly_rent":"1500.00","notes":"Annual lease"}'

# 5. Generate this month's bills, record a payment, check balances
curl -X POST $API/bills/generate-current-month -H "$AUTH"
curl -X POST $API/bills/1/payments -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"amount":"500.00","paid_at":"2026-10-07T10:00:00Z","note":"Partial payment"}'
curl $API/bills -H "$AUTH"
```

With `OPEN_REGISTRATION=true` (local development only), step 1 and the
`invite_code` can be skipped.

### Endpoints

| Area | Endpoints |
| --- | --- |
| Health | `GET /health` |
| Auth | `POST /auth/register`, `POST /auth/login`, `POST /oauth/token` (password grant, form-encoded), `POST /auth/logout` |
| Account | `GET /me`, `PATCH /me`, `POST /me/password` |
| Properties | `POST /properties`, `GET /properties`, `GET/PATCH/DELETE /properties/{property_id}` |
| Units | `POST /properties/{property_id}/units`, `GET /properties/{property_id}/units`, `GET /units`, `PATCH/DELETE /units/{unit_id}` |
| Tenants | `POST /tenants`, `GET /tenants`, `GET/PATCH/DELETE /tenants/{tenant_id}` |
| Leases | `POST /leases`, `GET /leases`, `GET/PATCH/DELETE /leases/{lease_id}`, `POST /leases/{lease_id}/generate-bills` |
| Property bills | `POST /property-bills`, `GET /property-bills`, `PATCH/DELETE /property-bills/{bill_id}`, `POST /property-bills/{bill_id}/extension-preview`, `POST /property-bills/{bill_id}/extend`, `GET /property-bills/{bill_id}/tenant-bills-preview`, `POST /property-bills/{bill_id}/generate-tenant-bills` |
| Tenant bills | `POST /bills/generate-current-month`, `POST /bills/manual`, `GET /bills`, `GET/DELETE /bills/{bill_id}` |
| Payments | `POST /bills/{bill_id}/payments`, `GET /payments` |
| Tenant | `GET /tenant/bills` |
| Admin | `GET /admin/landlords`, `PATCH /admin/landlords/{user_id}/status`, `POST /admin/landlords/{user_id}/password`, `POST /admin/invites`, `GET /admin/invites`, `POST /admin/invites/{invite_id}/revoke` |

Admin endpoints require the super admin, after the initial password change.

## Database encryption

Encryption is **opt-in per landlord and off by default**. In **Edit Profile >
Data Encryption**, the landlord selects **Encrypt my rental data**, enters their
current password, and clicks **Apply Encryption Setting**. This separately
confirmed action converts existing records and controls all future writes.
Saving other profile fields does not change encryption. Super admins and tenants
cannot change this setting. Turning it off requires the same confirmation and
converts the landlord's records back to readable plaintext.

Unencrypted accounts require no encryption key. Their sensitive fields are stored
as readable JSON in the storage payload column; normal billing and MCP behavior
is unchanged. The payload column retains its historical `encrypted_data` name,
but the `plain:v1:` prefix explicitly identifies unencrypted data. Encryption-enabled
accounts reject plaintext payloads instead of silently accepting a downgrade.
The owner preference and all of that owner's records change in one transaction,
with writes serialized against preference changes. Failed conversions roll back.

When enabled, sensitive rental records use AES-256-GCM before database storage.
Each opted-in landlord has a separate random 256-bit data key. Only its wrapped (encrypted)
form is stored in `landlord_encryption_keys`. API and MCP share a separate master
key, supplied outside the database, to unwrap these keys. This is independent of
JWT signing and login passwords. Nonces are random for every write; authentication
binds ciphertext to the landlord, table, and record ID.

Encrypted fields:

- Property name, address, and description; unit name and description.
- Managed tenant name, email, phone, and notes.
- Lease rent and notes; each tenant's rent share and deposit.
- Property bill title, amount, and notes.
- Rental bill title, amount due, amount paid, and balance.
- Payment amount and note.

IDs, ownership links, dates, categories, recurrence, statuses, and account
administration/authentication data (including login-account names and emails,
invitations, and audit/session records) remain readable. New unassigned tenant
profiles default to plaintext; previously encrypted ones keep their individual
keys. Ownership transfers require a separate workflow and are intentionally
rejected. Encrypted unit names use keyed lookup hashes for uniqueness; unencrypted
ones use ordinary hashes. Protected values cannot be queried as individual SQL
columns; API/MCP read them through the ORM after existing authorization checks.

This protects logical database reads and new logical backups, **not application
server compromise**. The application necessarily decrypts data in memory.
The existing admin password-reset limitation is preserved: an administrator who
resets a landlord's password can sign in as that landlord and access their data.
Password resets revoke sessions but do not replace encryption keys.

### Key setup

This is optional for unencrypted accounts, but must be configured before anyone
can enable encryption. Choose one source and use the same key for API and MCP:

- `DATA_ENCRYPTION_MASTER_KEY`: URL-safe base64 encoding of 32 random bytes,
  injected by a deployment secret manager. This takes precedence over the file.
- `DATA_ENCRYPTION_KEY_FILE`: a private file containing that base64 value. Locally
  it defaults to `.secrets/data-encryption.key` relative to the project root.
  `python -m scripts.manage_encryption init-key` creates it with mode `0600` and
  refuses to overwrite an existing file. `.secrets/` is git-ignored.

Docker Compose mounts `.secrets` read-only at `/run/secrets` in API and MCP only,
not the database or backup service. For a container-only installation, first
create a private `.secrets` directory, then initialize the file with a one-off
writable mount:

```bash
mkdir -m 700 .secrets
docker compose build api
docker compose run --rm --no-deps -v "$PWD/.secrets:/run/secrets:rw" api \
  python -m scripts.manage_encryption init-key
```

Back up the master key separately from database dumps with tightly restricted
access. Restores need both the master key and the database's wrapped-key table.
There is no password-based recovery for a lost encryption key. Never regenerate,
replace, or delete an in-use key; automatic key rotation is not implemented.
Missing, wrong, corrupted, or missing-per-landlord keys fail closed for encrypted
data. A missing server key prevents enabling encryption without changing the
landlord's existing plaintext data. Disabling encryption retains the wrapped
key for backups and later re-enabling; do not delete the master key while any
encrypted records or backups still need it.

### Existing database migration

Do this during a maintenance window. Do not run old and new application versions
against the database together, and do not roll back to old code against the
encrypted schema.

1. Stop API, MCP, and other database writers.
2. Take a consistent database backup and verify that it can be restored. Keep
   this plaintext backup restricted; do not put it beside the encryption key.
3. For databases with existing encrypted records, keep the current master key.
   For plaintext-only databases, no encryption key is required for migration.
4. Install updated dependencies, then run the migration and verification:

```bash
python -m scripts.manage_encryption migrate --confirm-backup
python -m scripts.manage_encryption verify
```

For Docker, with the database running and API/MCP stopped:

```bash
docker compose run --rm --no-deps api python -m scripts.manage_encryption migrate --confirm-backup
docker compose run --rm --no-deps api python -m scripts.manage_encryption verify
```

5. Start the updated API and MCP. Check landlord login, bills, payments, and MCP.

This is a one-time **storage schema migration**, not landlord consent to encrypt.
Legacy plaintext accounts remain unencrypted by default. Upgrades from the
previous encryption-enabled release detect existing ciphertext and preserve
encryption for those accounts. No user data is automatically encrypted merely
because a server key is configured.

Conversion of schema, records, and wrapped keys is transactional on SQLite and
PostgreSQL. It preserves record IDs, ownership, dates, amounts, and payment links,
and is idempotent. Unsupported/custom SQLite indexes or triggers are rejected
rather than silently lost. Very old schemas should first be upgraded with the
previous application release.

SQLite conversion enables secure deletion, truncates WAL, and compacts the
database afterward. If a reader blocks post-commit cleanup, stop it and rerun the
migration; the committed encrypted rows are not rewritten. PostgreSQL conversion
removes the plaintext columns logically, but old physical pages, WAL archives,
replicas, snapshots, logs, and old backups can still contain plaintext. Plan
PostgreSQL table compaction and historical-backup retention with your database
operator. Encryption does not retroactively protect those historical copies.
Likewise, enabling encryption from the profile only protects current logical
records and future writes: physical old pages and backups still need separate
retention/compaction handling.

Startup adds the preference flag when needed, preserving existing encrypted
accounts, but refuses legacy record schemas until the offline migration is run.
Recovery from a failed transactional
conversion leaves the original schema intact; after a successful conversion,
rollback requires restoring the pre-migration backup with the previous release.

## Testing

Backend tests use the standard library `unittest` with an in-memory SQLite database
and a fresh, process-local encryption key (never the deployment key):

```bash
python -m scripts.run_tests
```

SQLite encryption, migration rollback, ciphertext integrity, and existing
API/MCP workflows are covered by this suite. The optional PostgreSQL integration
test creates and removes a randomly named schema without touching existing
tables. Run it against a test database whose account can create schemas:

```bash
TEST_ENCRYPTION_POSTGRES_URL="$TEST_DATABASE_URL" python -m scripts.run_tests
```

Without that variable the PostgreSQL test is skipped. Validate it on your
PostgreSQL version before production migration.

Frontend tests and type-checked build:

```bash
cd frontend
npm test
npm run build
```
