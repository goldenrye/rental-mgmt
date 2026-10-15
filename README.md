# Rental Management App

Cloud-ready rental management application with a FastAPI backend and React frontend.

## Features

- Register users as `landlord` or `tenant`
- Authenticate with JWT bearer tokens
- Landlords can create, list, update, and delete rental properties
- Landlords can create, list, update, and delete multiple units under each property
- Landlords can create, list, update, and delete managed tenants
- Landlords can create, list, update, and delete leases that bind tenants to a property or a specific unit
- Active leases can generate monthly rental bills
- Payments are recorded against bills with paid amount and paid time
- Bill balance and status are recalculated after every payment
- Browser-based landlord dashboard for properties, tenants, leases, bills, and payments

For leases with multiple tenants, the landlord assigns each tenant's monthly rent share and deposit. Tenant rent shares must add up to the lease monthly rent.

Automatic rent bill generation is current-month only. When a lease starts or ends mid-month, the bill is prorated by active lease days in that month. The backend also runs an in-process scheduler that automatically generates the current month bill on the 1st day of each month.

Property and manual bills support `bi_monthly` recurrence (every two months, not twice a month). In Create Property Bill, select **Repeated bi-monthly bill (every two months)** and supply the period start and end. Like monthly recurrence, bills are created upfront starting in the period's first month, then every second month through the period's final month. The due date supplies the day of the month, clamped to the last day for shorter months; the amount is charged once per generated bill.

For bills repeating quarterly, select **Repeated quarterly bill (every three months)** (`quarterly` in the API). This generates bills upfront in the period's first month and every third month afterward, using the same due-day clamping and amount-per-bill rules. The schedule is anchored to the selected period, not fixed calendar quarters.

For bills repeating every six months, select **Repeated bill every six months** (`semi_annually` in the API). This uses the same period and due-day rules, generating a bill in the period's first month and every sixth month afterward.

### Extending Repeated Property Bills

Use **Extend** on a monthly, bi-monthly, quarterly, six-monthly, or annual property bill. Choose an **Extend through** date and select **Preview Bills**. The popup lists existing bills in that series due today or later, the proposed new bills with due dates and amounts, and the total for the new bills. An optional due day overrides the series due day for new bills only; shorter months use their last day.

**Confirm & Generate** creates only the proposed property bills. Cancel and preview make no database changes. Existing bills, periods, payments, and tenant charges remain unchanged. New bills copy the selected bill's title, category, amount, and notes, start unpaid, and belong to the same series. Their billing period runs from the previous series end plus one day through the chosen new end. Tenant charges must still be generated separately.

New dates retain the original cadence and must fall after the series end and on or before the new end. Existing bill months are skipped; deleted older occurrences are not regenerated. Extend at most ten years at a time. Changes after preview require a refreshed preview, and concurrent confirmations cannot create the same series occurrence twice.

Newly created repeated property bills have explicit series tracking. For older bills, the preview matches property, title, category, recurrence, original period, and creation time and displays a warning to review the match and inferred due day. Ambiguous overlapping legacy batches are rejected instead of silently combined. The first confirmation records membership for future extensions. The two new tracking tables are created automatically at backend startup; no existing bill rows are rewritten.

API: `POST /property-bills/{id}/extension-preview` accepts `through` and optional `due_day`. `POST /property-bills/{id}/extend` accepts the same fields plus the returned `preview_token`. Both require the bill's landlord.

### Tenant Charges

On a property bill, **Generate Tenant Bill** opens a read-only preview of the eligible tenants, their equal shares, and the tenant charge due date: the last day of the original bill's due-date month (for example, November 1 becomes November 30). Tenants whose leases overlap the original bill's stored period are included once each; bills without a period use the original due date for eligibility. Rounding remainders are distributed one cent at a time so shares always add up to the original amount.

**Confirm & Generate** creates one bill per tenant. **Cancel** creates nothing. Generated bills show each tenant's name, the month-end due date, and a **Tenant charge** label, separate from monthly rent. Existing bills and regular rent due dates are not changed. The source property bill can only be generated once; deleting individual charges does not reset it.

