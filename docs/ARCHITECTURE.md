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

## Open decisions (to settle in Phase 1)

1. **Client auth for Next.js:** session cookie (HttpOnly, SameSite, CSRF) on a shared parent domain, versus JWT in
   HttpOnly cookies. Recommendation: session cookies if the frontend and API share a registrable domain.
2. **Active-tenant selection:** `X-Tenant-ID` header validated against the user's membership on every request.
3. **MFA:** TOTP via django-otp or a compatible library; mandatory for platform admins.

## Phase 0 deliverables (this commit)

- Project skeleton, per-environment settings, Docker/Compose, Makefile, CI, pre-commit
- Custom user, minimal tenant, tenant-scoping framework
- Audit service (append-only), idempotency utility, error envelope, request-ID logging
- Health endpoints, OpenAPI docs, test suite for all of the above

## Hardening deferred to Phase 6

- DB-level audit protection (trigger denying UPDATE/DELETE) plus a hash chain for tamper evidence (EC-055)
- Postgres row-level security as defence in depth for tenant isolation
- Backup/restore drills, monitoring and alerting

## Phase roadmap

- [x] Phase 0 – Foundation
- [ ] Phase 1 – Identity and tenants (auth, MFA, onboarding, memberships, roles, tenant-context permission)
- [ ] Phase 2 – Catalogue (brands, products, regulatory refs, batches, bulk import)
- [ ] Phase 3 – Issuance and export (jobs, unique codes, reconciliation, deactivation)
- [ ] Phase 4 – Public verification (resolver, scan events, rate limits)
- [ ] Phase 5 – Risk, cases, recall/deactivation, consumer reports
- [ ] Phase 6 – Analytics, platform dashboard, hardening
- [ ] Phase 7 – Pharma readiness (gated), SMS/USSD, integrations
