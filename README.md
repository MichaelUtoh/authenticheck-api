# AuthentiCheck API

**Product verification and traceability for manufacturers. Nigeria-first, built for cosmetics and pharmaceuticals.**

AuthentiCheck lets a manufacturer give every unit it ships a unique code, and lets anyone (a shopper, a pharmacist, a
regulator) check that code in seconds. This repository is the backend: a Django REST API that serves the manufacturer
dashboard, the public checker, and integrations. The Next.js frontend lives in a separate repository.

---

## Mission and vision

**Mission.** Make counterfeits easier to spot and genuine products easier to trust, by giving manufacturers a simple way
to protect their products and giving consumers a simple way to check them.

**Vision.** A market where a person can verify what they are about to buy or take with one scan or one short code, and where
manufacturers and regulators see counterfeiting signals early enough to act on them.

**Principle: never claim certainty.** A registered code, or a successful check, does not prove that a physical item is
genuine. Codes can be copied. The API reports what it knows (registered, checked before, recalled, expired, not
recognised) and leaves the conclusion to the person. Copy returned or shown to consumers must never overstate this.

---

## How it works

```
 Manufacturer                                                         Consumer / regulator
 ────────────                                                         ────────────────────
 1. Register company  ──▶  Platform staff approve it
 2. Add products, batches, regulatory references
 3. Request N codes for a batch ──▶ background job generates unique codes
 4. Export codes (CSV) and print them on packs          ──────────▶   5. Scan or type the code
                                                                          GET /api/v1/verify/{code}
                                                                      6. Result: genuine · checked before ·
                                                                          expired · recalled · deactivated ·
                                                                          not recognised
 7. Dashboard shows scans, repeat checks, risk flags, consumer reports,
    cases to investigate, and can recall a batch or deactivate codes
```

**Codes.** Each code is 12 characters from a 30-symbol alphabet with no look-alikes (`0 O 1 I L U` are excluded), made of
11 random characters and one check character (Luhn mod-30), so most typos are rejected before a lookup is recorded.
Codes are shown and printed as `XXXX-XXXX-XXXX`. Lookup ignores case, spaces and dashes.

**Verification results.** The public endpoint returns one of: `genuine`, `expired`, `recalled`, `deactivated`,
`not_found`, plus whether this is the first check of that code. A repeat is described as "checked before", never as
"fake", because people legitimately scan twice. No personal data about who scanned is ever returned.

**Risk and response.** Scans, repeat checks and consumer reports feed automated risk flags. Staff open cases, review them,
and can recall a batch, which deactivates every code in it. Manufacturers can receive `code.verified` webhooks.

**Multi-tenancy.** A tenant is a manufacturer. Every tenant-owned record is scoped to its tenant, and the default query
manager fails closed when no tenant is active, so one company can never read another's data.

### Capabilities

| Area | What it covers |
|---|---|
| Identity | Email login, JWT access tokens, rotating HttpOnly refresh cookie, TOTP MFA (mandatory for platform staff), invitations, roles |
| Tenants | Company registration, staff approval workflow, memberships and roles, company settings and support contacts |
| Catalogue | Brands, products with images, regulatory references, batches, CSV bulk import |
| Issuance | Async code generation jobs, reconciliation, CSV export, per-code and bulk deactivation, pharma-gated issuance |
| Verification | Public resolver, scan recording, rate limiting, GS1 Digital Link redirect, Africa's Talking USSD lookup |
| Risk | Risk flags, case management, batch recall, public consumer reports |
| Analytics | Scan and code analytics for manufacturers, platform-wide dashboard for staff |
| Integrations | API keys, signed webhooks |

---

## Technology

| Layer | Choice |
|---|---|
| Language and framework | Python 3.12, Django 5.1, Django REST Framework |
| Database | PostgreSQL 16 |
| Cache and broker | Redis 7 (throttling, caching, Celery broker) |
| Background jobs | Celery 5.4 with `acks_late` and idempotent tasks; Celery beat for scheduled cleanup |
| Authentication | PyJWT access tokens, Argon2 password hashing, django-otp (TOTP) |
| API docs | OpenAPI 3 via drf-spectacular (Swagger UI) |
| Storage | Local disk in development; S3 or S3-compatible (Cloudflare R2, MinIO) behind a CDN in production |
| Serving | Gunicorn, WhiteNoise |
| Logging | Structured JSON logs with a request ID on every line |
| Quality | pytest, pytest-django, factory-boy, ruff, pre-commit |
| Packaging | Docker and Docker Compose |

### Design decisions

- **Thin views, fat services.** Business logic lives in `services.py`, read queries in `selectors.py`.
- **Tenant isolation is structural.** Tenant-owned models inherit `TenantScopedModel`. The escape hatch,
  `Model.unscoped`, is used only by platform staff and background jobs.
- **Every sensitive change writes an audit event in the same transaction** (`ATOMIC_REQUESTS = True`), so a failed audit
  write rolls back the change.