API clients must first call `GET /property-bills/{id}/tenant-bills-preview`, then send its `preview_token` in the JSON body of `POST /property-bills/{id}/generate-tenant-bills`. Changes to the bill or allocation invalidate the preview and return HTTP 409; fetch a fresh preview and confirm again. Bill responses include `tenant_name`, `is_tenant_charge`, and `source_property_bill_id`.

## Run Backend Locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/init_env.py
uvicorn app.main:app --reload
```

The setup command creates a private, git-ignored `.env` with a random JWT signing
key. It never overwrites an existing `.env` or rotates an existing key. Both API
and MCP load this file from the project root, even when launched elsewhere.
Environment variables override the file: remove any old placeholder
`JWT_SECRET_KEY` from your shell or service configuration.

If you previously copied `.env.example`, replace its placeholder `JWT_SECRET_KEY`
with a secret generated by `openssl rand -hex 32`. Use the same secret for API and
MCP, restart both, and sign in again. Never share the signing secret with clients.

Open the API docs at [http://localhost:8000/docs](http://localhost:8000/docs).

By default, local data is stored in `data/rental_mgmt.db` under the project root. The application resolves relative SQLite paths against the project root, so restarting `uvicorn` from another directory still uses the same local database file. On startup, the backend prints the resolved database URL as `Rental Management database: ...`.

To confirm which database file the app is using and how many records it contains:

```bash
python scripts/db_status.py
```

## Super Admin

API startup creates a single super-admin account with username **admin**. Its initial
password is `SUPER_ADMIN_INITIAL_PASSWORD` when that is at least 12 characters and not
`admin`. Otherwise, startup generates a random initial password and prints it once to
the API log (`docker compose logs api | grep "'admin' account"`). Use the normal login
page's **Email or admin username** field. The first login requires a different
password of at least eight characters before landlord management is available.
Restarting does not reset the admin password. Public registration cannot create
super admins.

The setting is used only when creating the account and never overwrites a password
the admin has changed. One exception protects older deployments: if the admin
account still has the old default password `admin` and has never changed it,
startup replaces it with the configured or generated initial password and revokes
its sessions.

Sign in as admin and change the initial password right after the first deployment.
Until you do, anyone who can read the API logs can sign in as admin.

### Inviting landlords

Landlord accounts are created by invitation only: signing up without an
invitation link is rejected, and the Register option appears only when the page
is opened from an invitation link. Tenant self-registration is also rejected,
because tenant records are created by landlords. Set `OPEN_REGISTRATION=true`
(local development only) to allow anyone to register an active landlord or
tenant account without an invitation.

To invite a landlord, open the admin dashboard's **Landlord Invitations** card,
optionally enter the landlord's email (then only that email can use the link),
choose how long it stays valid (1–30 days, default 7), and select **Create
Invite Link**. Copy the link and send it yourself, by email or text; the app
does not send email.

The link opens the Register form with the invitation applied. Signing up with it
creates an active landlord account that is signed in immediately. Each link
works once. The card lists every invitation as Unused, Used (with who used it),
Expired, or Revoked, and an unused link can be revoked. The link is shown only
when it is created: the server stores only a SHA-256 hash of the code. The code is
in the URL fragment (`#invite=...`), which browsers do not send to the server, so
it does not appear in access logs.

API: `POST /admin/invites` with optional `email` and `expires_in_days` returns the
`code` once; `GET /admin/invites` lists invitations; `POST /admin/invites/{id}/revoke`
revokes an unused one. `POST /auth/register` accepts an optional `invite_code`.

The dedicated admin dashboard only manages landlord accounts. It lists active or
deactivated status, last login, last activity, and active session counts, with
search, filtering, and ten entries per page. Admins cannot access landlord
properties, tenants, leases, bills, payments, tenant-portal data, or MCP tools.

Deactivating a landlord blocks new logins and revokes existing API and MCP tokens.
Reactivation allows fresh logins but does not restore old tokens. Password resets
also revoke all sessions and do not reactivate a deactivated account. No rental
data is deleted. Passwords are hashed, never returned by the management API, and
admin actions record the actor, target, action, and timestamp in
`admin_audit_events`, without recording passwords.

