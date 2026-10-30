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
| ✏️ | Requires 🏢 + `owner`, `admin` or `editor` role (catalogue drafts and batch entry) |
| 📤 | Requires 🏢 + `owner` or `admin`, or an `editor` with `can_submit_products` enabled |
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
| `GET` | `/tenants/me/members` | 👑 | List active tenant members (includes `role`, `can_submit_products`) |
| `PATCH` | `/tenants/me/members/{id}` | 👑 | Enable `can_submit_products` for an editor. Changing `role` (`admin`, `editor`, `member`, `viewer`) is **owner only**; the owner's own role cannot change |
| `GET` | `/tenants/me/support-contact` | 🏢 | Default consumer support contact (`support_email`, `support_phone`, `support_url`) |
| `PATCH` | `/tenants/me/support-contact` | 👑 | Update the support contact (audited). Shown on `/verify` unless a product overrides it |
| `GET` | `/tenants/me/release-gates` | 🏢 | Industry release-gate state (cosmetics `live`, pharmaceuticals `locked`) |
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

Lifecycle: `draft → in_review → approved | changes_requested | rejected`. Products are approved by
**AuthentiCheck platform staff** (see [Platform](#platform-staff-only)), never by the tenant.
`is_active=false` archives a product. A product is publicly verifiable once approved and stays so
(with its last approved values) while a revision is in review, unless staff suspend it.

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/catalogue/products` | 🏢 | List products (paginated). Filters: `q`, `status` (repeatable), `brand_id`, `industry`, `is_active` (default `true`), `ordering` (`-updated_at` default, `name`, `-name`, `sku`, `-sku`, `-created_at`), `page`, `page_size` |
| `POST` | `/catalogue/products` | ✏️ | Create a product. Always starts as `draft`; partial data allowed. Pass `confirm_not_duplicate: true` to override `possible_duplicate` |
| `GET` | `/catalogue/products/{id}` | 🏢 | Detail incl. `batch_count`, `active_batch_count`, `codes_issued`, `gtin_conflict`, `pending_revision` |
| `PATCH` | `/catalogue/products/{id}` | ✏️ | Edit. `status` cannot be set here. See edit rules below |
| `POST` | `/catalogue/products/{id}/submit` | 📤 | `draft`/`rejected`/`changes_requested` → `in_review`. Validates the industry's required fields; stores a full field snapshot in the audit log |
| `POST` | `/catalogue/products/{id}/withdraw` | submitter or 👑 | `in_review` → `draft`. Body: `{ "reason": "..." }` (required). Only before a reviewer decision |
| `POST` | `/catalogue/products/{id}/correction-request` | ✏️ | Ask for a controlled correction of a locked field. Body: `{ "field": "sku"\|"gtin", "requested_value": "...", "reason": "..." }` |
| `GET` | `/catalogue/products/{id}/activity` | 🏢 | Per-product audit history (last 100 events, with `before` / `after`) |
| `GET` | `/catalogue/products/{id}/consumer-preview` | 🏢 | Exactly what `/verify` would show (last approved values) |
| `GET` | `/catalogue/industries` | 🏢 | Industries, required fields and active categories (with `allowed_units`). Platform-managed; do not hard-code |
| `POST` | `/catalogue/images` | ✏️ | Upload a reference image (`multipart/form-data`, field `file`). PNG/JPEG by content, max 5 MB, 400–4000 px per side. Returns `id`, `url` (display image, longest side ≤ 1600 px) and `thumbnail_url` (≤ 320 px). EXIF/GPS stripped, orientation applied; PNGs with transparency stay PNG, everything else becomes JPEG. Unattached uploads are deleted after 24 h |
| `DELETE` | `/catalogue/images/{id}` | ✏️ | Delete an unused image and its files. `409 image_in_use` while a product or pending revision references it; replace it on the product instead |
| `GET` | `/catalogue/products/{product_id}/regulatory-refs` | 🏢 | List regulatory references (includes `source`, `verification_status`) |
| `POST` | `/catalogue/products/{product_id}/regulatory-refs` | ✏️ | Add a regulatory reference. Verification is staff-only |

**Key product fields:** `name`, `brand_id`, `industry` (`cosmetics`; `pharmaceuticals` is locked),
`variant`, `size_value` + `size_unit` (`ml`, `L`, `g`, `kg`, `mg`, `units`), `size` (read-only, generated,
e.g. `"400 ml"`), `pack_configuration`, `dosage_form`, `strength`, `category` (slug from
`/catalogue/industries`), `sku`, `gtin` (optional), `consumer_visibility`, `support_contact`
(`{email, phone, url}` override), `reference_image_id` (read-back also includes `reference_image` with `url` / `thumbnail_url`), `regulatory_reference` (nested, write-only),
`confirm_not_duplicate` (write-only). Read-only: `status`, `approved_version`, `pending_revision`,
`public_suspended`, `review_note`, `correction_request`, `gtin_conflict` (detail only).

**Edit rules**

| Product state | Behaviour |
|---|---|
| `in_review` | Locked: `409 product_not_editable`. Withdraw first |
| Never approved | Any field editable, except `industry` once set (`409 field_locked`) |
| Approved | `industry`, `sku`, `gtin` locked (`409 field_locked`, `details.field` names it). Consumer-visible fields (name, brand, variant, size, category, image, visibility, support contact, pharma fields) are stored in `pending_revision`, the status returns to `in_review`, and the live values are unchanged until a reviewer approves. Internal fields (e.g. `description`) save immediately without re-review |

**Errors:** `409 sku_conflict` (SKU unique per tenant, case-insensitive, archived included),
`409 possible_duplicate` (same brand + name + variant + size), both with
`details.existing_product`. `403 industry_locked` for pharmaceuticals. `400 validation_error` with
per-field `details`.

---

## Catalogue — Batches

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/catalogue/products/{product_id}/batches` | 🏢 | List batches for a product |
| `POST` | `/catalogue/products/{product_id}/batches` | ✏️ | Create a batch (`product_id` also required in the body) |
| `GET` | `/catalogue/products/{product_id}/batches/{id}` | 🏢 | Get a batch |
| `PATCH` | `/catalogue/products/{product_id}/batches/{id}` | ✏️ | Update a batch |
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
| `GET` | `/issuance/codes` | 🏢 | List codes, paginated (`count/next/previous/results`; `page`, `page_size` max 100). Filter by `batch_id`, `job_id`, `status` |
| `POST` | `/issuance/codes/{code}/deactivate` | 👑 | Deactivate a single code by its code string |
| `POST` | `/issuance/batches/{batch_id}/deactivate` | 👑 | Bulk-deactivate all active codes for a batch |

---

## Verification

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/verify/{code}` | 🌐 | Resolve a product code — returns result, product info, batch info, and `support_contact` (product override, else tenant default) |

Hidden fields (per the product's `consumer_visibility` toggles) are **omitted**, not null. Products that
are not yet approved, or are suspended by staff, return `404 not_found`.

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
| `GET` | `/platform/products` | 🛡️ | Product review queue, paginated, oldest submission first (filter `status`, default `in_review`; `tenant_id`) |
| `POST` | `/platform/products/{id}/approve` | 🛡️ | Approve; applies any pending revision as a new version. `409 gtin_conflict` if the GTIN is registered elsewhere, unless body has `gtin_conflict_resolution`. Pharmaceuticals stay blocked (`403 industry_locked`) |
| `POST` | `/platform/products/{id}/request-changes` | 🛡️ | Send back for edits. Body: `{ "review_note": "..." }` (required) |
| `POST` | `/platform/products/{id}/reject` | 🛡️ | Reject. Body: `{ "review_note": "..." }` (required) |
| `POST` | `/platform/products/{id}/suspend` | 🛡️ | Suspend public verification. Body: `{ "reason": "..." }` |
| `POST` | `/platform/products/{id}/unsuspend` | 🛡️ | Restore public verification. Body: `{ "reason": "..." }` |
| `POST` | `/platform/products/{id}/correct` | 🛡️ | Controlled correction of a locked `sku` / `gtin`. Body includes `reason`; re-runs uniqueness checks and audits old/new values |
| `POST` | `/platform/products/{product_id}/regulatory-refs/{ref_id}/verify` | 🛡️ | Record a regulatory-reference verification (`method` required, `note` optional) |
| `GET` `POST` | `/platform/industries` | 🛡️ | List / create industries |
| `PATCH` | `/platform/industries/{id}` | 🛡️ | Update an industry (`available`, `required_fields`, `is_active`, ...) |
| `GET` `POST` | `/platform/categories` | 🛡️ | List / create categories (`industry` is the industry key) |
| `PATCH` | `/platform/categories/{id}` | 🛡️ | Update or deactivate (`is_active: false`) a category. Never deleted, so existing products keep their category |

---

## Integrations

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/01/{gtin}/21/{serial}` | 🌐 | GS1 Digital Link — redirects (`302`) to `/verify/{serial}` |
| `POST` | `/integrations/africastalking/ussd` | 🌐 | Africa's Talking USSD inbound (validated by AT API key) |

---

## Error codes (product workflow)

| HTTP | `code` | When |
|---|---|---|
| 400 | `validation_error` | Field errors; `details` keyed by field (also submit with missing required fields) |
| 403 | `industry_locked` | Pharmaceuticals before the release gate opens |
| 409 | `sku_conflict` | SKU already used in the tenant |
| 409 | `possible_duplicate` | Similar product and no `confirm_not_duplicate` |
| 409 | `invalid_status_transition` | e.g. submitting an approved product, approving a draft, withdrawing after a decision |
| 409 | `product_not_editable` | Editing a product that is `in_review` |
| 409 | `field_locked` | Changing `industry` after creation, or `sku` / `gtin` after approval |
| 409 | `gtin_conflict` | Approving a product whose GTIN is registered against another product |
| 409 | `image_in_use` | Deleting an image a product still references |

**Image upload errors** carry a message that is safe to show to the user as is, plus structured `details` for building your own hint (for example a limits line beside the upload button):

```json
{ "error": {
    "code": "file_too_large",
    "message": "This image is 7.0 MB, which is over the 5.0 MB limit. Please compress it or save a smaller version, then upload it again.",
    "details": {
      "received_bytes": 7340032,
      "limits": { "max_bytes": 5242880, "min_side_px": 400, "max_side_px": 4000,
                  "accepted_types": ["image/png", "image/jpeg"] }
    } } }
```

`image_invalid` adds `details.reason` (`too_small`, `too_large` or `unreadable`) and, for size problems, `received_width_px` / `received_height_px`.
| 413 / 415 / 422 | `file_too_large` / `unsupported_media_type` / `image_invalid` | Image upload |

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
