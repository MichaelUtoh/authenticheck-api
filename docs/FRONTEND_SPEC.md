# AuthentiCheck – Next.js Frontend Specification

Product authentication and traceability platform for manufacturers (Nigeria-first, cosmetics and
pharmaceuticals). This document covers everything the frontend team needs to build the Next.js
client against the Django/DRF backend.

API reference: `docs/API_CONTRACTS.md`
Backend base URL: `https://<host>/api/v1/`

---

## Tech stack

| Concern | Choice | Notes |
|---|---|---|
| Framework | Next.js 15 (App Router) | Server Components for data-heavy pages; Client Components for interactive UI |
| Language | TypeScript | Strict mode |
| Styling | Tailwind CSS + shadcn/ui | Pre-built accessible components |
| Forms | React Hook Form + Zod | Client-side validation mirrors server rules |
| HTTP client | `ky` or `axios` | Interceptors handle token refresh transparently |
| State | Zustand (auth + tenant) | Lightweight; no Redux overhead |
| Charts | Recharts | Scan analytics, risk dashboards |
| File upload | Native `<input type="file">` + `fetch` | CSV bulk import |
| QR display | `qrcode.react` | MFA provisioning URI |

---

## Repository layout

```
app/
  (public)/
    verify/[code]/          # Consumer verification page (no auth)
    login/                  # Login + MFA challenge
    register/               # New user registration
  (auth)/                   # Authenticated shell with sidebar
    layout.tsx              # Checks access_token; redirects to /login if absent
    onboarding/             # Tenant registration + pending state
    dashboard/              # Scan/code/risk analytics overview
    catalogue/
      brands/
      products/[id]/
      batches/
    issuance/
      jobs/
      jobs/[id]/
      codes/
    risk/
      flags/
      cases/
      cases/[id]/
    webhooks/
    settings/
      api-keys/
      team/
  (staff)/                  # is_staff=true guard
    platform/
      tenants/
      tenants/[id]/
      flags/
      audit/
lib/
  api/                      # Typed API functions (one file per resource)
  auth/                     # Token storage, refresh logic, auth context
  hooks/                    # useTenant, useMe, useAnalytics, etc.
  types/                    # TypeScript types generated from API_CONTRACTS.md
components/
  ui/                       # shadcn/ui re-exports
  auth/                     # LoginForm, MFASetup, MFAChallenge
  catalogue/                # BrandTable, ProductCard, BatchForm
  issuance/                 # JobStatusBadge, CodeTable, IssuanceJobForm
  risk/                     # FlagSeverityBadge, CaseStatusSelect, FlagTable
  analytics/                # ScanChart, RiskSummaryCard, CodeInventoryCard
  platform/                 # TenantStatusBadge, AuditLogTable
  shared/                   # ErrorBoundary, LoadingSpinner, EmptyState, Pagination
```

---

## Authentication

### Token storage

- **Access token**: stored in memory (a Zustand store or React context). **Never** in
  `localStorage` or `sessionStorage` — XSS would expose it.
- **Refresh token**: stored server-side in an HttpOnly cookie; the frontend never reads it.

### Login flow

```
POST /auth/login
  → mfa_required: true  →  POST /auth/mfa/verify  →  store access_token
  → access_token         →  store access_token
```

After storing the token, call `GET /auth/me` to hydrate the user + memberships. If the user has
no memberships, redirect to `/onboarding`.

### Refresh flow

The access token is a short-lived JWT. On any API call that returns `401`:

1. Call `POST /auth/refresh` with `X-Requested-With: XMLHttpRequest`.
2. On success, update the in-memory token and retry the original request once.
3. On failure (`401` from refresh), clear token state and redirect to `/login`.

Implement this as an HTTP interceptor so all API calls benefit automatically.

### Logout

```
POST /auth/logout
  headers: { Authorization: Bearer ..., X-Requested-With: XMLHttpRequest }
```

Clear the in-memory token and redirect to `/login`.

### Tenant selection

A user may belong to multiple tenants. The selected tenant is embedded in the JWT (`tid` claim).
To switch tenants the user must log in again (re-select during login or via a tenant-switcher that
triggers a fresh login flow).

Store `active_tenant` from `GET /auth/me` in Zustand alongside the access token.

### Staff guard

Wrap `(staff)/` routes in a server-side check: if `user.is_staff === false`, redirect to `/dashboard`.

---

## Pages

### `/verify/[code]` — Consumer verification (public)

The entry point for consumers who scan a QR code on a product.

**Data**: `GET /verify/{code}`

**States to handle**:

| `result` | UI |
|---|---|
| `genuine` | Green badge; product name, batch number, expiry, manufacturer |
| `recalled` | Red warning banner; product info; "Do not use" message |
| `deactivated` | Amber banner; deactivated date; do not use copy |
| `not_found` | Red error; prompt to report |

Include a **Report** link / button at the bottom that opens `POST /reports` (pre-filled `code_str`).

> Copy must never claim the physical item is genuine. Show "This code is registered" not
> "This product is genuine".

---

### `/login`

Standard email + password form. On `mfa_required: true` show a TOTP challenge input in-page
(no page navigation). Use `LoginEmailIPThrottle` awareness: after the second `401` show a
"too many attempts" message rather than looping.

---

### `/register`

Email, password (with strength meter), full name. On success redirect to `/onboarding`.

---

### `/onboarding`

**Step 1** — Register tenant (`POST /tenants/register`): legal name, trading name.
**Step 2** — Pending approval screen (poll `GET /auth/me` for `tenant_status === 'approved'`).

Do not allow access to the main app until the tenant is `approved`.