Login history begins with this feature; historical login times cannot be
reconstructed. "Signed in" means an unexpired, unrevoked session exists, not that
the browser is currently open. Last activity updates at most once per minute on
authenticated API or MCP requests. Refresh the dashboard to update the snapshot.
Logout revokes the current session; changing your own password revokes all prior
sessions. Existing pre-upgrade tokens remain valid until expiry or account
revocation, but are not counted as tracked sessions.

The new `user_access`, `login_sessions`, and `admin_audit_events` tables are
created automatically. Existing landlord accounts default to active. Restart
both API and MCP after upgrading so both enforce the new checks.

Management endpoints (super admin only, after initial password change):

- `GET /admin/landlords`
- `PATCH /admin/landlords/{user_id}/status` with `{"is_active": false}` or `true`
- `POST /admin/landlords/{user_id}/password` with `{"new_password": "..."}`

`POST /auth/logout` revokes the current authenticated session.

### Sign-in throttling

After 5 failed password attempts within 15 minutes, a login name is locked for
15 minutes from that client IP. Locked attempts return HTTP 429 with `Retry-After`, even when the
password is correct. Failures are counted per lowercased login name and client IP on both `/auth/login` and
`/oauth/token`, including names with no account, so lockouts do not reveal
which emails are registered. A successful sign-in clears the count. Because the
count includes the IP, someone who knows a login name (such as `admin`) can only
lock out their own IP, not the account owner.

The client IP comes from nginx's `X-Real-IP` header only when
`TRUST_PROXY_HEADERS=true` (set in `docker-compose.prod.yml`, where the API port is
not published). Otherwise the TCP peer address is used, because clients that reach
the API directly could forge the header. The nginx frontend also limits `/api/auth/login`,
`/api/auth/register`, and `/api/oauth/token` to 10 requests per minute per client IP
(burst 5).

Landlords create tenant records with `POST /tenants`. That endpoint does not accept
`user_id`, so landlords cannot link or claim existing tenant user accounts.

## Run MCP Server For AI Agents

The project includes a read-only MCP server that lets AI agents query only the authenticated landlord's data from the same database used by the FastAPI backend. Every list and detail tool is account-scoped; supplying another landlord's ID or email is rejected. MCP sessions cannot be reused by a different account.

API and MCP must use the same strong `JWT_SECRET_KEY` (at least 32 characters, generated randomly, for example with `openssl rand -hex 32`). Placeholder secrets are rejected. This is a server signing secret, not a per-landlord password; never give it to MCP clients. Use HTTPS for remote token requests and MCP connections.

Run it as a local stdio server when the MCP client runs on the same host:

```bash
RENTAL_MGMT_MCP_TOKEN="$TOKEN" python -m app.mcp_server
```

Example local MCP client configuration:

```json
{
  "mcpServers": {
    "rental-management": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/Users/bytedance/Projects/rental-mgmt",
      "env": {
        "RENTAL_MGMT_MCP_TOKEN": "<your landlord access token>"
      }
    }
  }
}
```

Run it as a remote streamable HTTP server when MCP clients connect over the network:

```bash
RENTAL_MGMT_MCP_TRANSPORT=streamable-http \
RENTAL_MGMT_MCP_HOST=0.0.0.0 \
RENTAL_MGMT_MCP_PORT=8001 \
python -m app.mcp_server
```

The remote MCP endpoint is:

```text
http://localhost:8001/mcp
```

Remote MCP authentication is mandatory; `RENTAL_MGMT_MCP_AUTH_ENABLED=false` is rejected. The MCP server validates OAuth bearer JWTs issued by the FastAPI app, so `JWT_SECRET_KEY` and `JWT_ALGORITHM` must match between the API and MCP server. Stdio also requires a landlord token via `RENTAL_MGMT_MCP_TOKEN`; it no longer provides unrestricted database access.

Get a landlord access token using that landlord's own email and password. Set `LANDLORD_EMAIL` and `LANDLORD_PASSWORD` in your shell first:

```bash
TOKEN=$(curl --fail-with-body -sS -X POST http://localhost:8000/oauth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "grant_type=password" \
  --data-urlencode "username=$LANDLORD_EMAIL" \
  --data-urlencode "password=$LANDLORD_PASSWORD" \
  | python -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
```

Register the remote MCP server in Claude Code with the bearer token:

```bash
claude mcp add --transport http rental-management http://localhost:8001/mcp \
  --header "Authorization: Bearer $TOKEN"
```

