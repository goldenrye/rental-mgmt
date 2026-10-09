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

Bill generation is current-month only. When a lease starts or ends mid-month, the bill is prorated by active lease days in that month. The backend also runs an in-process scheduler that automatically generates the current month bill on the 1st day of each month.

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
