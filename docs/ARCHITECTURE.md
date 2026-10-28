# AuthentiCheck – Backend architecture notes

## Decisions

| # | Decision | Why |
|---|----------|-----|
| 1 | Django 5.1 + DRF (not FastAPI) | Workflow-heavy relational CRUD (tenants, roles, approvals, audit, bulk jobs). ORM, migrations, auth, permissions and admin come built in. The p95 ≤ 2s verify target is achievable without async. |
| 2 | Custom `User` from day one (email login, UUID pk) | Swapping the user model after the first migration is painful. |
| 3 | Tenant = manufacturer; isolation enforced in the model layer | `TenantScopedModel.objects` fails closed without a tenant context; `unscoped` is explicit. Cross-tenant tests gate releases. |
| 4 | `ATOMIC_REQUESTS = True` | A request is one transaction, so a failed audit write rolls back the mutation (EC-084). |
| 5 | Idempotency keys claimed before the handler runs | Concurrent duplicates cannot both execute (EC-010/013). |
| 6 | Single JSON error envelope with request IDs | Predictable for the Next.js client; no stack traces leak (EC-027). |
| 7 | No Celery result backend | Job state is persisted in our own tables, so it is queryable, auditable and resumable. |
| 8 | `acks_late` + idempotent tasks | A worker crash redelivers the task; resumable issuance relies on this (EC-012). |
| 9 | Admin URL configurable; Django admin = platform staff only | Tenant users never use Django admin; they use the API. |

## Resolved decisions

1. **Client auth for Next.js:** JWT Bearer tokens. Access token returned in login response body;
   refresh token in an HttpOnly `SameSite=Strict` cookie scoped to `/api/v1/auth/refresh`.
   The Next.js frontend stores the access token in memory (not localStorage) and refreshes
   silently on 401. CSRF protection is enforced via `X-Requested-With` on state-changing requests.
2. **Active-tenant selection:** `tid` claim embedded in the JWT at login. The frontend holds the
   selected tenant and re-authenticates (new login / token exchange) when switching tenants.
3. **MFA:** TOTP via django-otp. Mandatory for platform staff (`is_staff=True`).

## Phase deliverables

### Phase 0 – Foundation
- Project skeleton, per-environment settings, Docker/Compose, Makefile, CI, pre-commit
- Custom user, minimal tenant, tenant-scoping framework
- Audit service (append-only), idempotency utility, error envelope, request-ID logging
- Health endpoints, OpenAPI docs, test suite

### Phase 1 – Identity & Tenants
- JWT sessions (access + refresh tokens, HttpOnly cookie rotation)
- TOTP MFA enrolment and challenge flow
- Tenant registration, staff approval workflow, membership roles
- Tenant-context permission enforced on every scoped request

### Phase 2 – Catalogue
- Brands, products, regulatory references, production batches
- CSV bulk import with async job tracking

### Phase 3 – Issuance & Export
- Async code-generation jobs via Celery (`acks_late`, idempotent)
- Unique 128-bit codes, reconciliation, per-code and bulk deactivation
- CSV export of completed jobs

### Phase 4 – Public Verification
- `GET /verify/{code}` — public resolver, scan event recording, rate limiting
- GS1 Digital Link redirect (`/01/{gtin}/21/{serial}`)
- Webhook delivery on `code.verified`

### Phase 5 – Risk, Cases & Recall
- Automated risk flag generation (repeated scan, consumer report, recalled scan)
- Case management (open → under_review → closed)
- Batch recall with bulk code deactivation
- Consumer counterfeit report endpoint (public)

### Phase 6 – Analytics, Platform Dashboard & Hardening
- Scan, code, and risk analytics endpoints
- Platform staff dashboard (tenant list, cross-tenant flags, audit log)
- Audit hash chain for tamper evidence
- API key authentication (`X-API-Key: ac_<token>`)
- Swagger auth (bearerAuth + apiKeyAuth OpenAPI extensions)

### Phase 7 – Integrations & Pharma Readiness
- Webhooks (endpoint management, HMAC-signed deliveries, delivery log)
- Africa's Talking USSD inbound handler
- Pharma-gated batch restrictions (expiry enforcement)

## Phase roadmap

- [x] Phase 0 – Foundation
- [x] Phase 1 – Identity and tenants
- [x] Phase 2 – Catalogue
- [x] Phase 3 – Issuance and export
- [x] Phase 4 – Public verification
- [x] Phase 5 – Risk, cases, recall
- [x] Phase 6 – Analytics, platform dashboard, hardening
- [x] Phase 7 – Pharma readiness, SMS/USSD, integrations
