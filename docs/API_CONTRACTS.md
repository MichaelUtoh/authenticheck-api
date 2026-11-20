# AuthentiCheck – API contracts

Base URL: `https://<host>/api/v1/`

---

## Common conventions

### Authentication

The API uses **JWT Bearer tokens**. On successful login the server returns a short-lived
`access_token` (JWT) in the response body and sets a long-lived `refresh_token` in an HttpOnly,
`SameSite=Strict` cookie.

```
Authorization: Bearer eyJ...
```

Alternatively, machine clients may authenticate with an **API key**:

```
X-API-Key: ac_<token>
```

Browser clients that call logout or refresh must include the header:

```
X-Requested-With: XMLHttpRequest
```

### Tenant context

All catalogue, issuance, risk, analytics, and webhook endpoints require the active tenant to be
declared via the JWT claim `tid` (set at login when the user selects a tenant) or via the session.
The user must have an active membership in that tenant and the tenant must be `approved`.

Platform staff endpoints do **not** require a tenant context.

### Error envelope

Every error response uses the same JSON shape:

```json
{
  "error": {
    "code":       "validation_error",
    "message":    "One or more fields are invalid.",
    "request_id": "4f3a1c…",
    "details":    { "field": ["Error message."] }
  }
}
```

`details` is present only for `validation_error`. `request_id` matches the `X-Request-ID` response
header and can be used to correlate server logs.

| HTTP status | `code` | When |
|---|---|---|
| 400 | `validation_error` | Body fails serializer validation |
| 400 | `idempotency_key_required` | `Idempotency-Key` header missing or malformed |
| 401 | `authentication_failed` | Token expired, revoked, or credentials wrong |
| 401 | `token_expired` | Access token has passed its `exp` |
| 401 | `session_revoked` | Session was explicitly logged out or timed out |
| 403 | `permission_denied` | Not authenticated, wrong role, or inactive membership |
| 404 | `not_found` | Resource does not exist (or belongs to another tenant) |
| 409 | `idempotency_in_progress` | Same key is being processed concurrently |
| 422 | `idempotency_key_reuse` | Same key used with a different request body |
| 500 | `server_error` | Unexpected server-side failure |

### Idempotency

Endpoints that create resources and trigger background work require an `Idempotency-Key` header.
The key must be 8–128 URL-safe characters (`[A-Za-z0-9_.:\-]`). Repeating the exact same key and
body replays the original response with an `Idempotent-Replayed: true` header; no duplicate job is
created.

### Timestamps

All `created_at` / `updated_at` / `*_at` fields are ISO 8601 strings in UTC,
e.g. `"2026-10-26T14:32:00.123456Z"`.

---

## System

### `GET /health/live`

Liveness check. Returns `200 OK` if the process is running.

**Response `200`**
```json
{ "status": "ok" }
```

### `GET /health/ready`

Readiness check. Verifies the database connection.

**Response `200`**
```json
{ "status": "ok" }
```

---

## Auth

### `POST /auth/register`

Create a new user account. No authentication required.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `email` | string | yes | Normalised to lowercase. Must be unique. |
| `password` | string | yes | Min 12 characters. Django validators applied. |
| `full_name` | string | no | Defaults to `""`. |

```json
{
  "email": "alice@example.com",
  "password": "hunter2-is-too-short",
  "full_name": "Alice Okafor"
}
```

