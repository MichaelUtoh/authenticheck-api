# AuthentiCheck – API contracts

Base URL: `https://<host>/api/v1/`

---

## Common conventions

### Authentication

Session-cookie authentication. After a successful login the server sets a `sessionid` HttpOnly cookie.
Every subsequent request must present that cookie. CSRF is required for state-changing requests
(`POST`/`PATCH`/`DELETE`) when called from a browser; omit the CSRF token only when the client
explicitly opts out (e.g. native app calling with `Authorization` header instead – not yet supported).

### Tenant context

All catalogue, issuance, and most tenant endpoints require the active tenant to be declared in a
request header:

```
X-Tenant-ID: <tenant-uuid>
```

The user must have an active membership in that tenant and the tenant must be `approved`. Missing or
invalid values return `403`.

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
| 401 | `authentication_failed` | Wrong credentials or invalid MFA token |
| 403 | `permission_denied` | Not authenticated, wrong role, or inactive membership |
| 404 | `not_found` | Resource does not exist (or belongs to another tenant) |
| 409 | `idempotency_in_progress` | Same key is being processed concurrently |
| 422 | `idempotency_key_reuse` | Same key used with a different request body |
| 500 | `server_error` | Unexpected server-side failure |

### Idempotency

Endpoints that create resources and trigger background work require an `Idempotency-Key` header.
The key must be 8–128 URL-safe characters (`[A-Za-z0-9_.:\-]`). Repeating the exact same key and
body replays the original response with a `Idempotent-Replayed: true` header; no duplicate job is
created.

### Timestamps