- **Idempotency.** Creation and job endpoints require an `Idempotency-Key` header, claimed before the handler runs.
- **One error envelope:** `{"error": {"code", "message", "details?", "request_id"}}`. No stack traces leak.
- **No Celery result backend.** Job state lives in our own tables, so it is queryable and resumable.
- **UTC everywhere.** Timestamps are server-generated; display time zones are a frontend concern.
- **Opaque public identifiers.** UUIDs or codes, never sequential IDs.

More detail is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Project layout

```
config/              settings (base / local / test / production), URLs, Celery, WSGI/ASGI
apps/core/           base models, tenancy, idempotency, error envelope, request IDs, health checks
apps/accounts/       custom User (email login, UUID primary key)
apps/identity/       login, sessions, refresh, MFA, invitations, API keys
apps/tenants/        tenants, memberships, onboarding and approval
apps/catalogue/      brands, products, batches, regulatory references, bulk import
apps/issuance/       code generation jobs, export, deactivation
apps/verification/   public verify endpoint, scan events, GS1 redirect, USSD
apps/risk/           risk flags, cases, recalls, consumer reports
apps/analytics/      manufacturer analytics
apps/platform/       platform staff dashboard
apps/webhooks/       webhook endpoints and delivery
apps/integrations/   third-party integrations
apps/audit/          append-only audit events
docs/                architecture, endpoint reference, API contracts and frontend requirements
```

---

## Getting started

### Prerequisites

- Docker and Docker Compose, or
- Python 3.12, PostgreSQL 16 and Redis 7 for a local setup

### 1. Configure the environment

```bash
cp .env.example .env
```

Set these in `.env`:

| Variable | Purpose |
|---|---|
| `DJANGO_SECRET_KEY` | Required. A long random string. |
| `JWT_SIGNING_KEY` | Required. A separate long random string used to sign access tokens. |
| `DATABASE_URL`, `REDIS_URL` | Connection strings (the Compose file overrides them for containers). |
| `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS`, `FRONTEND_URL` | The frontend origin, `http://localhost:3000` by default. |

Generate a key with `python3 -c "import secrets; print(secrets.token_urlsafe(64))"`. Image storage, email delivery and
other optional settings are documented inline in [.env.example](.env.example).

### 2a. Run with Docker (recommended)

```bash
docker compose up --build
docker compose exec api python manage.py migrate          # first run, and after pulling new migrations
docker compose exec api python manage.py createsuperuser  # platform staff account
```

This starts the API, Celery worker, Celery beat, PostgreSQL and Redis. The API listens on <http://localhost:8000>.

> The Compose `api` service does not run migrations on start. Run `migrate` yourself whenever the schema changes, or you
> will get errors such as `column ... does not exist`.

### 2b. Run locally without containers for the API

```bash
docker compose up -d db redis            # Postgres and Redis only

python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements/dev.txt

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver               # http://localhost:8000
celery -A config worker -l info          # in a second terminal, needed for code generation
```

### 3. Explore the API

| URL | What |
|---|---|
| <http://localhost:8000/api/v1/docs/> | Swagger UI (when `API_DOCS_ENABLED=true`) |
| <http://localhost:8000/api/v1/schema/> | OpenAPI schema |
| <http://localhost:8000/api/v1/health/ready> | Readiness check (database and Redis) |
| <http://localhost:8000/admin/> | Django admin, for platform staff only |

In development, emails (for example invitations) are printed to the `api` container logs:
`docker compose logs -f api`.

---

## Testing and code quality

```bash
pytest                    # full test suite
pytest apps/identity -q   # one app
ruff check .              # lint
ruff check --fix . && ruff format .
```

Cross-tenant isolation tests gate releases. A `Makefile` provides shortcuts (`make test`, `make lint`, `make migrate`).

---

## Security notes

- Login, refresh, registration and the public verify endpoint are rate limited. In local development the counters live in
  Redis, so repeated failed logins can lock you out for up to 15 minutes.
- The access token is held in memory by the frontend. The refresh token is an HttpOnly, `SameSite=Strict` cookie scoped to
  `/api/v1/auth/refresh`, and state-changing requests require an `X-Requested-With` header.
- `/api/v1/verify/*` responses are sent with `Cache-Control: no-store`.
- Keep the media bucket private and serve it through a CDN. Never commit `.env`.

---

## Documentation

| Document | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Decisions and the phase roadmap |
| [docs/ENDPOINT_REFERENCE.md](docs/ENDPOINT_REFERENCE.md) | Endpoint reference |
| [docs/API_CONTRACTS.md](docs/API_CONTRACTS.md) | Request and response contracts |
| [docs/BACKEND_API_REQUIREMENTS.md](docs/BACKEND_API_REQUIREMENTS.md) | Living list of what the frontend needs from the backend |
| [docs/PRODUCTS_API_REQUIREMENTS.md](docs/PRODUCTS_API_REQUIREMENTS.md), [docs/BATCHES_CODES_API_REQUIREMENTS.md](docs/BATCHES_CODES_API_REQUIREMENTS.md) | Detailed area specs |