Each login issues a unique token (`jti`) whose signed `sub` identifies the authenticated user. Logging in repeatedly with the same credentials still accesses the same landlord account, not a new account. Landlord identity is resolved from the database, never from tool arguments.

Treat any bearer token like a password. If it is leaked while valid, anyone who can reach the MCP endpoint can access that landlord's data. Tokens expire according to `ACCESS_TOKEN_EXPIRE_MINUTES`; rotate `JWT_SECRET_KEY` on both services to revoke all issued JWTs. After upgrading, install the updated requirements (MCP SDK 1.30+), restart API and MCP, and obtain fresh tokens. Existing valid tokens are also restricted by the new ownership checks.

To serve the remote endpoint over HTTPS, point the server at a certificate and private key (both are required; this applies to `streamable-http` and `sse`):

```bash
RENTAL_MGMT_MCP_TRANSPORT=streamable-http \
RENTAL_MGMT_MCP_HOST=0.0.0.0 \
RENTAL_MGMT_MCP_PORT=8001 \
RENTAL_MGMT_MCP_SSL_CERTFILE=/path/to/fullchain.pem \
RENTAL_MGMT_MCP_SSL_KEYFILE=/path/to/privkey.pem \
python -m app.mcp_server
```

The endpoint is then `https://<your-host>:8001/mcp`. `docker-compose.prod.yml` does not use this: it keeps the `mcp` container unpublished and serves MCP through nginx at `https://<DOMAIN>/mcp`, with TLS, a per-IP rate limit (10 requests/second, burst 40), and the OAuth discovery paths proxied from the same origin.

If you prefer SSE transport:

```bash
RENTAL_MGMT_MCP_TRANSPORT=sse \
RENTAL_MGMT_MCP_HOST=0.0.0.0 \
RENTAL_MGMT_MCP_PORT=8001 \
python -m app.mcp_server
```

The SSE endpoint is:

```text
http://localhost:8001/sse
```