All `created_at` / `updated_at` / `deactivated_at` fields are ISO 8601 strings in UTC,
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
{ "email": "alice@example.com", "password": "hunter2-is-too-short" }
```

**Response `200` — no MFA enrolled** — [User object](#user-object)

**Response `200` — MFA enrolled** (session holds a pending user; call `/auth/mfa/verify` next)
```json
{ "mfa_required": true }
```

**Error responses**

| Status | Meaning |
|---|---|
| `401` | Invalid credentials |

---

### `POST /auth/logout`

Destroy the current session. Requires authentication.

**Response `204`** — no body

---

### `GET /auth/me`

Return the authenticated user's profile and tenant memberships. Requires authentication.

**Response `200`**

[User object](#user-object) extended with:

```json
{
  "id": "…",
  "email": "alice@example.com",
  "full_name": "Alice Okafor",
  "is_staff": false,
  "mfa_enabled": false,
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
  ]
}
```

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

Confirm a TOTP device with a valid token from the authenticator app. Requires authentication.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `token` | string | yes | Exactly 6 digits. |

```json
{ "token": "123456" }
```

**Response `200`**
```json
{ "mfa_enabled": true }
```

---

### `POST /auth/mfa/verify`

Step 2 of MFA login. Must be called after `/auth/login` returned `mfa_required: true`.
No authentication required (the pending user is held in the session). Throttled to 10/min.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `token` | string | yes | Exactly 6 digits. |

```json
{ "token": "654321" }
```

**Response `200`** — [User object](#user-object). Session is now fully authenticated; `_mfa_verified`
flag is set for staff endpoints.

**Error responses**

| Status | Meaning |
|---|---|
| `401` | Invalid TOTP token |
| `403` | No pending MFA session (call `/auth/login` first) |

---

## Tenants

### `POST /tenants/register`

Register a new manufacturer tenant. The authenticated user automatically becomes its `owner`.
The tenant starts in `pending` status and must be approved by platform staff before it can be used.

**Request body**

| Field | Type | Required |
|---|---|---|
| `legal_name` | string | yes |
| `trading_name` | string | no |

```json
{ "legal_name": "Acme Cosmetics Ltd", "trading_name": "AcmeCare" }
```

**Response `201`** — [Tenant object](#tenant-object)

---

### `GET /tenants/me`

Retrieve the active tenant's details. Requires `X-Tenant-ID` with an approved membership.

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

**Request body** — same as approve

**Response `200`** — [Tenant object](#tenant-object)

---

### `POST /tenants/{id}/request-info` *(staff only)*

Move a `pending` tenant to `info_requested`.

**Request body** — same as approve

**Response `200`** — [Tenant object](#tenant-object)

---

### `POST /tenants/{id}/suspend` *(staff only)*

Suspend an `approved` tenant.

**Request body** — same as approve

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

All catalogue endpoints require `X-Tenant-ID`. Write operations (POST / PATCH / DELETE) require
`owner` or `admin` role.

---

### Brands

#### `GET /catalogue/brands`

List active brands for the tenant.

**Response `200`** — array of [Brand objects](#brand-object)

#### `POST /catalogue/brands`

Create a brand.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes | |
| `slug` | string | yes | Normalised to lowercase. Unique per tenant. |
| `is_active` | boolean | no | Defaults to `true`. |

```json
{ "name": "AcmeCare", "slug": "acmecare" }
```

**Response `201`** — [Brand object](#brand-object)

#### `GET /catalogue/brands/{id}`

Retrieve a single brand.

**Response `200`** — [Brand object](#brand-object)

#### `PATCH /catalogue/brands/{id}`

Partial update. Any subset of the write fields.

**Response `200`** — [Brand object](#brand-object)

#### `DELETE /catalogue/brands/{id}`

Soft-deactivate (sets `is_active = false`).

**Response `204`** — no body

---

### Products

#### `GET /catalogue/products`

List all products for the tenant.

**Response `200`** — array of [Product objects](#product-object)

#### `POST /catalogue/products`

Create a product under an existing brand.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `brand_id` | UUID | yes | Must belong to the active tenant. |
| `name` | string | yes | |
| `sku` | string | yes | Normalised to uppercase. Unique per tenant. |
| `description` | string | no | |
| `status` | string | no | `active` (default) or `discontinued`. |

```json
{ "brand_id": "…", "name": "AcmeCare 50ml", "sku": "ACM-50ML" }
```

**Response `201`** — [Product object](#product-object)

#### `GET /catalogue/products/{id}`

**Response `200`** — [Product object](#product-object)

#### `PATCH /catalogue/products/{id}`

Partial update. Any subset of the write fields (including `brand_id` to reassign).

**Response `200`** — [Product object](#product-object)

---

### Regulatory references

#### `GET /catalogue/products/{product_id}/regulatory-refs`

List regulatory references for a product.

**Response `200`** — array of [RegulatoryRef objects](#regulatoryref-object)

#### `POST /catalogue/products/{product_id}/regulatory-refs`

Create a regulatory reference.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `country_code` | string | yes | ISO 3166-1 alpha-3. Normalised to uppercase. |
| `reference_number` | string | yes | Unique per (tenant, product, country, reference). |
| `authority` | string | no | e.g. `"NAFDAC"` |
| `valid_from` | date | no | `YYYY-MM-DD` |
| `valid_until` | date | no | `YYYY-MM-DD` |

```json
{
  "country_code": "NGA",
  "reference_number": "NAFDAC-A7-0042",
  "authority": "NAFDAC",
  "valid_from": "2024-01-01",
  "valid_until": "2027-12-31"
}
```

**Response `201`** — [RegulatoryRef object](#regulatoryref-object)

---

### Batches

#### `GET /catalogue/products/{product_id}/batches`

List batches for a product (newest first).

**Response `200`** — array of [Batch objects](#batch-object)

#### `POST /catalogue/products/{product_id}/batches`

Create a production batch.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `batch_number` | string | yes | Unique per (tenant, product). |
| `quantity` | integer | no | Expected units. Defaults to `0`. |
| `manufactured_at` | date | no | `YYYY-MM-DD` |
| `expires_at` | date | no | `YYYY-MM-DD` |
| `status` | string | no | `active` (default), `recalled`, or `expired`. |
| `notes` | string | no | |

```json
{ "batch_number": "B2026-001", "quantity": 5000, "manufactured_at": "2026-10-01" }
```

**Response `201`** — [Batch object](#batch-object)

#### `GET /catalogue/products/{product_id}/batches/{id}`

**Response `200`** — [Batch object](#batch-object)

#### `PATCH /catalogue/products/{product_id}/batches/{id}`

Partial update.

**Response `200`** — [Batch object](#batch-object)

---

### Bulk import

#### `POST /catalogue/import`

Upload a CSV file to asynchronously import brands, products, or batches. Requires `owner` or `admin`.

**Request** — `multipart/form-data`

| Field | Type | Required | Notes |
|---|---|---|---|
| `import_type` | string | yes | `brands`, `products`, or `batches`. |
| `file` | file | yes | UTF-8 CSV with a header row. |

CSV column requirements by type:

| `import_type` | Required columns | Optional columns |
|---|---|---|
| `brands` | `name` | `slug` (auto-generated from `name` if absent) |
| `products` | `brand_slug`, `name`, `sku` | `description` |
| `batches` | `sku`, `batch_number` | `quantity`, `manufactured_at`, `expires_at`, `notes` |

**Response `202`** — [BulkImportJob object](#bulkimportjob-object)

#### `GET /catalogue/import/{id}`

Poll job status.

**Response `200`** — [BulkImportJob object](#bulkimportjob-object)

---

## Issuance

All issuance endpoints require `X-Tenant-ID`. Write operations require `owner` or `admin`.

---

### Issuance jobs

#### `GET /issuance/jobs`

List issuance jobs for the tenant (newest first).

**Response `200`** — array of [IssuanceJob objects](#issuancejob-object)

#### `POST /issuance/jobs`

Submit a job to generate unique codes for a batch. The job is processed asynchronously by a Celery
worker. Requires `Idempotency-Key` header.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `batch_id` | UUID | yes | Must be an `active` batch owned by the tenant. |
| `quantity` | integer | yes | 1–1 000 000. |

```json
{ "batch_id": "…", "quantity": 10000 }
```

**Response `202`** — [IssuanceJob object](#issuancejob-object). `status` is `pending`; poll
`GET /issuance/jobs/{id}` until `done` or `failed`.

**Error responses**

| Status | Meaning |
|---|---|
| `400` | `batch_id` not found, batch not `active`, or `quantity` out of range |
| `400` | `Idempotency-Key` header missing or malformed |
| `409` | Same key in flight concurrently |
| `422` | Same key reused with a different body |

#### `GET /issuance/jobs/{id}`

Retrieve job status and reconciliation.

**Response `200`** — [IssuanceJob object](#issuancejob-object)

`is_reconciled` is `true` when `quantity_issued == quantity_requested`.

#### `GET /issuance/jobs/{id}/export`

Download a CSV of all codes for a **completed** job. Returns a streaming response.

**Response `200`** — `text/csv`

```
Content-Disposition: attachment; filename="codes-<job-id>.csv"
```

CSV columns: `code`, `batch_number`, `product_sku`, `product_name`, `issued_at`

**Error responses**

| Status | Meaning |
|---|---|
| `400` | Job is not yet `done` |

---

### Codes

#### `GET /issuance/codes`

List codes for the tenant. Returns at most 500 rows. Filter with query parameters:

| Param | Type | Notes |
|---|---|---|
| `batch_id` | UUID | Filter by batch. |
| `job_id` | UUID | Filter by issuance job. |
| `status` | string | `active` or `deactivated`. |

**Response `200`** — array of [Code objects](#code-object)

#### `POST /issuance/codes/{code}/deactivate`

Deactivate a single code by its code string. Requires `owner` or `admin`.

**Request body**

| Field | Type | Required |
|---|---|---|
| `reason` | string | no |

```json
{ "reason": "defective unit" }
```

**Response `200`** — [Code object](#code-object) with `status: "deactivated"` and `deactivated_at` set.

**Error responses**

| Status | Meaning |
|---|---|
| `400` | Code is already deactivated |
| `404` | Code not found or belongs to another tenant |

#### `POST /issuance/batches/{batch_id}/deactivate`

Bulk-deactivate all active codes for a batch. Already-deactivated codes are untouched.

**Request body** — same as single-code deactivate

**Response `200`**
```json
{ "codes_deactivated": 4872 }
```

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

`role` values: `owner` · `admin` · `member` · `viewer`

### Tenant object

```json
{
  "id":           "uuid",
  "legal_name":   "Acme Cosmetics Ltd",
  "trading_name": "AcmeCare",
  "status":       "pending",
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
    { "row": 14, "error": "Brand 'unknown-brand' not found" },
    { "row": 57, "error": "sku and batch_number are required" }
  ],
  "created_at":    "2026-10-26T12:00:00Z",
  "updated_at":    "2026-10-26T12:00:00Z"
}
```

`import_type` values: `brands` · `products` · `batches`
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
  "code":                "Xk3mN9pQ2rL7sT4v",
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

`code` is a 22-character URL-safe string with 128 bits of entropy. It is globally unique across all
tenants (used as the lookup key in Phase 4 public verification).
