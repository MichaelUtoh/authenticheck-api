# AuthentiCheck – Endpoint Reference

Quick-reference for frontend developers. Full request/response shapes are in `API_CONTRACTS.md`.

---

## Base URLs

| Environment | URL |
|---|---|
| **Local** | `http://localhost:8000/api/v1` |
| **Production** | `https://<host>/api/v1` |

Swagger UI (local): `http://localhost:8000/api/v1/docs/`
OpenAPI schema (local): `http://localhost:8000/api/v1/schema/`

---

## Required headers (all authenticated requests)

```
Authorization: Bearer <access_token>
X-Requested-With: XMLHttpRequest
Content-Type: application/json
```

For API-key auth replace the `Authorization` header with:

```
X-API-Key: ac_<token>
```

---

## Auth levels

| Symbol | Meaning |
|---|---|
| 🌐 | Public — no token needed |
| 🔑 | Requires `Authorization: Bearer <token>` |
| 🏢 | Requires Bearer token **and** active approved tenant membership (`tid` in JWT) |
| 👑 | Requires 🏢 + `owner` or `admin` role |
| 🛡️ | Requires `is_staff=True` + MFA verified |

---

## System

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health/live` | 🌐 | Process liveness check |
| `GET` | `/health/ready` | 🌐 | DB connectivity check |

---

## Auth

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/auth/register` | 🌐 | Create a new user account |
| `POST` | `/auth/login` | 🌐 | Login with email + password; returns `access_token` + sets refresh cookie |
| `POST` | `/auth/refresh` | 🌐 | Exchange refresh cookie for a new `access_token` |
| `POST` | `/auth/logout` | 🔑 | Revoke session and clear refresh cookie |
| `GET` | `/auth/me` | 🔑 | Current user profile, memberships, and active tenant |
| `POST` | `/auth/mfa/setup` | 🔑 | Begin TOTP enrolment — returns provisioning URI for QR code |
| `POST` | `/auth/mfa/confirm` | 🔑 | Confirm TOTP device with a 6-digit token |
| `POST` | `/auth/mfa/verify` | 🌐 | Step 2 of MFA login (called after login returns `mfa_required: true`) |

---

## API Keys

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api-keys` | 👑 | List API keys for the tenant (raw key not shown) |
| `POST` | `/api-keys` | 👑 | Create an API key — raw key returned **once** |
| `DELETE` | `/api-keys/{id}` | 👑 | Revoke an API key immediately |

---

## Tenants

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/tenants/register` | 🔑 | Register a new manufacturer tenant (creator becomes `owner`) |
| `GET` | `/tenants/me` | 🏢 | Active tenant details |
| `GET` | `/tenants/me/members` | 👑 | List active tenant members |
| `POST` | `/tenants/{id}/approve` | 🛡️ | Approve a pending tenant |
| `POST` | `/tenants/{id}/reject` | 🛡️ | Reject a pending tenant |
| `POST` | `/tenants/{id}/request-info` | 🛡️ | Move tenant to `info_requested` |
| `POST` | `/tenants/{id}/suspend` | 🛡️ | Suspend an approved tenant |

---

## Catalogue — Brands

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/catalogue/brands` | 🏢 | List active brands |
| `POST` | `/catalogue/brands` | 👑 | Create a brand |
| `GET` | `/catalogue/brands/{id}` | 🏢 | Get a brand |
| `PATCH` | `/catalogue/brands/{id}` | 👑 | Update a brand |
| `DELETE` | `/catalogue/brands/{id}` | 👑 | Soft-deactivate a brand |

---

## Catalogue — Products

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/catalogue/products` | 🏢 | List products |
| `POST` | `/catalogue/products` | 👑 | Create a product |
| `GET` | `/catalogue/products/{id}` | 🏢 | Get a product |
| `PATCH` | `/catalogue/products/{id}` | 👑 | Update a product |
| `GET` | `/catalogue/products/{product_id}/regulatory-refs` | 🏢 | List regulatory references for a product |
| `POST` | `/catalogue/products/{product_id}/regulatory-refs` | 👑 | Add a regulatory reference |

---

## Catalogue — Batches

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/catalogue/products/{product_id}/batches` | 🏢 | List batches for a product |
| `POST` | `/catalogue/products/{product_id}/batches` | 👑 | Create a batch |
| `GET` | `/catalogue/products/{product_id}/batches/{id}` | 🏢 | Get a batch |
| `PATCH` | `/catalogue/products/{product_id}/batches/{id}` | 👑 | Update a batch |
| `POST` | `/catalogue/products/{product_id}/batches/{id}/recall` | 👑 | Recall a batch (sets status + deactivates all codes) |

---

## Catalogue — Bulk Import

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/catalogue/import` | 👑 | Upload CSV to import brands, products, or batches (`multipart/form-data`) |
| `GET` | `/catalogue/import/{id}` | 👑 | Poll import job status |