---

### `/dashboard`

Overview page. Requires active approved tenant.

**Widgets**:
- Scan analytics chart: `GET /analytics/scans?days=30` — line chart of `by_day`, summary of `by_result`
- Code inventory: `GET /analytics/codes` — 3 stat cards (total, active, deactivated)
- Risk summary: `GET /analytics/risk` — flag severity breakdown, open cases count

---

### `/catalogue/brands`

Table of brands (`GET /catalogue/brands`). Create (`POST`) and deactivate (`DELETE`) inline.

### `/catalogue/products`

Table with brand filter. Link to product detail page.

### `/catalogue/products/[id]`

Product details, batches list, regulatory refs list. Sub-forms to add batches and regulatory refs.
**Recall** button on each batch (calls `POST /catalogue/products/{product_id}/batches/{id}/recall`)
with a confirmation modal and a required reason field.

### `/catalogue/import`

Drag-and-drop CSV uploader. Radio selector for `import_type`. After `POST /catalogue/import`
returns `202`, poll `GET /catalogue/import/{id}` every 3 s until `status` is `done` or `failed`.
Show per-row errors in an expandable error table.

---

### `/issuance/jobs`

Job list with status badges. **New job** form: batch picker (only `active` batches), quantity.
Requires `Idempotency-Key` — generate a `crypto.randomUUID()` client-side per submission.

### `/issuance/jobs/[id]`

Job detail: status, `quantity_requested`, `quantity_issued`, `is_reconciled`.
**Download CSV** button (`GET /issuance/jobs/{id}/export`) — only enabled when `status === 'done'`.
Poll every 5 s while `status` is `pending` or `processing`.

### `/issuance/codes`

Searchable/filterable table. Filters: `batch_id`, `status`, `job_id`. Max 500 rows per request.
Single-code deactivate action per row.

---

### `/risk/flags`

Flag list with severity colour-coding. Filter by `severity`, `kind`, `is_reviewed`.
Mark-reviewed action per flag.

### `/risk/cases`

Case list. Open new case form (title, description, optional batch/product link).

### `/risk/cases/[id]`

Case detail: status selector, notes, linked flags count.

---

### `/webhooks`

Endpoint list. **Register** form: URL, events checkboxes (from event type enum), description.
On creation, show the `secret` in a one-time modal with a copy button and a warning that it
cannot be retrieved again. Delete with confirmation.

**Deliveries drawer**: click an endpoint → slide-out panel listing recent deliveries with status
and HTTP response code.

---

### `/settings/api-keys`

API key list (prefix, name, last used, expiry, revoked). **Create** button: name, optional
expiry date. On creation, show raw key in a one-time modal. Revoke action per key.

### `/settings/team`

Member list. (Invite flow is out of scope for Phase 1 of the frontend — show read-only list.)

---

### `/platform/tenants` *(staff only)*

Sortable/filterable tenant table. Status filter. Click through to tenant detail.

### `/platform/tenants/[id]` *(staff only)*

Tenant detail: usage stats, action buttons (approve / reject / request-info / suspend) rendered
based on allowed transitions.

### `/platform/flags` *(staff only)*

Cross-tenant unreviewed flags. Severity + kind filters.

### `/platform/audit` *(staff only)*

Paginated audit log. Filter by `action` prefix and `tenant_id`. Show `before`/`after` JSON
in an expandable detail row.

---

## API client

Create a base client that:

1. Attaches `Authorization: Bearer <token>` from in-memory store.
2. Attaches `X-Requested-With: XMLHttpRequest` on all requests.
3. On `401`, attempts one token refresh then retries.
4. Normalises errors into the `{ error: { code, message, details, request_id } }` envelope so
   components always receive the same shape.

```typescript
// lib/api/client.ts (sketch)
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      'X-Requested-With': 'XMLHttpRequest',
      Authorization: `Bearer ${getAccessToken()}`,
      ...init?.headers,
    },
  });

  if (res.status === 401 && !isRefreshPath(path)) {
    const refreshed = await tryRefresh();
    if (refreshed) return apiFetch(path, init); // retry once
    redirectToLogin();
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, body.error ?? { code: 'unknown', message: res.statusText });
  }

  return res.json();
}
```

---

## Error handling

- **Validation errors** (`validation_error`): display field-level messages next to form inputs.
- **`authentication_failed` / `token_expired`**: trigger token refresh or redirect to login.
- **`permission_denied`**: show an in-page 403 message; do not redirect.
- **`not_found`**: show a 404 page or inline empty state.
- **`server_error`**: show a toast with the `request_id` so users can report it to support.
- **Network errors**: show a retry toast.

Always surface the `request_id` in error UI so support can correlate with server logs.

---

## Environment variables

```bash
NEXT_PUBLIC_API_BASE_URL=https://api.authenticheck.com/api/v1
NEXT_PUBLIC_APP_ENV=production          # "development" | "staging" | "production"
```

Do **not** put secrets or private keys in `NEXT_PUBLIC_*` variables.

---

## Key constraints and copy guidelines

- The verification page copy must never say a product "is genuine" or "is authentic". Approved
  language: "This code is registered to [Manufacturer]", "This code has been used [N] times".
- Recalled batches must display a clear warning; do not soften the language.
- All timestamps are UTC from the API; convert to the user's local timezone in the UI layer.
- The platform (staff) section is invisible to non-staff users at the routing level — do not
  render staff navigation items for tenant users even when disabled.
- API keys and webhook secrets are shown exactly once. Use a modal with a "copied" confirmation
  and a "I have saved this" checkbox before allowing the modal to close.