**Response `201`** — [User object](#user-object)

---

### `POST /auth/login`

Authenticate with email and password. Throttled to 10 requests per minute per IP.

**Request body**

| Field | Type | Required |
|---|---|---|
| `email` | string | yes |
| `password` | string | yes |

```json
{ "email": "alice@example.com", "password": "s3cr3t-passphrase!" }
```

**Response `200` — no MFA enrolled**

```json
{
  "access_token": "eyJ…",
  "user": { "id": "…", "email": "alice@example.com", "full_name": "Alice Okafor", "is_staff": false, "mfa_enabled": false }
}
```

A `refresh_token` HttpOnly cookie is also set (path `/api/v1/auth/refresh`).

**Response `200` — MFA enrolled** (call `POST /auth/mfa/verify` next)

```json
{ "mfa_required": true }
```

**Error responses**

| Status | Meaning |
|---|---|
| `401` | Invalid credentials |

---

### `POST /auth/refresh`

Exchange the `refresh_token` cookie for a new access token.
Requires `X-Requested-With: XMLHttpRequest`.

**Response `200`**
```json
{ "access_token": "eyJ…" }
```

A new `refresh_token` cookie is set (rotating refresh).

**Error responses**

| Status | Meaning |
|---|---|
| `401` | Cookie missing, expired, or session revoked |

---

### `POST /auth/logout`

Revoke the current session and clear the refresh cookie.
Requires `Authorization: Bearer` and `X-Requested-With: XMLHttpRequest`.

**Response `204`** — no body

---

### `GET /auth/me`

Return the authenticated user's profile, active tenant (if scoped), and all memberships.

**Response `200`**

```json
{
  "id": "…",
  "email": "alice@example.com",
  "full_name": "Alice Okafor",
  "is_staff": false,
  "mfa_enabled": false,
  "amr": ["pwd"],
  "memberships": [
    {
      "id": "…",
      "tenant_id": "…",
      "tenant_name": "Acme Cosmetics",
      "tenant_legal_name": "Acme Cosmetics Ltd",
      "tenant_status": "approved",
      "role": "owner",
      "is_active": true,
      "joined_at": "2026-10-26T12:00:00Z"
    }
  ],
  "active_tenant": { "id": "…", "legal_name": "Acme Cosmetics Ltd", "trading_name": "AcmeCare", "status": "approved" }
}
```

`active_tenant` is present only when the JWT carries a `tid` claim.

---

### `POST /auth/mfa/setup`

Begin TOTP enrolment. Returns a provisioning URI to display as a QR code. Requires authentication.
Any previous unconfirmed device is replaced.

**Response `201`**
```json
{ "provisioning_uri": "otpauth://totp/AuthentiCheck:alice%40example.com?secret=…&issuer=AuthentiCheck" }
```

---

### `POST /auth/mfa/confirm`

Confirm a TOTP device with a valid token from the authenticator app.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `token` | string | yes | Exactly 6 digits. |

**Response `200`**
```json
{ "mfa_enabled": true }
```

---

### `POST /auth/mfa/verify`

Step 2 of MFA login. Must be called after `/auth/login` returned `mfa_required: true`.
No authentication required (pending user is held in session). Throttled to 10/min.

**Request body**

| Field | Type | Required |
|---|---|---|
| `token` | string | yes |

**Response `200`** — same shape as a successful `/auth/login` (access_token + refresh cookie).

**Error responses**

| Status | Meaning |
|---|---|
| `401` | Invalid TOTP token |
| `403` | No pending MFA session (call `/auth/login` first) |

---

## API Keys

Require `owner` or `admin` role. The tenant must be active.

### `GET /api-keys`

List API keys for the active tenant. Does not reveal the raw key.

**Response `200`** — array of:

```json
{
  "id": "uuid",
  "name": "CI pipeline",
  "key_prefix": "ab12cd34",
  "last_used_at": "2026-10-26T10:00:00Z",
  "expires_at": null,
  "revoked_at": null,
  "created_at": "2026-10-26T09:00:00Z"
}
```

---

### `POST /api-keys`

Create an API key. The raw key is returned **once** — store it securely.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes | Max 100 characters. |
| `expires_at` | datetime | no | ISO 8601. Omit for non-expiring. |

**Response `201`**

```json
{
  "id": "uuid",
  "name": "CI pipeline",
  "key": "ac_ab12cd34xxxxxxxxxxxxxxxxxxxxxxxx",
  "key_prefix": "ab12cd34",
  "created_at": "2026-10-26T09:00:00Z"
}
```

Send the key in the `X-API-Key` header. It cannot be retrieved again.

---

### `DELETE /api-keys/{id}`

Revoke an API key immediately. Idempotent — revoking an already-revoked key returns `204`.

**Response `204`** — no body

---

## Tenants

### `POST /tenants/register`

Register a new manufacturer tenant. The authenticated user automatically becomes its `owner`.
The tenant starts in `pending` status and must be approved by platform staff.

**Request body**

| Field | Type | Required |
|---|---|---|
| `legal_name` | string | yes |
| `trading_name` | string | no |
| `industry` | string | yes. A key from `GET /tenants/industries` (e.g. `cosmetics`, `pharmaceuticals`); trimmed and lower-cased. Choosing `pharmaceuticals` does not unlock pharma issuance |
| `rc_number` | string | yes. CAC registration number, max 32 characters; stored upper-case without spaces or dashes |
| `city` | string | yes. Max 120 characters |

Missing or invalid fields return `400 validation_error` with `error.details.<field>`.

**Response `201`** — [Tenant object](#tenant-object)

---

### `GET /tenants/me`

Retrieve the active tenant's details. Requires an approved membership.

**Response `200`** — [Tenant object](#tenant-object)

---

### `GET /tenants/me/members`

List active members of the active tenant. Requires `owner` or `admin` role.

**Response `200`** — array of [Membership objects](#membership-object)

---

### `POST /tenants/{id}/approve` *(staff only)*

Approve a `pending` or `info_requested` tenant. Requires `is_staff=True` and MFA verified.

**Request body**

| Field | Type | Required |
|---|---|---|
| `reason` | string | no |

**Response `200`** — [Tenant object](#tenant-object)

---

### `POST /tenants/{id}/reject` *(staff only)*

Reject a `pending` or `info_requested` tenant.

**Response `200`** — [Tenant object](#tenant-object)

---

### `POST /tenants/{id}/request-info` *(staff only)*

Move a `pending` tenant to `info_requested`.

**Response `200`** — [Tenant object](#tenant-object)

---

### `POST /tenants/{id}/suspend` *(staff only)*

Suspend an `approved` tenant.

**Response `200`** — [Tenant object](#tenant-object)

**Allowed status transitions**

| Current status | approve | reject | request_info | suspend |
|---|---|---|---|---|
| `pending` | ✓ | ✓ | ✓ | — |
| `info_requested` | ✓ | ✓ | — | — |
| `approved` | — | — | — | ✓ |
| `suspended` | ✓ | — | — | — |

---

## Catalogue

All catalogue endpoints require an active tenant membership. Write operations require `owner` or
`admin` role.

---

### Brands

#### `GET /catalogue/brands`

List active brands for the tenant.

**Response `200`** — array of [Brand objects](#brand-object)

#### `POST /catalogue/brands`

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes | |
| `slug` | string | yes | Normalised to lowercase. Unique per tenant. |
| `is_active` | boolean | no | Defaults to `true`. |

**Response `201`** — [Brand object](#brand-object)

#### `GET /catalogue/brands/{id}`

**Response `200`** — [Brand object](#brand-object)

#### `PATCH /catalogue/brands/{id}`

Partial update.

**Response `200`** — [Brand object](#brand-object)

#### `DELETE /catalogue/brands/{id}`

Soft-deactivate (`is_active = false`).

**Response `204`** — no body

---

### Products

#### `GET /catalogue/products`

**Response `200`** — array of [Product objects](#product-object)

#### `POST /catalogue/products`

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `brand_id` | UUID | yes | Must belong to the active tenant. |
| `name` | string | yes | |
| `sku` | string | yes | Normalised to uppercase. Unique per tenant. |
| `description` | string | no | |
| `status` | string | no | `active` (default) or `discontinued`. |

**Response `201`** — [Product object](#product-object)

#### `GET /catalogue/products/{id}`

**Response `200`** — [Product object](#product-object)

#### `PATCH /catalogue/products/{id}`

**Response `200`** — [Product object](#product-object)

---

### Regulatory references

#### `GET /catalogue/products/{product_id}/regulatory-refs`

**Response `200`** — array of [RegulatoryRef objects](#regulatoryref-object)

#### `POST /catalogue/products/{product_id}/regulatory-refs`

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `country_code` | string | yes | ISO 3166-1 alpha-3. |
| `reference_number` | string | yes | Unique per (tenant, product, country). |
| `authority` | string | no | e.g. `"NAFDAC"` |
| `valid_from` | date | no | `YYYY-MM-DD` |
| `valid_until` | date | no | `YYYY-MM-DD` |

**Response `201`** — [RegulatoryRef object](#regulatoryref-object)

---

### Batches

#### `GET /catalogue/products/{product_id}/batches`

**Response `200`** — array of [Batch objects](#batch-object)

#### `POST /catalogue/products/{product_id}/batches`

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `batch_number` | string | yes | Unique per (tenant, product). |
| `quantity` | integer | no | Expected units. Defaults to `0`. |
| `manufactured_at` | date | no | |
| `expires_at` | date | no | |
| `status` | string | no | `active` (default), `recalled`, or `expired`. |
| `notes` | string | no | |

**Response `201`** — [Batch object](#batch-object)

#### `GET /catalogue/products/{product_id}/batches/{id}`

**Response `200`** — [Batch object](#batch-object)

#### `PATCH /catalogue/products/{product_id}/batches/{id}`

**Response `200`** — [Batch object](#batch-object)

#### `POST /catalogue/products/{product_id}/batches/{id}/recall`

Immediately recalls the batch: sets `status = recalled` and bulk-deactivates all active codes.

**Request body**

| Field | Type | Required |
|---|---|---|
| `reason` | string | yes |

**Response `200`** — [Batch object](#batch-object) with `status: "recalled"`

---

### Bulk import

#### `POST /catalogue/import`

Upload a CSV file to asynchronously import brands, products, or batches. Requires `owner` or `admin`.

**Request** — `multipart/form-data`

| Field | Type | Required | Notes |
|---|---|---|---|
| `import_type` | string | yes | `brands`, `products`, or `batches`. |
| `file` | file | yes | UTF-8 CSV with a header row. |

**Response `202`** — [BulkImportJob object](#bulkimportjob-object)

#### `GET /catalogue/import/{id}`

Poll job status.

**Response `200`** — [BulkImportJob object](#bulkimportjob-object)

---

## Issuance

All issuance endpoints require an active tenant membership. Write operations require `owner` or `admin`.

---

### `GET /issuance/jobs`

List issuance jobs for the tenant (newest first).

**Response `200`** — array of [IssuanceJob objects](#issuancejob-object)

---

### `POST /issuance/jobs`

Submit a job to generate unique codes for a batch. Requires `Idempotency-Key` header.

**Headers**

| Header | Required | Notes |
|---|---|---|
| `Idempotency-Key` | yes | 8–128 URL-safe chars. |

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `batch_id` | UUID | yes | Must be an `active` batch owned by the tenant. |
| `quantity` | integer | yes | 1–1 000 000. |

**Response `202`** — [IssuanceJob object](#issuancejob-object). Poll `GET /issuance/jobs/{id}` until `status` is `done` or `failed`.

---

### `GET /issuance/jobs/{id}`

**Response `200`** — [IssuanceJob object](#issuancejob-object)

---

### `GET /issuance/jobs/{id}/export`

Download a CSV of all codes for a completed job (streaming).

**Response `200`** — `text/csv`

```
Content-Disposition: attachment; filename="codes-<job-id>.csv"
```

Columns: `code`, `batch_number`, `product_sku`, `product_name`, `issued_at`, `code_display`, `verify_url`

- `code_display`: the code grouped `XXXX-XXXX-XXXX`; print it beside the QR as the manual-entry fallback.
- `verify_url`: `{VERIFY_BASE_URL}/v/{code}`, the address to encode in the QR code (the printer builds the QR image from this column).

**Error** `400` if job status is not `done`.

---

### `GET /issuance/codes`

List codes. Returns at most 500 rows.

| Param | Type | Notes |
|---|---|---|
| `batch_id` | UUID | Filter by batch. |
| `job_id` | UUID | Filter by issuance job. |
| `status` | string | `active` or `deactivated`. |

**Response `200`** — array of [Code objects](#code-object)

---

### `POST /issuance/codes/{code}/deactivate`

Deactivate a single code by its code string. Requires `owner` or `admin`.

**Request body**

| Field | Type | Required |
|---|---|---|
| `reason` | string | no |

**Response `200`** — [Code object](#code-object)

---

### `POST /issuance/batches/{batch_id}/deactivate`

Bulk-deactivate all active codes for a batch.

**Request body** — same as single-code deactivate

**Response `200`**
```json
{ "codes_deactivated": 4872 }
```

---

## Verification

### `GET /verify/{code}`

Public endpoint (no authentication). Resolve a product code and record a scan event.
Throttled per IP. The `result` field is the canonical source of truth.

**Path parameter**: `code` — the 12-character code from the label. Case, spaces and dashes are ignored (`7kq2-m9xw-4prt` and `7KQ2M9XW4PRT` are the same). Input that cannot be a code returns `400 invalid_code_format` and is not recorded.

**Response `200` — genuine or recalled**

```json
{
  "result": "genuine",
  "code": "Xk3mN9pQ2rL7sT4v",
  "product": {
    "name": "AcmeCare 50ml",
    "sku": "ACM-50ML",
    "brand": "AcmeCare"
  },
  "batch": {
    "batch_number": "B2026-001",
    "expires_at": "2028-10-01",
    "status": "active"
  },
  "manufacturer": {
    "name": "Acme Cosmetics Ltd"
  }
}
```

`result` values: `genuine` · `recalled` · `deactivated` · `not_found`

**Response `200` — deactivated**

```json
{
  "result": "deactivated",
  "code": "Xk3mN9pQ2rL7sT4v",
  "deactivated_at": "2026-10-15T08:00:00Z",
  "deactivation_reason": "defective unit"
}
```

**Response `404` — not found**

```json
{ "result": "not_found", "detail": "Code not found." }
```

> A `genuine` result confirms the code exists in the database; it does not certify the physical
> product is genuine. Copy must never make that claim.

---

## Risk & Recall

### `POST /reports`

Submit a consumer counterfeit report. Public endpoint, no authentication. Throttled per IP.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `code_str` | string | yes | The code from the label. |
| `description` | string | yes | |
| `contact_email` | string | no | |
| `country_code` | string | no | ISO 3166-1 alpha-3. |

**Response `201`** — [ConsumerReport object](#consumerreport-object)

---

### `GET /risk/flags`

List risk flags for the active tenant. Requires active membership.

| Param | Type | Notes |
|---|---|---|
| `kind` | string | Filter by flag kind (e.g. `repeated_scan`, `consumer_report`). |
| `severity` | string | `high`, `medium`, or `low`. |
| `is_reviewed` | boolean | `true` or `false`. |

**Response `200`** — array of [RiskFlag objects](#riskflag-object)

---

### `POST /risk/flags/{id}/review`

Mark a flag as reviewed. Requires `owner` or `admin`.

**Response `200`** — [RiskFlag object](#riskflag-object) with `is_reviewed: true`

---

### `GET /risk/cases`

List cases for the active tenant. Requires active membership.

**Response `200`** — array of [Case objects](#case-object)

---

### `POST /risk/cases`

Open a new case. Requires `owner` or `admin`.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | string | yes | |
| `description` | string | no | |
| `batch_id` | UUID | no | Associate with a batch. |
| `product_id` | UUID | no | Associate with a product. |
| `notes` | string | no | |

**Response `201`** — [Case object](#case-object)

---

### `GET /risk/cases/{id}`

**Response `200`** — [Case object](#case-object)

---

### `PATCH /risk/cases/{id}`

Update case status or notes. Requires `owner` or `admin`.

**Request body**

| Field | Type | Notes |
|---|---|---|
| `status` | string | `open`, `under_review`, or `closed`. |
| `notes` | string | |

**Response `200`** — [Case object](#case-object)

---

## Analytics

All analytics endpoints require an active tenant membership.

---

### `GET /analytics/scans`

Scan volume and outcome breakdown.

| Param | Type | Notes |
|---|---|---|
| `days` | integer | Lookback window 1–365. Default `30`. |

**Response `200`**

```json
{
  "period_days": 30,
  "total_scans": 1240,
  "by_result": {
    "genuine": 1190,
    "recalled": 10,
    "deactivated": 25,
    "not_found": 15
  },
  "by_day": [
    { "date": "2026-10-01", "count": 42 },
    { "date": "2026-10-02", "count": 38 }
  ]
}
```

---

### `GET /analytics/codes`

Current code inventory snapshot.

**Response `200`**

```json
{
  "total_issued": 50000,
  "active": 48500,
  "deactivated": 1500
}
```

---

### `GET /analytics/risk`

Risk flag and case summary.

**Response `200`**

```json
{
  "open_flags": {
    "high": 3,
    "medium": 12,
    "low": 45
  },
  "flags_last_30_days": 22,
  "cases": {
    "open": 2,
    "under_review": 1,
    "closed": 8
  }
}
```

---

## Webhooks

All webhook endpoints require an active tenant membership. Write operations require `owner` or `admin`.

---

### `GET /webhooks`

List webhook endpoints for the tenant.

**Response `200`** — array of [WebhookEndpoint objects](#webhookendpoint-object)

---

### `POST /webhooks`

Register a webhook endpoint. A secret is generated and returned **once**.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `url` | string | yes | HTTPS URL, max 500 characters. |
| `events` | array | yes | Array of event type strings (see below). |
| `description` | string | no | |

**Event types**

| Event | Fired when |
|---|---|
| `code.verified` | A code is scanned via `GET /verify/{code}` |
| `code.deactivated` | A code is deactivated |
| `batch.recalled` | A batch is recalled |
| `risk.flag_created` | A new risk flag is raised |
| `case.status_changed` | A case status changes |

**Response `201`** — [WebhookEndpoint object](#webhookendpoint-object) with `secret` included.

Store the secret securely. Deliveries are signed with `HMAC-SHA256(secret, body)` in the
`X-AuthentiCheck-Signature` header.

---

### `GET /webhooks/{id}`

**Response `200`** — [WebhookEndpoint object](#webhookendpoint-object)

---

### `PATCH /webhooks/{id}`

Update URL, events list, or description.

**Response `200`** — [WebhookEndpoint object](#webhookendpoint-object)

---

### `DELETE /webhooks/{id}`

**Response `204`** — no body

---

### `GET /webhooks/{id}/deliveries`

List recent delivery attempts for a webhook endpoint (newest first, paginated).

**Response `200`** — array of [WebhookDelivery objects](#webhookdelivery-object)

---

## Platform *(staff only)*

All platform endpoints require `is_staff=True` and MFA verified.

---

### `GET /platform/tenants`

List all tenants with pagination.

| Param | Type | Notes |
|---|---|---|
| `status` | string | Filter by tenant status. |

**Response `200`** — paginated array of [Tenant objects](#tenant-object)

---

### `GET /platform/tenants/{id}`

Retrieve a tenant with usage statistics.

**Response `200`** — [Tenant object](#tenant-object) extended with:

```json
{
  "member_count": 5,
  "product_count": 12,
  "codes_issued": 50000,
  "flags_count": 8
}
```

---

### `GET /platform/flags`

List unreviewed risk flags across all tenants.

| Param | Type | Notes |
|---|---|---|
| `severity` | string | Filter by severity. |
| `kind` | string | Filter by flag kind. |

**Response `200`** — paginated array of [RiskFlag objects](#riskflag-object)

---

### `GET /platform/audit`

Paginated audit log across all tenants.

| Param | Type | Notes |
|---|---|---|
| `action` | string | Filter by action prefix (e.g. `auth.`, `batch.`). |
| `tenant_id` | UUID | Filter by tenant. |

**Response `200`** — paginated array of audit events:

```json
{
  "id": "uuid",
  "action": "batch.recalled",
  "actor_id": "uuid",
  "tenant_id": "uuid",
  "target_type": "Batch",
  "target_id": "uuid",
  "before": {},
  "after": { "status": "recalled", "reason": "contamination" },
  "created_at": "2026-10-26T14:00:00Z"
}
```

---

## Integrations

### `GET /01/{gtin}/21/{serial}`

GS1 Digital Link resolver. Redirects (`302`) to the consumer page `{VERIFY_BASE_URL}/v/{code}`; `serial` is the 12-character code and is normalised (upper-case, no spaces or dashes) first.

**Path parameters**: `gtin` — 14-digit GTIN; `serial` — code string from label.

**Response `302`** — `Location: /verify/{serial}`

---

### `POST /integrations/africastalking/ussd`

Africa's Talking inbound USSD hook. Accepts form-encoded POST from AT's platform.
Request is validated against the AT API key. Returns a USSD menu/response string.

**Request** — `application/x-www-form-urlencoded`

| Field | Notes |
|---|---|
| `sessionId` | AT session identifier |
| `phoneNumber` | Caller's E.164 number |
| `text` | Current USSD input string |
| `apiKey` | Validated server-side |

**Response `200`** — plain text, prefixed `CON` (continue) or `END` (terminate).

---

## Object schemas

### User object

```json
{
  "id":          "uuid",
  "email":       "alice@example.com",
  "full_name":   "Alice Okafor",
  "is_staff":    false,
  "mfa_enabled": false
}
```

### Membership object

```json
{
  "id":                "uuid",
  "tenant_id":         "uuid",
  "tenant_name":       "AcmeCare",
  "tenant_legal_name": "Acme Cosmetics Ltd",
  "tenant_status":     "approved",
  "role":              "owner",
  "is_active":         true,
  "joined_at":         "2026-10-26T12:00:00Z"
}
```

`role` values: `owner` · `admin` · `production_quality` · `investigator` · `viewer` (legacy `editor`/`member` are accepted on input and mapped to `production_quality`)

### Tenant object

```json
{
  "id":           "uuid",
  "legal_name":   "Acme Cosmetics Ltd",
  "trading_name": "AcmeCare",
  "status":       "approved",
  "created_at":   "2026-10-26T12:00:00Z"
}
```

`status` values: `pending` · `info_requested` · `approved` · `rejected` · `suspended`

### Brand object

```json
{
  "id":         "uuid",
  "name":       "AcmeCare",
  "slug":       "acmecare",
  "is_active":  true,
  "created_at": "2026-10-26T12:00:00Z",
  "updated_at": "2026-10-26T12:00:00Z"
}
```

### Product object

```json
{
  "id":          "uuid",
  "brand_id":    "uuid",
  "name":        "AcmeCare 50ml",
  "sku":         "ACM-50ML",
  "description": "",
  "status":      "active",
  "created_at":  "2026-10-26T12:00:00Z",
  "updated_at":  "2026-10-26T12:00:00Z"
}
```

`status` values: `active` · `discontinued`

### RegulatoryRef object

```json
{
  "id":               "uuid",
  "product_id":       "uuid",
  "country_code":     "NGA",
  "authority":        "NAFDAC",
  "reference_number": "NAFDAC-A7-0042",
  "valid_from":       "2024-01-01",
  "valid_until":      "2027-12-31",
  "created_at":       "2026-10-26T12:00:00Z",
  "updated_at":       "2026-10-26T12:00:00Z"
}
```

### Batch object

```json
{
  "id":              "uuid",
  "product_id":      "uuid",
  "batch_number":    "B2026-001",
  "manufactured_at": "2026-10-01",
  "expires_at":      "2028-10-01",
  "quantity":        5000,
  "status":          "active",
  "notes":           "",
  "created_at":      "2026-10-26T12:00:00Z",
  "updated_at":      "2026-10-26T12:00:00Z"
}
```

`status` values: `active` · `recalled` · `expired`

### BulkImportJob object

```json
{
  "id":            "uuid",
  "import_type":   "products",
  "status":        "done",
  "file_name":     "products.csv",
  "rows_total":    100,
  "rows_imported": 98,
  "rows_failed":   2,
  "errors": [
    { "row": 14, "error": "Brand 'unknown-brand' not found" }
  ],
  "created_at":    "2026-10-26T12:00:00Z",
  "updated_at":    "2026-10-26T12:00:00Z"
}
```

`status` values: `pending` · `processing` · `done` · `failed`

### IssuanceJob object

```json
{
  "id":                 "uuid",
  "batch_id":           "uuid",
  "quantity_requested": 10000,
  "quantity_issued":    10000,
  "status":             "done",
  "is_reconciled":      true,
  "errors":             [],
  "created_at":         "2026-10-26T12:00:00Z",
  "updated_at":         "2026-10-26T12:00:00Z"
}
```

`status` values: `pending` · `processing` · `done` · `failed`

### Code object

```json
{
  "id":                  "uuid",
  "code":                "7KQ2M9XW4PRT",
  "batch_id":            "uuid",
  "product_id":          "uuid",
  "status":              "active",
  "deactivated_at":      null,
  "deactivation_reason": "",
  "created_at":          "2026-10-26T12:00:00Z",
  "updated_at":          "2026-10-26T12:00:00Z"
}
```

`status` values: `active` · `deactivated`

`code` is 12 characters, returned canonical (upper-case, no dashes; display as `XXXX-XXXX-XXXX`). Alphabet `23456789ABCDEFGHJKMNPQRSTVWXYZ`; the 12th character is a Luhn mod-30 check. Globally unique across all tenants. Codes converted from the old 22-character format also resolve by their old value (exact match).

### ConsumerReport object

```json
{
  "id":            "uuid",
  "code_str":      "7KQ2M9XW4PRT",
  "description":   "Packaging looked different",
  "contact_email": "consumer@example.com",
  "country_code":  "NGA",
  "created_at":    "2026-10-26T12:00:00Z"
}
```

### RiskFlag object

```json
{
  "id":          "uuid",
  "kind":        "repeated_scan",
  "severity":    "high",
  "code_id":     "uuid",
  "batch_id":    "uuid",
  "case_id":     null,
  "detail":      { "scan_count": 15, "threshold": 10 },
  "is_reviewed": false,
  "reviewed_at": null,
  "created_at":  "2026-10-26T12:00:00Z"
}
```

`kind` values: `repeated_scan` · `consumer_report` · `recalled_scan` · `geographic_anomaly`
`severity` values: `high` · `medium` · `low`

### Case object

```json
{
  "id":          "uuid",
  "status":      "open",
  "title":       "Suspected counterfeit batch B2026-001",
  "description": "",
  "batch_id":    "uuid",
  "product_id":  "uuid",
  "notes":       "",
  "flags_count": 3,
  "resolved_at": null,
  "created_at":  "2026-10-26T12:00:00Z",
  "updated_at":  "2026-10-26T12:00:00Z"
}
```

`status` values: `open` · `under_review` · `closed`

### WebhookEndpoint object

```json
{
  "id":             "uuid",
  "url":            "https://example.com/hooks/authenticheck",
  "events":         ["code.verified", "batch.recalled"],
  "status":         "active",
  "description":    "",
  "failure_count":  0,
  "last_success_at": "2026-10-26T13:00:00Z",
  "created_at":     "2026-10-26T09:00:00Z",
  "updated_at":     "2026-10-26T09:00:00Z"
}
```

On creation only, `secret` is also included in the response.

### WebhookDelivery object

```json
{
  "id":              "uuid",
  "event_type":      "code.verified",
  "status":          "delivered",
  "attempt_count":   1,
  "response_status": 200,
  "delivered_at":    "2026-10-26T13:00:05Z",
  "created_at":      "2026-10-26T13:00:04Z"
}
```

`status` values: `pending` · `delivered` · `failed`
