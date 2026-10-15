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

For bills repeating every six months, select **Repeated bill every six months** (`semi_annually` in the API). This uses the same period and due-day rules, generating a bill in the period's first month and every sixth month afterward.

### Tenant Charges

On a property bill, **Generate Tenant Bill** opens a read-only preview of the eligible tenants, their equal shares, and the tenant charge due date: the last day of the original bill's due-date month (for example, November 1 becomes November 30). Tenants whose leases overlap the original bill's stored period are included once each; bills without a period use the original due date for eligibility. Rounding remainders are distributed one cent at a time so shares always add up to the original amount.

**Confirm & Generate** creates one bill per tenant. **Cancel** creates nothing. Generated bills show each tenant's name, the month-end due date, and a **Tenant charge** label, separate from monthly rent. Existing bills and regular rent due dates are not changed. The source property bill can only be generated once; deleting individual charges does not reset it.

API clients must first call `GET /property-bills/{id}/tenant-bills-preview`, then send its `preview_token` in the JSON body of `POST /property-bills/{id}/generate-tenant-bills`. Changes to the bill or allocation invalidate the preview and return HTTP 409; fetch a fresh preview and confirm again. Bill responses include `tenant_name`, `is_tenant_charge`, and `source_property_bill_id`.

## Run Backend Locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Open the API docs at [http://localhost:8000/docs](http://localhost:8000/docs).

By default, local data is stored in `data/rental_mgmt.db` under the project root. The application resolves relative SQLite paths against the project root, so restarting `uvicorn` from another directory still uses the same local database file. On startup, the backend prints the resolved database URL as `Rental Management database: ...`.

To confirm which database file the app is using and how many records it contains:

```bash
python scripts/db_status.py
```

## Run MCP Server For AI Agents

The project includes a read-only MCP server that lets AI agents query rental-management data from the same database used by the FastAPI backend.

Run it as a local stdio server when the MCP client runs on the same host:

```bash
python -m app.mcp_server
```

Example local MCP client configuration:

```json
{
  "mcpServers": {
    "rental-management": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/Users/bytedance/Projects/rental-mgmt"
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

Remote MCP authentication is enabled by default. The MCP server validates OAuth bearer JWTs issued by the FastAPI app, so `JWT_SECRET_KEY` and `JWT_ALGORITHM` must match between the API and MCP server.

Get a landlord access token:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/oauth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=password&username=owner@example.com&password=password123" \
  | python -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
```

Register the remote MCP server in Claude Code with the bearer token:

```bash
claude mcp add --transport http rental-management http://localhost:8001/mcp \
  --header "Authorization: Bearer $TOKEN"
```

Treat any bearer token like a password. If it is leaked while valid, anyone who can reach the MCP endpoint can access rental data. Tokens expire according to `ACCESS_TOKEN_EXPIRE_MINUTES`; rotate `JWT_SECRET_KEY` to revoke all issued JWTs.

To serve the remote endpoint over HTTPS, point the server at a certificate and private key (both are required; this applies to `streamable-http` and `sse`):

```bash
RENTAL_MGMT_MCP_TRANSPORT=streamable-http \
RENTAL_MGMT_MCP_HOST=0.0.0.0 \
RENTAL_MGMT_MCP_PORT=8001 \
RENTAL_MGMT_MCP_SSL_CERTFILE=/path/to/fullchain.pem \
RENTAL_MGMT_MCP_SSL_KEYFILE=/path/to/privkey.pem \
python -m app.mcp_server
```

The endpoint is then `https://<your-host>:8001/mcp`. In Docker Compose, mount the certificate directory into the `mcp` container and set the same two variables.

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

- `database_status`: show the resolved database URL.
- `list_landlords`: list landlord IDs and emails for filtering.
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

The frontend's nginx serves HTTPS when both `NGINX_SSL_CERTFILE` and `NGINX_SSL_KEYFILE` are set; port 80 then redirects to HTTPS on 443. nginx also proxies `/api/` to the `api` service, so build the frontend with `VITE_API_BASE_URL=/api` to keep every browser request on the same HTTPS origin (a page served over HTTPS cannot call a plain-HTTP API). For example, in a `docker-compose.override.yml`:

```yaml
services:
  frontend:
    build:
      args:
        VITE_API_BASE_URL: /api
    ports:
      - "443:443"
    volumes:
      - /etc/letsencrypt:/etc/letsencrypt:ro
    environment:
      NGINX_SSL_CERTFILE: /etc/letsencrypt/live/<your-host>/fullchain.pem
      NGINX_SSL_KEYFILE: /etc/letsencrypt/live/<your-host>/privkey.pem
```

nginx reads the certificate at startup, so after a renewal run `docker compose restart frontend mcp`.

### Compose configuration

`api` and `mcp` share their database and JWT settings through the `x-backend-env` anchor at the top of `docker-compose.yml`, so both services always use the same `DATABASE_URL` and `JWT_SECRET_KEY`. They must match, because the MCP server validates tokens issued by the API.

Set a real JWT secret before running anywhere other than your laptop. Compose reads it from your shell or from a `.env` file next to `docker-compose.yml`; otherwise it falls back to an insecure placeholder:

```bash
echo "JWT_SECRET_KEY=$(openssl rand -hex 32)" > .env
docker compose up -d --build
```

Changing `JWT_SECRET_KEY` invalidates every token already issued, so clients must sign in again.

Startup and restart behavior:

- `db` has a `pg_isready` health check. `api` and `mcp` wait until Postgres is healthy before starting, which avoids connection errors on first boot.
- Every service uses `restart: unless-stopped`, so containers come back after a crash or host reboot. Use `docker compose stop` or `docker compose down` to keep them stopped.

### MCP public URL

`RENTAL_MGMT_MCP_PUBLIC_BASE_URL` is the address MCP clients use to reach the MCP server. It is advertised in the OAuth metadata (`/.well-known/oauth-protected-resource/mcp` and the `WWW-Authenticate` header on 401 responses), so clients use it to discover how to authenticate. If unset, it defaults to `http://<RENTAL_MGMT_MCP_HOST>:<RENTAL_MGMT_MCP_PORT>`, which becomes the unreachable `http://0.0.0.0:8001` inside Docker.

The Compose default is `http://localhost:8001`, which works for local clients only. When deploying, set it to the externally reachable URL without the `/mcp` suffix, and point `RENTAL_MGMT_MCP_AUTH_SERVER_URL` at the public API URL. For example, in a `docker-compose.override.yml`:

```yaml
services:
  mcp:
    environment:
      RENTAL_MGMT_MCP_PUBLIC_BASE_URL: https://mcp.example.com
      RENTAL_MGMT_MCP_AUTH_SERVER_URL: https://api.example.com
```

Docker Compose merges `docker-compose.override.yml` automatically on `docker compose up`.

## Public Cloud Deployment

Build and deploy the Docker image to any container platform such as AWS ECS, Google Cloud Run, Azure Container Apps, Kubernetes, or a VM.

Required environment variables:

- `DATABASE_URL`: SQLAlchemy database URL, for example `postgresql+psycopg://user:password@host:5432/rental_mgmt`
- `JWT_SECRET_KEY`: long random secret for signing tokens
- `JWT_ALGORITHM`: defaults to `HS256`
- `ACCESS_TOKEN_EXPIRE_MINUTES`: defaults to `1440`
- `CORS_ORIGINS`: comma-separated browser origins allowed to call the API

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