---

## Issuance — Jobs

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/issuance/jobs` | 🏢 | List issuance jobs |
| `POST` | `/issuance/jobs` | 👑 | Submit a code-generation job ⚠️ requires `Idempotency-Key` header |
| `GET` | `/issuance/jobs/{id}` | 🏢 | Get job status and reconciliation |
| `GET` | `/issuance/jobs/{id}/export` | 🏢 | Download codes CSV (only when `status === 'done'`) |

---

## Issuance — Codes

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/issuance/codes` | 🏢 | List codes (max 500; filter by `batch_id`, `job_id`, `status`) |
| `POST` | `/issuance/codes/{code}/deactivate` | 👑 | Deactivate a single code by its code string |
| `POST` | `/issuance/batches/{batch_id}/deactivate` | 👑 | Bulk-deactivate all active codes for a batch |

---

## Verification

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/verify/{code}` | 🌐 | Resolve a product code — returns result, product info, batch info |

---

## Risk & Recall

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/reports` | 🌐 | Submit a consumer counterfeit report |
| `GET` | `/risk/flags` | 🏢 | List risk flags (filter by `kind`, `severity`, `is_reviewed`) |
| `POST` | `/risk/flags/{id}/review` | 👑 | Mark a risk flag as reviewed |
| `GET` | `/risk/cases` | 🏢 | List cases |
| `POST` | `/risk/cases` | 👑 | Open a new case |
| `GET` | `/risk/cases/{id}` | 🏢 | Get a case |
| `PATCH` | `/risk/cases/{id}` | 👑 | Update case status or notes |

---

## Analytics

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/analytics/scans` | 🏢 | Scan volume + outcome breakdown (query param: `days` 1–365, default 30) |
| `GET` | `/analytics/codes` | 🏢 | Code inventory snapshot (total, active, deactivated) |
| `GET` | `/analytics/risk` | 🏢 | Risk flag severity counts + open cases summary |

---

## Webhooks

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/webhooks` | 🏢 | List webhook endpoints |
| `POST` | `/webhooks` | 👑 | Register a webhook endpoint — secret returned **once** |
| `GET` | `/webhooks/{id}` | 🏢 | Get a webhook endpoint |
| `PATCH` | `/webhooks/{id}` | 👑 | Update URL, events, or description |
| `DELETE` | `/webhooks/{id}` | 👑 | Delete a webhook endpoint |
| `GET` | `/webhooks/{id}/deliveries` | 🏢 | List recent delivery attempts |

---

## Platform *(staff only)*

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/platform/tenants` | 🛡️ | List all tenants (filter by `status`; paginated) |
| `GET` | `/platform/tenants/{id}` | 🛡️ | Tenant detail with usage stats |
| `GET` | `/platform/flags` | 🛡️ | Unreviewed risk flags across all tenants |
| `GET` | `/platform/audit` | 🛡️ | Paginated audit log (filter by `action`, `tenant_id`) |

---

## Integrations

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/01/{gtin}/21/{serial}` | 🌐 | GS1 Digital Link — redirects (`302`) to `/verify/{serial}` |
| `POST` | `/integrations/africastalking/ussd` | 🌐 | Africa's Talking USSD inbound (validated by AT API key) |

---

## Notes for local development

1. **Swagger UI** at `http://localhost:8000/api/v1/docs/` — click **Authorize**, paste the
   `access_token` from a login response (no `Bearer` prefix needed in the Swagger field).

2. **Getting a token locally**:
   ```bash
   curl -s -X POST http://localhost:8000/api/v1/auth/login \
     -H "Content-Type: application/json" \
     -H "X-Requested-With: XMLHttpRequest" \
     -d '{"email":"you@example.com","password":"yourpassword"}' \
     | jq .access_token
   ```

3. **Idempotency-Key** — any unique string works locally:
   ```
   Idempotency-Key: test-001
   ```
   Use `crypto.randomUUID()` in production frontend code.

4. **Triggering Celery tasks manually** (no worker running locally):
   ```python
   # Django shell
   from apps.issuance.tasks import generate_codes
   from apps.issuance.models import IssuanceJob
   job = IssuanceJob.unscoped.order_by('-created_at').first()
   generate_codes(str(job.pk))
   ```

5. **CORS** — ensure `CORS_ALLOWED_ORIGINS` in local settings includes your Next.js dev URL
   (typically `http://localhost:3000`).
