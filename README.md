# AuthentiCheck – Backend API

Manufacturer-facing product verification and traceability service (Nigeria-first, cosmetics and pharmaceuticals).
Decoupled architecture: this repo is the **Django + DRF API**. The Next.js frontend lives in a separate repo.

> A registered code or a successful scan never proves a physical item is genuine. API copy must never claim certainty.

## Stack

Python 3.12 · Django 5.1 · Django REST Framework · PostgreSQL 16 · Redis 7 · Celery · drf-spectacular (OpenAPI) · pytest

## First-time setup

```bash
cp .env.example .env                # then set DJANGO_SECRET_KEY
docker compose up -d db redis       # Postgres + Redis

python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements/dev.txt

# Phase 0 ships WITHOUT migrations: generate them once from the models, then commit them.
python manage.py makemigrations accounts tenants audit core
python manage.py migrate
python manage.py createsuperuser

pytest                              # run the test suite
python manage.py runserver          # http://localhost:8000/api/v1/docs/
```

Or run everything in containers: `docker compose up --build` (see `Makefile` for shortcuts).

## Layout

```
config/            settings (base/local/test/production), urls, celery, wsgi/asgi
apps/core/         base models, tenancy, idempotency, error envelope, request IDs, health checks
apps/accounts/     custom User (email login, UUID pk)
apps/tenants/      Tenant (the isolation boundary)
apps/audit/        append-only AuditEvent + audit service
apps/testsupport/  test-only model used to exercise tenant scoping (loaded in test settings only)
docs/              architecture notes, decisions, roadmap
```

## Conventions

- **Thin views, fat services.** Business logic lives in `services.py`; read queries in `selectors.py`.
- **Tenant isolation is structural.** Tenant-owned models inherit `TenantScopedModel`; the default manager
  fails closed without a tenant context. Escape hatch is `Model.unscoped`, used only by platform admin and jobs.
- **Every sensitive mutation writes an audit event in the same transaction** (`audit.services.record`).
- **Creation/job endpoints require an `Idempotency-Key` header** (`core.idempotency.run_idempotent`).
- **Errors share one envelope:** `{"error": {"code", "message", "details?", "request_id"}}`. No stack traces.
- **Timestamps are UTC**, server-generated. Display timezone is a frontend concern.
- Public identifiers are UUIDs or opaque codes, never sequential IDs.

See `docs/ARCHITECTURE.md` for decisions and the phase roadmap.