For Docker Compose, the `mcp` service starts automatically and exposes the streamable HTTP endpoint at [http://localhost:8001/mcp](http://localhost:8001/mcp).

Keep remote MCP endpoints private or protected behind your network/auth gateway because they expose rental, bill, lease, tenant, and payment data.

Available MCP tools:

- `database_status`: verify authenticated database access without exposing the database URL or credentials.
- `list_landlords`: show only the signed-in landlord's profile.
- `list_properties`: list properties and units.
- `list_tenants`: list tenants, rent totals, and deposit totals.
- `list_leases`: list leases with tenants, rent shares, and deposits.
- `list_property_bills`: list landlord-paid property bills.
- `list_rental_bills`: list tenant-payable rental bills and balances.
- `list_payments`: list tenant payment records.
- `get_tenant_payment_history`: show one tenant's bills and payments.
- `get_property_overview`: show one property's leases, bills, and rentals.

## Run Frontend Locally

In a second terminal:

```bash
cd frontend
cp .env.example .env
npm install
npm run dev
```

Open the web app at [http://localhost:5173](http://localhost:5173).

## Run With Docker Compose

```bash
docker compose up --build
```

The frontend listens on [http://localhost:5173](http://localhost:5173). The API listens on [http://localhost:8000](http://localhost:8000), backed by Postgres.

### Serving the frontend over HTTPS

The frontend's nginx serves HTTPS when both `NGINX_SSL_CERTFILE` and `NGINX_SSL_KEYFILE` are set; port 80 then redirects to HTTPS on 443. nginx also proxies `/api/` to the `api` service, so build the frontend with `VITE_API_BASE_URL=/api` to keep every browser request on the same HTTPS origin (a page served over HTTPS cannot call a plain-HTTP API).

`docker-compose.prod.yml` sets this up for a Let's Encrypt certificate. Set `DOMAIN` in `.env` and layer it on the base file:

```bash
echo "DOMAIN=<your-host>" >> .env
# Optional: make plain `docker compose ...` use both files on this host
echo "COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml" >> .env
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

The production file publishes only ports 80 and 443, both on nginx. Port 80 redirects to HTTPS and serves Let's Encrypt challenges from the host's `/var/www/certbot`. The API is reachable only through nginx at `https://<your-host>/api`, MCP only at `https://<your-host>/mcp`, and Postgres only from the other containers. In the AWS security group, open only 80 and 443 (plus SSH from your own IP); close 8001 if an older deployment opened it.

nginx sends HSTS, `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, and, on the web app, a `Content-Security-Policy` that only allows same-origin scripts, styles, and requests. The CSP also blocks the CDN assets of FastAPI's `/api/docs` page; use `/api/openapi.json` or a local backend for interactive docs.

Because nginx now holds port 80, certificates are renewed with the webroot method. Get the first certificate with `--standalone` before starting the stack, as before. Once the stack is running, switch the renewal method once and add a hook that reloads nginx:

```bash
sudo mkdir -p /var/www/certbot
sudo certbot certonly --webroot -w /var/www/certbot -d <your-host> --force-renewal
printf '#!/bin/sh\ncd %s && docker compose exec -T frontend nginx -s reload\n' "$PWD" \
  | sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
sudo certbot renew --dry-run
```

### Database backups

`docker-compose.prod.yml` adds a `backup` service. It runs `pg_dump` at startup and
then every `BACKUP_INTERVAL_SECONDS` (default 86400, one day). Dumps go to
`BACKUP_DIR` (default `./backups`), and dumps older than `BACKUP_RETENTION_DAYS`
(default 14) are deleted.

Local dumps are on the same disk as the database, so they do not survive losing the
EC2 instance or its EBS volume. Set `BACKUP_S3_URI` (for example
`s3://my-bucket/rental-mgmt`) to copy each dump to S3. The container uses the EC2
instance role, which needs `s3:PutObject` on that prefix. Containers can reach the
role only if the instance metadata hop limit is at least 2:

```bash
aws ec2 modify-instance-metadata-options --instance-id <id> --http-put-response-hop-limit 2 --http-tokens required
```

Also turn on EBS encryption, and add a Data Lifecycle Manager snapshot policy for
the volume. Use an S3 lifecycle rule to expire old dumps.

To restore a dump (this replaces the current data):

```bash
docker compose exec -T db pg_restore -U rental -d rental_mgmt --clean --if-exists < backups/rental_mgmt-<timestamp>.dump
docker compose restart api mcp
```

Test a restore before you rely on the backups.

### Compose configuration

`api` and `mcp` share their database and JWT settings through the `x-backend-env` anchor at the top of `docker-compose.yml`, so both services always use the same `DATABASE_URL` and `JWT_SECRET_KEY`. They must match, because the MCP server validates tokens issued by the API.

Set a real JWT secret before running, including locally. Compose reads it from your shell or from a `.env` file next to `docker-compose.yml`; its placeholder fallback is rejected by the application:

```bash
export JWT_SECRET_KEY="$(openssl rand -hex 32)"
docker compose up -d --build
```

Changing `JWT_SECRET_KEY` invalidates every token already issued, so clients must sign in again.

Startup and restart behavior:

- `db` has a `pg_isready` health check. `api` and `mcp` wait until Postgres is healthy before starting, which avoids connection errors on first boot.
- `api` has a `/health` check, and `mcp` waits for it so the two never create the database schema at the same time.
- Every service uses `restart: unless-stopped`, so containers come back after a crash or host reboot. Use `docker compose stop` or `docker compose down` to keep them stopped.

### MCP public URL

`RENTAL_MGMT_MCP_PUBLIC_BASE_URL` is the address MCP clients use to reach the MCP server. It is advertised in the OAuth metadata (`/.well-known/oauth-protected-resource/mcp` and the `WWW-Authenticate` header on 401 responses), so clients use it to discover how to authenticate. If unset, it defaults to `http://<RENTAL_MGMT_MCP_HOST>:<RENTAL_MGMT_MCP_PORT>`, which becomes the unreachable `http://0.0.0.0:8001` inside Docker.

The Compose default is `http://localhost:8001`, which works for local clients only. When deploying, set it to the externally reachable URL without the `/mcp` suffix, and point `RENTAL_MGMT_MCP_AUTH_SERVER_URL` at the public HTTPS API URL (`docker-compose.prod.yml` uses `https://<DOMAIN>` and `https://<DOMAIN>/api`). For example:

```yaml
services:
  mcp:
    environment:
      RENTAL_MGMT_MCP_PUBLIC_BASE_URL: https://mcp.example.com
      RENTAL_MGMT_MCP_AUTH_SERVER_URL: https://api.example.com
```

Pass the file with `-f` after `docker-compose.yml`, or list both in `COMPOSE_FILE`.

## Public Cloud Deployment

Build and deploy the Docker image to any container platform such as AWS ECS, Google Cloud Run, Azure Container Apps, Kubernetes, or a VM.

Required environment variables:

- `DATABASE_URL`: SQLAlchemy database URL, for example `postgresql+psycopg://user:password@host:5432/rental_mgmt`
- `JWT_SECRET_KEY`: long random secret for signing tokens
- `JWT_ALGORITHM`: defaults to `HS256`
- `ACCESS_TOKEN_EXPIRE_MINUTES`: defaults to `1440`
- `SUPER_ADMIN_INITIAL_PASSWORD`: initial admin password, at least 12 characters. If unset or weak, a random one is generated and printed once to the API log.
- `CORS_ORIGINS`: comma-separated browser origins allowed to call the API
- `OPEN_REGISTRATION`: defaults to `false`; landlords can sign up only with an admin invitation link and tenants cannot self-register. Set `true` only for local development.
- `TRUST_PROXY_HEADERS`: defaults to `false`; set `true` only when the API is reachable solely through nginx, so login lockout uses the real client IP

Frontend build environment variables:

- `VITE_API_BASE_URL`: public browser-accessible API URL, for example `https://api.example.com`

The app creates database tables at startup for quick deployment. For production, add migrations before making schema changes.

## Main API Flow

1. Register a landlord:

```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"owner@example.com","full_name":"Owner","password":"password123","role":"landlord"}'
```

2. Log in and copy the `access_token`:

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"owner@example.com","password":"password123"}'
```

3. Create a property:

```bash
curl -X POST http://localhost:8000/properties \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"Unit 1","address":"123 Main St","description":"Two bedroom apartment"}'
```

4. Create a tenant:

```bash
curl -X POST http://localhost:8000/tenants \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"full_name":"Tenant One","email":"tenant@example.com","phone":"+1-555-0100"}'
```

5. Create an active lease:

```bash
curl -X POST http://localhost:8000/leases \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"property_id":1,"tenants":[{"tenant_id":1,"monthly_rent":"1500.00","deposit":"1500.00"}],"start_date":"2026-10-01","end_date":"2027-09-30","monthly_rent":"1500.00","notes":"Annual lease"}'
```

6. Generate current bills:

```bash
curl -X POST http://localhost:8000/bills/generate-current-month \
  -H "Authorization: Bearer <token>"
```

7. Record a payment:

```bash
curl -X POST http://localhost:8000/bills/1/payments \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"amount":"500.00","paid_at":"2026-10-07T10:00:00Z","note":"Partial payment"}'
```

8. Check balances:

```bash
curl http://localhost:8000/bills \
  -H "Authorization: Bearer <token>"
```

## Endpoint Summary

- `POST /auth/register`
- `POST /auth/login`
- `GET /me`
- `GET /health`
- `POST /properties`
- `GET /properties`
- `GET /properties/{property_id}`
- `PATCH /properties/{property_id}`
- `DELETE /properties/{property_id}`
- `POST /properties/{property_id}/units`
- `GET /properties/{property_id}/units`
- `GET /units`
- `PATCH /units/{unit_id}`
- `DELETE /units/{unit_id}`
- `POST /tenants`
- `GET /tenants`
- `GET /tenants/{tenant_id}`
- `PATCH /tenants/{tenant_id}`
- `DELETE /tenants/{tenant_id}`
- `POST /leases`
- `GET /leases`
- `GET /leases/{lease_id}`
- `PATCH /leases/{lease_id}`
- `DELETE /leases/{lease_id}`
- `POST /leases/{lease_id}/generate-bills`
- `POST /bills/generate-current-month`
- `GET /bills`
- `GET /bills/{bill_id}`
- `DELETE /bills/{bill_id}`
- `POST /bills/{bill_id}/payments`
- `GET /payments`
- `GET /tenant/bills`
