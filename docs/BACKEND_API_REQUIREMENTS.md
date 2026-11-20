# AuthentiCheck – Backend API Requirements (consolidated)

One living list of everything the frontend needs from the backend that `ENDPOINT_REFERENCE.md` does not yet give it, grouped by area. It is meant to be **kept and edited**, not rewritten each time.

- Last reviewed against `ENDPOINT_REFERENCE.md`: **2026-10-30**
- **Frontend: start with "Backend changes to adopt (2026-11-16)" below, a checklist of everything that changed, then "Backend changes to adopt (2026-11-20)" for the 12-character codes, QR and GS1 work.**
- Frontend: see also "staff sign-in with MFA is now available (2026-11-16)".
- Section 6 (public verification) updated **2026-11-05** after reading the backend source (`apps/issuance/tasks.py`, `apps/verification/views.py`): see 6.1 and 6.2.
- Frontend state at that date: **Products tab built** against the endpoints below. Batches & codes and Investigations are still on sample data; their detailed specs stay in their own docs and are indexed in [section 5](#5-batches--codes) and [section 8](#8-investigations).

---

## Backend changes to adopt (2026-11-16)

Everything below is **implemented, tested and on `main`**. Run `python manage.py migrate` against any database first (new migrations: identity 0005-0006, catalogue 0006, tenants 0007-0009). Items marked **Breaking** need a frontend change or the call fails.

| # | Change | Frontend action | Breaking? |
|---|---|---|---|
| 1 | **Company registration collects `industry`, `rc_number` (CAC) and `city`** on `POST /tenants/register`. Industries for the dropdown: `GET /tenants/industries` → `[{ key, label }]` (works before the user has a company). Errors: `error.details.industry / rc_number / city`. Returned on `GET /tenants/me` and on the platform tenant rows. | Add three fields to the registration form; populate the select from `/tenants/industries`. Tenants registered earlier have them blank: show "-". | **Yes**: registration returns `400` without them |
| 2 | **Reject and Request-info need a reason.** `POST /tenants/{id}/reject` and `/request-info` return `400` with `details.reason` when `reason` is blank. The reason is stored as `decision_reason` (+ `decided_at`) and shown to the applicant on `GET /tenants/me` and in the platform rows. Approve and suspend: `reason` optional (audit only) and clears `decision_reason`. | Make the reason box required for those two actions. Show `decision_reason` to the applicant while the status is `rejected` or `info_requested`. | **Yes**: a blank reason is rejected |
| 3 | **Tenant list filter is repeatable:** `GET /platform/tenants?status=pending&status=info_requested`. An unknown status returns `400`. | Use it for the "waiting" view; the whole queue now shows. | No |
| 4 | **Platform tenant rows** (list and detail) now include `industry`, `rc_number`, `city`, `owner_email`, `owner_name`, `brands: [{ name }]`, `submitted_at` (same value as `created_at` for now), `decision_reason`, `decided_at`. Owner is the earliest active owner membership (blank if none); brands are the active ones, sorted by name. | Replace the "-" placeholders in the Tenant applications page. | No |
| 5 | **Batch create and edit accept the frontend names:** `manufacture_date`, `expiry`, `declared_quantity`, `pack_format` (the original `manufactured_at`, `expires_at`, `quantity` still work). Responses and the `GET /catalogue/batches` rows return both sets. Dates `YYYY-MM-DD`. Errors are keyed by the name you sent. | Send and read the frontend names; drop the mapping workarounds. | No |
| 6 | **Duplicate batch number** for the same product returns `400 validation_error` with `details.batch_number` (it used to be a server error). | Show it under the batch number input. | No |
| 7 | **Staff sign-in with MFA** (`/auth/mfa/enroll`, `/enroll/confirm`, `/verify`) and the staff token keeps `otp` across refreshes. | See the section below. | n/a |
| 8 | **Session refresh keeps the active company** (`tid`) so company routes no longer return `403` after a page refresh (AUTH-5). | None; remove any workaround that re-logs in after a refresh. | No |
| 9 | **Batch attention markers.** Every batch (list, detail, create) has `attention: string[]` with any of `partial_job` (latest issuance job stopped partway), `unreconciled` (jobs finished but codes generated differs from requested) and `expiring_soon` (active, best-before within 60 days). `GET /catalogue/batches?needs_attention=true\|false` filters by it (anything else is `400`). | Drop the client-side derivation; show the chips from `attention` and restore the "Needs attention" filter. | No |
| 10 | **Tenant evidence checklist.** `GET /platform/tenants/{id}` has `evidence: [{ key, label, status, provided, detail, checked_at, checked_by, note }]` for `cac_certificate`, `brand_ownership`, `nafdac_registrations`, `signatory`, `support_contact`. `status` is `missing`, `manufacturer_provided` or `checked`. Staff tick an item with `PUT /platform/tenants/{id}/evidence/{key}` body `{ checked: true\|false, note? }`, which returns the full checklist. | Render the checklist in the review panel with a check box per item and a note. | No |
| 11 | **Operations at a glance:** `GET /platform/ops` → `verification { requests, errors, error_rate, p95_ms, scans_24h }` (last hour; `p95_ms` is a bucket upper bound, `null` with no traffic), `issuance { queue_backlog, failed_jobs }`, `tls_cert_expires_at` / `tls_days_remaining` and `last_restore_test_at` / `days_since_restore_test` (null until the deployment sets `TLS_CERT_EXPIRES_AT` / `LAST_RESTORE_TEST_AT`). | Build the panel; show "Not set" for the nulls. | No |
| 12 | **Staff authenticator reset.** `GET /platform/staff` lists staff with `mfa_enabled` and `is_you`. `POST /platform/staff/{id}/reset-mfa` body `{ reason }` (required) removes a colleague's authenticators and signs them out; at their next login they get `mfa_enrollment_required` and enrol again. You cannot reset yourself (`403 cannot_reset_own_mfa`); a sole staff user is reset by an operator with `python manage.py reset_staff_mfa <email>`. | Add a "Reset authenticator" action on a staff list, with a reason box and a confirmation. | No |
| 13 | **Duplicate CAC numbers.** Registration still succeeds (it never says a number is taken). Platform tenant rows and detail have `rc_duplicates: [{ id, legal_name, status }]` (other non-rejected tenants with the same `rc_number`). Approving such a tenant returns `409 rc_number_conflict` with `details.duplicates` until the request carries `rc_conflict_resolution` (free text, saved on the tenant and returned as `rc_conflict_resolution` on the detail). | Show a warning with links to the clashing tenants; on the `409`, ask for a resolution note and retry the approve with it. | **Yes**, for approving a tenant that has a duplicate |
| 14 | **Issuing codes is stricter.** `POST /issuance/jobs` (and resume) returns `409 over_issuance` (`details { declared_quantity, already_requested, remaining }`) when the batch's total requested codes would exceed its declared quantity, plus `409 product_suspended` and `409 product_archived`. `GET /issuance/codes?q=` ignores dashes, spaces and case. | Show the `over_issuance` message and offer "Raise declared quantity" (`PATCH` the batch), then retry. Show the suspended and archived messages. | **Yes**, for issuing beyond the declared quantity |
| 15 | **CORS now allows `Idempotency-Key`** (and exposes `Retry-After`), so issuing codes works from the browser. `evidence` rows carry `checked`; `/platform/ops` also returns `verify`, `scans_24h` and `tls { expires_at, days_remaining }`. | None, once the API is redeployed. | No |


## Backend changes to adopt (2026-11-20): 12-character codes, QR and GS1

**Implemented, tested and on `main`.** Run `python manage.py migrate` first (it converts existing codes). Details: [section 6.1](#61-product-code-format-blocker).

**Done on the backend**

| Change | Where |
|---|---|
| New codes are **12 characters** from a 30-symbol alphabet (`23456789ABCDEFGHJKMNPQRSTVWXYZ`) with a Luhn mod-30 check as the 12th character, generated with `secrets.choice`. Stored and returned canonical (upper-case, no dashes). | `apps/issuance/codes.py` |
| Codes issued before the change were converted; the old 22-character value still resolves through `legacy_code` (exact, case-sensitive match). | migration |
| One normalising helper (trim, upper-case, strip spaces and dashes) is used by verify, reports, deactivate, USSD and GS1. | VER-10, VER-12 |
| `GET /verify/{code}` returns `400 invalid_code_format` for input that cannot be a code, and does **not** record a scan. | VER-14 |
| **GS1 Digital Link** `GET /01/{gtin}/21/{serial}` now redirects (`302`) to the consumer page `{VERIFY_BASE_URL}/v/{code}` (code normalised) instead of the JSON endpoint. New setting `VERIFY_BASE_URL`, default `FRONTEND_URL`. | `apps/integrations/views.py` |

**Needed from the frontend**

| # | Action | Detail |
|---|---|---|
| 1 | **Update `lib/api/verify.ts`** to the new format: `CODE_LENGTH = 12`, the alphabet, `cleanCode()` (trim, remove spaces and `-`, upper-case, never substitute `0/O` or `1/I/L`) and `validateCode()` including the check character (sample in 6.1). Group codes as `XXXX-XXXX-XXXX` for display. | VER-8 to VER-11 |
| 2 | **Handle `400 invalid_code_format`** from `GET /verify/{code}` (show "That code isn't valid, check and try again"; nothing was recorded). | VER-14 |
| 3 | **Add the consumer route `/v/[code]`** that runs the check for that code. This is the path the QR codes and the GS1 redirect point to. Tell the backend if the route is named differently and the redirect and docs will follow. | QR payload, 6.1 |
| 4 | **Build "Scan QR"** with the browser camera. Accept only a URL on the verify domain (`/v/{code}` or `/01/{gtin}/21/{code}`) or a bare 12-character code; anything else is rejected without a lookup. Keep the typed-code input beside it as the fallback. | 6.1 |
| 5 | **Verify domain: decided for now.** The temporary test domain is `https://authenticheck-web.netlify.app` (`VERIFY_BASE_URL`), so QR payloads are `https://authenticheck-web.netlify.app/v/{code}`. **Do not print real labels on it:** switch to the permanent domain first, because the domain is printed on the label and cannot change afterwards. | 6.1 |
| 6 | **Batch code exports and print layouts** use the canonical code, grouped `XXXX-XXXX-XXXX`, with the printed code beside every QR. | 6.1 |

**Not built:** the backend does not generate QR images; the label printer or vendor builds the QR from the exported code or URL. Decision deferred (2026-11-20): whether to add a server-side QR or CSV export for batches; for now the printer builds the QR.

### Not built (do not build against these)

- `approval_pending` on batches: there is no deactivation-approval step yet (second approver is deferred), so it is never returned. Hide that chip.
- The recent privileged activity feed has no new endpoint: it is a frontend page over the existing `GET /platform/audit`.

---

## Frontend: staff sign-in with MFA is now available (2026-11-16)

**Frontend status (2026-11-16): built, untested against a live staff account.** Login branches on `mfa_required` / `mfa_enrollment_required`; `components/auth/MFASetup.tsx` (QR + manual key + confirm) and `MFAChallenge.tsx` (returning staff) handle the error codes below. Still to do: the staff review-queue shell checks (steps 4 and 5). The lost-authenticator reset endpoint now exists (see item 12 above).

**Action needed on the frontend: build the staff sign-in screens (QR code and 6-digit code).** Until now a platform staff user could not finish logging in through the API, so the product review queue could not be used. The backend is done and tested; only the screens are missing. Reference: `ENDPOINT_REFERENCE.md`, Auth section.

### Flow

```
POST /auth/login { email, password }
  └─ staff user ─▶ 200 { mfa_required: true, challenge_token, mfa_enrollment_required? }

  mfa_enrollment_required: true  (first time, no authenticator yet)
    POST /auth/mfa/enroll          { challenge_token }          ─▶ 201 { provisioning_uri, secret }
    show QR code (+ secret for manual entry), user adds it to Google Authenticator / Authy / 1Password
    POST /auth/mfa/enroll/confirm  { challenge_token, token }   ─▶ 200 login response

  otherwise (authenticator already set up)
    POST /auth/mfa/verify          { challenge_token, token }   ─▶ 200 login response
```

- **Login response** is the same as a normal login: `{ access_token, token_type, user }` plus the HttpOnly refresh cookie. The access token's `amr` is `["pwd","otp"]`. Treat it exactly like the normal login result (keep the token in memory, send it as `Authorization: Bearer`).
- `token` is exactly **6 digits, sent as a string** (keep leading zeros, so never parse it to a number).
- Non-staff users are unaffected: their login returns the access token directly, with no `mfa_required`.

### Screens to build

1. **Login**: after `POST /auth/login`, if `mfa_required` is true, keep `challenge_token` **in memory only** (not localStorage) and go to step 2 or 3. Do not call anything else with it.
2. **Set up authenticator** (when `mfa_enrollment_required` is true):
   - Call `/auth/mfa/enroll` on arrival and render `provisioning_uri` as a **QR code** (client-side, e.g. the `qrcode` or `qrcode.react` package; never send the URI to a third-party QR service, it contains the secret).
   - Under the QR code show `secret` as a "Can't scan? Enter this key" fallback, grouped in fours.
   - A 6-digit input and a "Confirm" button calling `/auth/mfa/enroll/confirm`.
   - Calling `/auth/mfa/enroll` again returns a **new** secret and invalidates the previous one, so call it once per visit and not on every re-render (React strict mode double-calls in development will show a different QR than the one that is scanned).
3. **Enter code** (returning staff): one 6-digit input (`inputMode="numeric"`, `autocomplete="one-time-code"`), calling `/auth/mfa/verify`. Submit automatically on the sixth digit if you like.
4. After success, route to the staff area (product review queue, below).

### Errors to handle

| Status | `error.code` | Meaning | What to show |
|---|---|---|---|
| 401 | `invalid_mfa_code` | Wrong or expired code (the challenge is still usable) | "That code did not work. Use the latest code from your app." Keep the input, clear it, allow retry. |
| 401 | `challenge_invalid` | Challenge expired (5 minutes), already used, or burned after **5 wrong codes** | "This sign-in step expired." Send the user back to the login form. |
| 409 | `enrollment_not_started` | `enroll/confirm` called before `enroll` | Call `/auth/mfa/enroll` first. |
| 400 | `validation_error` | `token` is not 6 digits | Inline field error. |
| 429 | `throttled` | Too many attempts (10 per 15 minutes per IP) | "Too many attempts, try again in a few minutes" using `Retry-After`. |

Also: the login endpoint itself is rate limited (5 attempts per 15 minutes per email and IP), so repeated test logins can return `429` for a while.

### After sign-in

- **Refresh keeps MFA.** `POST /auth/refresh` returns a token that still has `otp` in `amr`; no second code is needed until the session ends. If the session ends (`401`), the user goes through login and the code again.
- **Staff routes** all require the `otp` claim. A staff token without it gets `403` on `/platform/*`; if you see that, send the user to login.
- **Product review queue** (this is what the Product Owner needs to test):
  - `GET /platform/products` lists products waiting for review (`?status=in_review` is the default; `?tenant_id=` filters). Each row has `tenant_id` and `tenant_name`.
  - `POST /platform/products/{id}/approve` (409 `gtin_conflict` needs a `gtin_conflict_resolution` text and a retry)
  - `POST /platform/products/{id}/request-changes` and `POST /platform/products/{id}/reject`, both with a required `review_note`.
  - Approving a product only makes it visible to consumers. It does not unlock pharma, batch approval or code issuance.
- A user's `is_staff` flag is set by an existing staff user or by `createsuperuser`; there is no self-service route to become staff.

### Suggested order of work

1. Login: branch on `mfa_required` and `mfa_enrollment_required`.
2. Enrolment screen with the QR code and the confirm step (test with a real authenticator app).
3. Verify screen for returning staff, plus the error states above.
4. Staff area shell with the product review queue and the approve / request changes / reject actions.
5. Staff list with a "Reset authenticator" action (`GET /platform/staff`, `POST /platform/staff/{id}/reset-mfa`). A staff member cannot reset themselves.

---

### Fields the backend still needs (to complete the frontend)

Verified against the code on 2026-11-16. Each item says what the frontend reads and what the API gives today.

**Tenant applications (`GET /platform/tenants`, `GET /platform/tenants/{id}`)**

| Field the frontend reads | API today | Needed to supply it |
|---|---|---|
| `id`, `status`, `legal_name`, `trading_name`, `created_at` | Returned | – |
| `decision_reason`, `decided_at` | Returned (new) | – |
| `industry` | Returned (new). Required at registration; a key from `GET /tenants/industries`. Tenants registered earlier have it blank | – |
| `rc_number` (CAC number) | Returned (new). Required at registration; stored upper-case with no spaces or dashes | – |
| `owner_email`, `owner_name` | Returned (new): the earliest active member with the owner role; blank when none | – |
| `city` | Returned (new). Required at registration | – |
| `brands: [{ name }]` | Returned (new): active brands, sorted by name | – |
| `submitted_at` | Returned (new), currently the same value as `created_at` | A separate field only if resubmission after "info requested" should reset it |

**Registration form (frontend action).** `POST /tenants/register` now requires `industry`, `rc_number` and `city` besides `legal_name` (and optional `trading_name`). Populate the industry select from `GET /tenants/industries` (`[{ key, label }]`; use `key` as the value). That endpoint works for a signed-in user with no company yet; `/catalogue/industries` does not. Show field errors from `error.details.<field>`. The same three fields are returned on `GET /tenants/me`. Tenants registered before this change have them blank (show "-").

**Tenant review extras (PLT-11)**, not built on the backend: evidence checklist (CAC certificate, brand ownership, NAFDAC registrations, signatory, support contact), operations at a glance (verification p95, error rate, failed jobs, queue backlog, TLS expiry, last restore test), recent privileged activity (`GET /platform/audit` exists, so this one only needs the frontend page). Optional; say which ones the review panel must have.

**Batches (`GET /catalogue/batches`, BCH-12)**

| Field | API today |
|---|---|
| `id`, `batch_number`, `product_id`, `product_name`, `status`, `declared_quantity`, `issuance { requested, generated, failed, deactivated }` | Returned |
| `manufacture_date`, `expiry`, `pack_format` | Returned (new). `expires_at` and `manufactured_at` are kept as aliases |
| `attention[]` and a `needs_attention` filter | **Not built.** The frontend derives *Partial* (failed > 0) and *Expires soon* (60 days) itself. *Unreconciled* and *Approval pending* need the server |

**Create batch (`POST /catalogue/products/{id}/batches`, BCH-11)**

Send `{ product_id, batch_number, manufacture_date?, expiry?, declared_quantity, pack_format? }`, dates as `YYYY-MM-DD`. Field errors come back under `error.details.<field>`, keyed by the name you sent (`expiry`, `manufacture_date`, `batch_number`, `declared_quantity`).

---

## How to use this document

| Field | Meaning |
|---|---|
| **ID** | Stable reference (`PRD-3`). Never reuse an ID; refer to it in tickets and PRs. |
| **Priority** | **Required**: the frontend cannot ship the feature without it. **Optional**: improves the experience; the "Without it" column says what the frontend does instead. **Decision**: a product or policy answer the backend needs first. |
| **Status** | `Open` (not built) · `Confirm` (frontend built against an assumption, backend please confirm or correct) · `Done` (verified in `ENDPOINT_REFERENCE.md` or against a running API). |

**Updating it**

1. When the backend ships something, set the row to `Done`, note the date, and move on. Don't delete rows.
2. When the frontend starts a new tab, add a section here and link its detailed doc (if any).
3. When a response shape in `lib/api/catalogue.ts` is changed, update [section 9](#9-frontend-assumptions-register).
4. Re-read `ENDPOINT_REFERENCE.md` first; strike anything it already covers.

**Caveat.** `API_CONTRACTS.md` is not in the frontend repo, so "exists today" is judged from `ENDPOINT_REFERENCE.md` alone. If the contracts already answer a row, mark it `Done`. The fastest way to remove most `Confirm` rows is [XC-1](#7-cross-cutting).

**Errors** use the existing envelope `{ "error": { "code", "message", "details", "request_id" } }`. **Times** are ISO 8601 UTC; the frontend renders West Africa Time.

---

## 1. Auth, roles and session

| ID | Requirement | Priority | Status | Without it |
|---|---|---|---|---|
| AUTH-1 | `GET /auth/me` returns `can_submit_products` on each membership (and `role`, already present). The Submit button for an `editor` depends on it; `GET /tenants/me/members` is 👑-only so editors can't read their own flag. | **Required** | Open | Editors see Submit and get a `403` when it isn't enabled. |
| AUTH-2 | Production / Quality lead and Investigator roles, with the permission table agreed (see `BATCHES_CODES_API_REQUIREMENTS.md` §9 and `INVESTIGATIONS_API_REQUIREMENTS.md`). `PATCH /tenants/me/members/{id}` must accept them. | **Required** (Decision) | Done (per `ENDPOINT_REFERENCE.md`, 2026-11-16): `production_quality`, `investigator`, `viewer`; legacy `editor` / `member` map to `production_quality`. Frontend updated. | Only `owner`, `admin`, `editor` are modelled; batches and investigations stay owner/admin. |
| AUTH-3 | Switch the active company for a user with several memberships (the sidebar has a switcher). Needs an endpoint that re-issues the access token with another `tid`, e.g. `POST /auth/switch-tenant { tenant_id }`. | Optional | Open | The frontend uses the first membership only. |
| AUTH-4 | Return the current user's `id` in `/auth/me` and `submitted_by` / `created_by` (id + name) on products, so "submitter or admin" can withdraw is decidable in the UI (see PRD-12). | **Required** | Confirm | Withdraw is shown to every ✏️ user; the API answers `403` for the rest. |
| AUTH-5 | `POST /auth/refresh` must return an access token carrying the same tenant (`tid`) as the login token: store the active tenant on the refresh session at login and re-issue it (or fall back to the user's first active, approved membership). Observed: after a page refresh the frontend loses its in-memory token, calls `/auth/refresh`, and `/auth/me` works with the new token, but every 🏢 route (`/catalogue/products`, `/catalogue/batches`, `/analytics/scans`, `/analytics/codes`, ...) answers `403 permission_denied` "A valid X-Tenant-ID header with an active membership is required." Decode the refresh token's access JWT and compare with the login one to confirm `tid` is missing. If an `X-Tenant-ID` header is the intended mechanism instead, say so and add it to the CORS allowed headers; the frontend does not send it today. | **Required** | Done 2026-11-16: `AuthSession.active_membership` is set at login (single membership) and `/auth/refresh` re-issues `tid`/`mid` from it, falling back to the first active membership in an approved tenant (`session_service.resolve_session_membership`). No `X-Tenant-ID` header needed. | Every page refresh (and every silent token renewal) breaks all company data until the user logs in again. |

---

## 2. Tenant (company)

| ID | Requirement | Priority | Status | Without it |
|---|---|---|---|---|
| TEN-1 | `GET/PATCH /tenants/me/support-contact` | Optional | **Done** | – |
| TEN-2 | Enforce that a support **email** exists before a tenant goes live (PO decision 8). Return a clear `409`/`422` code (e.g. `support_contact_required`) from tenant approval or first code issuance, and expose `support_contact_complete: bool` on `GET /tenants/me` so the dashboard can prompt. | **Required** | Open ("not enforced yet") | Consumers may see products with no way to contact the company. |
| TEN-3 | Confirm the shape of `GET /tenants/me/release-gates` (the frontend assumes `{ cosmetics: { status }, pharmaceuticals: { status, items: [{ key, label, state }] } }`, `state` ∈ `not_started \| in_progress \| done`). | Optional | Confirm | The product form lists gate items only when this shape matches. The dashboard's Release gates card is still hard-coded. |
| TEN-4 | `GET /tenants/me/members` readable by non-admins in a minimal form (`id`, `name`) so actors in activity logs and "submitted by" can be named for everyone. | Optional | Open | Names come from the activity payload only. |
| TEN-5 | `GET /tenants/me` should return `support_contact_complete` and `go_live` state (see TEN-2). | Optional | Open | – |
| TEN-6 | Confirm the shape of `GET /tenants/me/members` rows. The Team page assumes `{ id, full_name, email, role, can_submit_products, created_at }`; `name` is accepted as an alias for `full_name`. | **Required** | Confirm | Names, emails or the submit switch may render blank. |
| TEN-7 | Invitations so a second person can join an existing company: create (email + role), list, resend, revoke, and accept by single-use expiring token. The accepting user must sign in with the invited email and is added with that role, skipping company registration and approval. | **Required** | Open | Anyone who signs up can only register a new, separate company. |
| TEN-8 | Remove or deactivate a member (`DELETE` or `PATCH is_active=false` on `/tenants/me/members/{id}`). The owner cannot be removed; removed members lose access immediately. | **Required** | Open | There is no way to offboard someone. |

---

## 3. Products

Endpoints used by the Products tab: all of `Catalogue — Products`, `Catalogue — Brands`, `GET /catalogue/products/{id}/batches`, `GET/PATCH /tenants/me/support-contact`, `GET /tenants/me/release-gates`, and the staff `Platform` product endpoints. Everything below is what is **still missing or unconfirmed**.

### 3.1 Missing fields and endpoints

| ID | Requirement | Priority | Status | Without it |
|---|---|---|---|---|
| PRD-1 | `brand_name` on list and detail rows (and `reference_image_url`, the public/signed URL of the attached image, on detail). | **Required** | Confirm | Brand column shows "—"; the edit form shows "Attached" instead of a thumbnail. |
| PRD-2 | Status counts for the Products KPIs: `status_counts` on the list response (`{ draft, in_review, changes_requested, approved, rejected }`) or `GET /analytics/catalogue`. Counts respect `is_active=true`. | Optional | Open | Three extra `page_size=1` list calls on every load of the list page. |
| PRD-3 | `batch_count` (and ideally `codes_issued`) on **list** rows, not just detail. | Optional | Confirm | The Batches column shows "—". |
| PRD-4 | Live duplicate pre-check: `GET /catalogue/products/check-duplicate?sku=&name=&brand_id=&variant=&size_value=&size_unit=&exclude_id=` → `{ sku_conflict, similar[] }`. Rate-limited. | Optional | Open | The duplicate banner appears only on save (`409`). |
| PRD-5 | Submitter and reviewer detail on the product: `created_by`, `submitted_by`, `submitted_at`, `reviewed_by`, `reviewed_at` (id + name + time). | Optional | Open | Detail page shows created/updated times only. |
| PRD-6 | Batch rows returned by `GET /catalogue/products/{id}/batches` include `manufacture_date` and `expiry` (see BCH-2). | **Required** | Open | Manufactured column shows "—". |

### 3.2 Behaviour to confirm

| ID | Question | Frontend assumption | Status |
|---|---|---|---|
| PRD-7 | Validation errors: `400` (ENDPOINT_REFERENCE) or `422` (`PRODUCTS_API_REQUIREMENTS.md`)? | Both are read the same way: `details` keyed by field, mapped onto form fields. | Confirm |
| PRD-8 | `details` keys for nested/grouped fields. | `size` or `size_value`/`size_unit` → Size; `regulatory_reference.number` → Registration number; `support_contact.email|phone|url` → their inputs. Unknown keys are shown as a summary. | Confirm |
| PRD-9 | Clearing a field on `PATCH`. | Sending `null` clears an optional field (`variant`, `gtin`, `reference_image_id`, `support_contact`, `regulatory_reference`). The form only sends fields that changed. | Confirm |
| PRD-10 | Nested `regulatory_reference` on `PATCH`. | It creates or updates **the product's one reference** (and resets verification if `number`/`authority` changed). With several references on a product, behaviour is undefined: please state it. | Confirm |
| PRD-11 | `PATCH` on an `approved` product with consumer-visible changes. | Returns the product with `status: "in_review"` and `pending_revision` set; live fields unchanged. No separate submit call. | Confirm |
| PRD-12 | `POST .../withdraw` permission is "submitter or 👑". | See AUTH-4. | Confirm |
| PRD-13 | Archive / restore via `PATCH { is_active }`. | Allowed for any status except `in_review`; restore returns the previous status. Archived SKUs stay reserved. | Confirm |
| PRD-14 | `correction_request` read shape on detail. | `{ field: "sku"\|"gtin", requested_value, reason }` while pending, else `null`. Also: what does `POST .../correction-request` return, and can there be only one open request? | Confirm |
| PRD-15 | `gtin_conflict` read shape on detail. | `true` / `false`, or `{ product_id, name }` of the conflicting product (name shown only if the caller may see it; never another company's product). | Confirm |
| PRD-16 | `size` is read-only and generated from `size_value` + `size_unit`. | Form sends the two parts; the displayed string comes from `size`. Category `allowed_units` restricts the unit list. | Confirm |
| PRD-17 | `sku` normalisation. | Frontend upper-cases and trims; server returns the stored form. Pattern `[A-Z0-9_-]{3,64}`. | Confirm |
| PRD-18 | Which fields count as "consumer-visible" (go to `pending_revision`) vs "internal" (save immediately)? | Internal: `description` only. Everything else in the form is consumer-visible. Please publish the list; the UI wording depends on it. | Confirm |
| PRD-19 | Reference image detach. | Setting `reference_image_id: null` detaches it. `DELETE /catalogue/images/{id}` is called only for images uploaded in the current form session and not yet attached. | Confirm |
| PRD-20 | Upload response and lifecycle. | `201 { id, url, width, height }`; unattached uploads expire (~24 h). Errors `413 file_too_large`, `415 unsupported_media_type`, `422 image_invalid`. | Confirm |
| PRD-21 | CSV import (`POST /catalogue/import`) for products. | Imported rows use the same fields (`size_value`, `size_unit`, `category` slug, `industry`), arrive as `draft`, never skip review, and a row-level error report is available from `GET /catalogue/import/{id}`. Please document the column names. | Open |

### 3.3 Response shapes the UI parses defensively

These are read as "array **or** `{ results }`" so either works, but one should be chosen and documented.

| ID | Endpoint | Assumed shape |
|---|---|---|
| PRD-22 | `GET /catalogue/industries` | `[{ key, label, description?, available, required_fields: string[], categories: [{ key, label, allowed_units?: string[] }] }]`. `required_fields` uses the product field keys (`brand_id`, `variant`, `size`, `category`, `sku`, `dosage_form`, `strength`, `pack_configuration`). `size` stands for `size_value` + `size_unit`. |
| PRD-23 | `GET /catalogue/products/{id}/activity` | `{ results: [{ id?, at, actor: { id, name }, action, changes?: [{ field, from, to }], before?, after?, note? }] }`. `action` is dotted (`product.submitted`). Reason text from withdraw, reject, suspend and correction goes in `note`. |
| PRD-24 | `GET /catalogue/products/{id}/consumer-preview` | Same payload `/verify` would return for this product's **last approved** values with a sample batch (see VER-3). |
| PRD-25 | `GET/POST .../regulatory-refs` | `{ id, authority, number, source, verification_status, verified_at?, verification_method?, verification_note? }`. `source` ∈ `entered_by_manufacturer \| imported \| verified_by_reviewer`. |
| PRD-26 | `GET /catalogue/products` | `{ count, next, previous, results, status_counts? }` |
| PRD-27 | `GET /catalogue/products/{id}/batches` | Paginated or plain array of `{ id, batch_number, manufacture_date?, expiry?, status }`. |

### 3.4 Brands

| ID | Requirement | Priority | Status | Without it |
|---|---|---|---|---|
| BRD-1 | Brand create is 👑 only, but editors register products. State the intended flow: either allow ✏️ to create a brand, or accept that editors pick from existing brands. | Decision | Open | "New brand" shows for owner/admin only. |
| BRD-2 | `GET /catalogue/brands` supports `include_inactive` and pagination (the filter on the Products list uses all brands). | Optional | Open | Only active brands are filterable. |

---

### 3.5 Imports (`/catalogue/import`)

The Imports page is built against the two documented paths; everything else below is assumed or missing.

| ID | Requirement | Priority | Status | Without it |
|---|---|---|---|---|
| IMP-1 | Document the CSV columns per `import_type` (`brands`, `products`, `batches`): required fields, allowed values (industry, `size_unit`, category slug), date format, and the order brands → products → batches. | **Required** | Open | Users can't prepare a file. |
| IMP-2 | Confirm the request and response. The UI sends multipart `import_type` + `file`, and reads `{ id, import_type, status: pending\|processing\|done\|failed, total_rows, processed_rows, created, skipped, failed, errors: [{ row, column, message }], message }`. | **Required** | Confirm | Progress, counts or row errors may render empty. |
| IMP-3 | Partial-success rule: does one bad row fail the whole file, or are good rows imported and bad rows skipped? The UI wording ("Imported, with some rows to fix") assumes the latter. | **Required** (Decision) | Open | Result copy may be wrong. |
| IMP-4 | Validate-only mode (or dry run) that returns row errors without writing, so users can fix a file before importing. | Optional | Open | Users learn about errors only after rows were created. |
| IMP-5 | `GET /catalogue/import` (history: date, type, file name, status, counts, who) and a downloadable CSV template per type. | Optional | Open | No history; users build files from the docs. |
| IMP-6 | State limits (file size, rows; the UI assumes 10 MB) and make a repeat upload of the same file safe (idempotency or duplicate warning). | **Required** | Open | Double uploads create duplicates; the size check is a guess. |

---

### 3.6 Settings (company, API keys, webhooks, security)

Built so far: Company (support contact) and API keys. Webhooks and Security are not built yet.

| ID | Requirement | Priority | Status | Without it |
|---|---|---|---|---|
| SET-1 | Confirm the support-contact field names. `ENDPOINT_REFERENCE.md` says `support_email`, `support_phone`, `support_url`; the Settings page writes those and reads either those or `email`/`phone`/`url`. | **Required** | Confirm | The form may save nothing or load blank. |
| SET-2 | Confirm the `GET/POST /api-keys` shapes. The UI reads `{ id, name, prefix, created_at, last_used_at, expires_at, revoked }` and the raw secret from `key` on create (also `raw_key`, `secret`, `token`). It sends `{ name, expires_at? }`. | **Required** | Confirm | The key list or the one-time secret may render empty. |
| SET-3 | API key scopes (for example read-only, issue-only) and an optional IP allow-list. | **Required** (Decision) | Open | Every key has full admin power; a leaked key is a leaked account. |
| SET-4 | `PATCH /tenants/me` to edit company details (trading name, address, logo), with an audit entry. | Optional | Open | Company name can't be changed in the app. |
| SET-5 | Webhooks: publish the event-type list, add a "send test event" and "redeliver" action, and secret rotation. | **Required** for the Webhooks page | Open | The events picker can't be built from data. |
| SET-6 | Security: change password, change email, list and revoke sessions, disable MFA, MFA recovery codes. | **Required** | Open | Users can enrol in MFA but not recover or leave it. |
| SET-7 | Decision: is MFA mandatory for owners and admins? | **Required** (Decision) | Open | Changes onboarding and the Security page. |
| SET-8 | Notification preferences (who gets flag, approval and import emails). | Optional | Open | No control over email volume. |

---

## 4. Platform review (staff)

The review queue at `/platform/products` exists on the frontend. It needs more than the queue endpoint returns.

| ID | Requirement | Priority | Status | Without it |
|---|---|---|---|---|
| PLT-1 | Queue rows include `tenant_name`, `submitted_at`, `submitted_by`, `brand_name`, `pending_revision`, `correction_request`, `gtin_conflict`. | **Required** | Confirm | Rows show fewer details; the reviewer can't tell a revision from a first submission except by `pending_revision`. |
| PLT-2 | A staff product detail: `GET /platform/products/{id}` returning the full field snapshot, a **diff against the approved version**, reference image URL, regulatory references (with ids), the activity log, and the tenant's support contact. `GET /catalogue/products/{id}` is 🏢-only, so staff can't read it today. | **Required** | Open | The reviewer decides from the queue row alone. The "Review details" panel shows the row's own fields and the pending revision (live vs proposed), but no regulatory references, activity or tenant support contact. |
| PLT-3 | Staff-readable activity (`GET /platform/products/{id}/activity` or covered by PLT-2). | **Required** | Open | – |
| PLT-4 | Body of `POST /platform/products/{id}/correct`: `{ field, new_value, reason }`. | Confirm | Confirm | The frontend mirrors the tenant's `correction-request` body with `new_value`. |
| PLT-5 | Body of `POST .../regulatory-refs/{ref_id}/verify`: `{ method, note? }` plus an optional `checked_at` (default now). | Confirm | Confirm | A "Verify" action is not yet in the review UI (needs PLT-2 for ref ids). |
| PLT-6 | `gtin_conflict_resolution` format: free text, or `{ action: "allow_duplicate"\|"..." , note }`? Also: a conflict preview (`conflicting_product` summary) so the reviewer can see what clashes. | Confirm | Confirm | Frontend sends a free-text string. |
| PLT-7 | Notify the submitter on a decision (email and/or webhook event `product.approved`, `product.changes_requested`, `product.rejected`). | Optional | Open | Users find out when they open the product. |
| PLT-8 | Queue filters: `tenant_id`, `industry`, `has_pending_revision`, `ordering` (oldest first is the default). | Optional | Open | – |
| PLT-9 | Shape of `GET /platform/tenants` rows and filters. The Tenant applications page (`/platform/tenants`) reads `{ count, results: [{ id, status, legal_name, trading_name, industry, rc_number, owner_email, owner_name, city, brands: [{name}], created_at, submitted_at }] }` (a plain array also works) and sends `status` **repeated** (`?status=pending&status=info_requested`) for the "waiting" view. Please confirm the names, or accept a repeatable `status`. | **Required** | **Partly done 2026-11-16.** Repeatable `status` is done (unknown value returns `400`), and rows now carry `decision_reason` and `decided_at`. `owner_email`, `owner_name`, `brands` and `submitted_at` are now returned too (list and detail). `industry`, `rc_number` and `city` are now collected at registration and returned on list and detail (done 2026-11-16, see below). **Frontend: the registration form must now send them, or `POST /tenants/register` returns `400`.** | Missing fields show "—"; if only one `status` is accepted the "waiting" view shows only part of the queue. |
| PLT-10 | Body of `POST /tenants/{id}/approve`, `/reject`, `/request-info`, `/suspend`. The frontend sends `{ "reason": "..." }` (required in the UI, audited). Please confirm the key (`reason` vs `review_note`) and that `reject` / `request-info` reasons reach the applicant. | **Required** | **Done 2026-11-16.** The key is `reason`. `reject` and `request-info` now **require** it (`400` with `details.reason` if blank); it is saved as `decision_reason` plus `decided_at` and returned on `GET /tenants/me` and the platform tenant rows, so the applicant sees it. `approve` and `suspend` keep it optional (audit only) and clear `decision_reason`. | A wrong key returns `400`; the decision is not recorded. |
| PLT-11 | The design's tenant-review extras have no endpoint yet, so they are not shown: the **evidence checklist** (CAC certificate, brand ownership, NAFDAC registrations, signatory, support contact: `Checked` / `Manufacturer-provided`), **operations at a glance** (verification p95, error rate, failed jobs, queue backlog, TLS expiry, last restore test), **recent privileged activity** (`GET /platform/audit` exists but the page isn't built), and a pending-application count for the sidebar badge (the frontend uses `count` from the list). | Optional | **Done 2026-11-16.** Evidence checklist (derived data plus a staff tick), `GET /platform/ops` (verify latency and error rate measured by middleware, queue backlog, failed jobs, TLS expiry and last restore test set by the deployment). The activity feed is a frontend page over `GET /platform/audit`. The pending-application badge uses `count` from the list. | The review panel shows only company details and the decision form. |
| PLT-12 | Update 2026-11-17: the reference now covers the **evidence checklist** (`GET /platform/tenants/{id}` `evidence`, `PUT /platform/tenants/{id}/evidence/{key}`), **`rc_duplicates`** and the **`409 rc_number_conflict`** approval override (`rc_conflict_resolution`). The Tenant applications panel uses all three. The frontend reads `evidence` as either an array of `{ key, checked, note? }` or an object keyed by item; please confirm which. | Optional | **Answered and done 2026-11-17.** `evidence` is an **array** of `{ key, label, checked, status, provided, detail, checked_at, checked_by, note }`. `checked` is the boolean to tick the box from; `status` is `checked`, `manufacturer_provided` or `missing`. `PUT .../evidence/{key}` returns the same array. | The checklist may show every item unticked if the shape differs. |
| PLT-13 | Field names for `GET /platform/ops` and `GET /platform/staff`, which the reference describes only in prose. The Operations health and Staff pages read, for ops: `verify.requests`, `verify.errors`, `verify.error_rate`, `verify.p95_ms`, `scans_24h`, `issuance.queue_backlog`, `issuance.failed_jobs`, `tls.expires_at`, `last_restore_test_at` (flat names like `verify_p95_ms` and `tls_cert_expires_at` also work; a missing value shows "—"); for staff: `id`, `full_name` (or `name`), `email`, `mfa_enabled`, `is_you`. `reset-mfa` is sent `{ "reason": "..." }`. Please confirm the names, the error-rate unit (fraction or percent; the page works it out from `errors` / `requests` when both are sent), and that a staff row carries `email`. | Optional | **Answered and done 2026-11-17.** `GET /platform/ops` returns `verify { requests, errors, error_rate, p95_ms, scans_24h }` (also under the older name `verification`), top-level `scans_24h`, `issuance { queue_backlog, failed_jobs }`, `tls { expires_at, days_remaining }` (flat `tls_cert_expires_at` / `tls_days_remaining` also sent) and `last_restore_test_at` / `days_since_restore_test`. `error_rate` is a **fraction** (0.0123 = 1.23%), `null` when there was no traffic; `p95_ms` is a histogram bucket upper bound. `GET /platform/staff` rows: `id`, `email`, `full_name`, `is_active`, `mfa_enabled`, `is_you`. `reset-mfa` takes `{ reason }` (required). | Tiles show "—"; staff rows show a name only. |

---

## 5. Batches & codes

The Batches & codes tab is still on sample data (`lib/mock/batches.ts`). The full specification lives in **`BATCHES_CODES_API_REQUIREMENTS.md`**; this table is the index, plus the items the Products work added. Update the status here when that doc's items ship.

| ID | Requirement | Priority | Status | Detail |
|---|---|---|---|---|
| BCH-1 | Batches across all products: `GET /catalogue/batches`, `GET /catalogue/batches/{id}` | **Required** | Open | Batches doc §2.1 |
| BCH-2 | New batch fields `manufacture_date`, `declared_quantity`, `pack_format` | **Required** | Open | §2.2 |
| BCH-3 | Per-batch issuance counts (requested, generated, failed, exported, deactivated) | **Required** | Open | §3 |
| BCH-4 | Job `partial` status, failed count, started by/at, resume endpoint | **Required** | Open | §4 |
| BCH-5 | Label reconciliation read/update | **Required** | Open | §5 |
| BCH-6 | Print exports as an entity (checksum, re-download, download log, PDF) | **Required** | Open | §6 |
| BCH-7 | Deactivation requests with reason and second approver | **Required** | Open | §7 |
| BCH-8 | Codes list: cursor pagination, search, export/range filters | **Required** | Open | §10.1 |
| BCH-9 | Server-computed attention markers, per-batch scans, history | Optional | Open | §2.3, §10.2, §10.3 |
| BCH-10 | **Gating rule, written down.** A batch can be created and codes issued only when the product is `approved`, **not** suspended, and active. Error codes: `409 product_not_approved`, `409 product_suspended`, `409 product_archived`. Say what happens to existing batches when a product is suspended (codes stay issued but `/verify` returns 404) or when a revision is in review (nothing changes). | **Required** | **Done 2026-11-17.** Creating a job (and resuming one) returns `409 batch_not_active`, `409 product_not_approved`, `409 product_suspended` (platform suspension) or `409 product_archived` (`is_active=false`). Existing batches are untouched when a product is suspended or a revision is in review. | Products doc §3 |
| BCH-11 | Confirm the **create-batch body** the new form (`/issuance/batches/new`) sends to `POST /catalogue/products/{product_id}/batches`: `{ product_id, batch_number, manufacture_date, expiry, declared_quantity, pack_format? }` (dates `YYYY-MM-DD`). `ENDPOINT_REFERENCE.md` says `expires_at` in one place and the types say `expiry`; the form sends `expiry` and maps `expires_at` / `best_before` / `quantity` errors back to the right inputs. Also confirm: `pack_format` is accepted (or ignored) today, `batch_number` uniqueness error is a `validation_error` keyed `batch_number`, and the response includes `id`. | **Required** | **Done 2026-11-16.** The create and patch bodies accept `manufacture_date`, `expiry`, `declared_quantity` and `pack_format`, as well as the original `manufactured_at`, `expires_at` and `quantity`. Errors are keyed by the name the client sent. A duplicate `batch_number` for the product now returns `400 validation_error` with `details.batch_number` (before, it was a database error). The response includes `id`. `pack_format` is stored (free text, max 100). | A wrong key returns `400`/`422` and the form shows the server's field errors; `pack_format` is only sent when filled. |
| BCH-12 | The Batches & codes list now reads `GET /catalogue/batches` (`q`, `status`, `page`, `page_size`). Row fields read: `id`, `batch_number`, `product_id`, `product_name`, `manufacture_date`, `expiry` (or `expires_at` / `best_before`), `declared_quantity` (or `quantity`), `pack_format`, `status`, `issuance { requested, generated, failed, deactivated }`, and optionally `attention[]` (`partial_job`, `unreconciled`, `expiring_soon`, `approval_pending`). Without `attention` the UI derives only *Partial* (failed > 0) and *Expires soon* (≤ 60 days); *Unreconciled* and *Approval pending* need the server. The "Needs attention" filter sends `needs_attention=true`. | **Required** | **Done 2026-11-16** except `approval_pending`. `attention[]` (`partial_job`, `unreconciled`, `expiring_soon`) is on every batch response and `needs_attention=true|false` filters the list. `approval_pending` needs a deactivation-approval step that does not exist yet, so it is never returned. | Missing fields show "—"; attention chips are incomplete. |
| BCH-13 | The batch detail page now issues codes and tracks jobs. It sends `POST /issuance/jobs` `{ batch_id, quantity }` with an `Idempotency-Key` (a new UUID per submission, reused on a retry after a network failure), lists `GET /issuance/jobs?batch_id=`, polls it every 3 s while a job is `pending` or `processing`, calls `POST /issuance/jobs/{id}/resume` for `partial` jobs, and downloads `GET /issuance/jobs/{id}/export` for `done` jobs. Job fields read: `id`, `status`, `quantity_requested`, `quantity_generated` (or `quantity_issued`), `quantity_failed`, `started_at`, `started_by.name`. Please confirm: the `POST` response is the job (not wrapped); an `over_issuance` `409` exists (see `BATCHES_CODES_API_REQUIREMENTS.md` §4.4) or issuing more than the declared quantity is allowed; `production_quality` may create and resume jobs. | **Required** | **Answered and done 2026-11-17.** The `POST` response is the job, unwrapped, status `202`. `GET /issuance/jobs?batch_id=` is a plain array (not paginated); every field listed is returned (`started_at`, `started_by: { id, name }`, `quantity_generated`, `quantity_failed`). Resume of a running job returns that job; of a non-resumable one `409 job_not_resumable`; export of a job that is not `done` is `400 validation_error`. `production_quality` (with owner and admin) can create and resume jobs. **Over-issuance is rejected:** `409 over_issuance` with `details { declared_quantity, already_requested, remaining }` when a batch's total requested codes would exceed its declared quantity (a failed job that made no codes does not count; declared quantity 0 means unlimited). To issue more, raise `declared_quantity` with `PATCH` first (audited). Also `409 product_suspended` and `409 product_archived`. **The browser also needs XC-15 (CORS) to send the `Idempotency-Key` header.** | Issuing, progress and resume may show wrong counts or generic errors. |
| BCH-14 | The live batch page (owner/admin only) calls three endpoints whose request bodies the reference doesn't give. The frontend sends `{ "reason": "..." }` (required in the UI) to `POST /catalogue/products/{product_id}/batches/{id}/recall`, `POST /issuance/batches/{batch_id}/deactivate` and `POST /issuance/codes/{code}/deactivate`, and finds a code with `GET /issuance/codes?batch_id=&q=&page_size=10` (rows read as `code_str` or `code`, and `status`). Please confirm the body key and that the reason is audited. These act immediately with no second approver; the design's approval flow and *recall vs withdraw* are still open (`BATCHES_CODES_API_REQUIREMENTS.md` §2.4, §7). | **Required** | **Answered 2026-11-17.** All three take `{ reason }`; it is optional on the API (max 500 characters) and audited, so keep it required in the UI. Recall also accepts an optional `consumer_message` (shown to consumers). `GET /issuance/codes?batch_id=&q=&page_size=` works: rows have `code` (not `code_str`) and `status`; `q` ignores dashes, spaces and case (`7KQ2-M9XW-4PRT` finds `7KQ2M9XW4PRT`). Owner and admin only; no second approver yet. | A wrong body key returns `400` and nothing is deactivated; the page shows the API's message. |
| BCH-15 | `GET /catalogue/products/{id}/batches` is paginated and returns `codes_issued` per batch. | Optional | Open | – |

---

## 6. Public verification (`/verify`)

Consumer-facing rules that depend on product data. The public page itself is not part of the Products tab but must honour these.

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VER-1 | Hidden fields (per `consumer_visibility`) are **omitted**, not null. Product name is always returned. | **Required** | Done (per reference) |
| VER-2 | Only `approved`, non-suspended products resolve; anything else is `404 not_found`. While a revision is `in_review`, **last approved values** are returned. State this as the full rule: the earlier "approved **and** at least one batch with issued codes" condition is dropped. | **Required** | Confirm |
| VER-3 | Regulatory reference object: `{ authority, number, verification: "unverified"\|"verified", method?, checked_at? }`. The frontend owns the wording ("Provided by the manufacturer · not independently checked"). | **Required** | Confirm |
| VER-4 | `support_contact` = product override, else tenant default, omitted entirely when the toggle is off. Shape `{ email?, phone?, url? }`, omitted keys when empty. | **Required** | Done (per reference) |
| VER-5 | Product image URL (public or signed with a lifetime longer than the page session). Omitted when `product_image` is off. | **Required** | Confirm |
| VER-6 | `consumer-preview` for a never-approved product: return `404`, or `200` with a flag `{ approved: false, ... }` and the draft values? The UI handles `404` as "Nothing to preview yet". | Decision | Open |
| VER-7 | Never return who checked, where, flags or cases (design rule). | **Required** | Done (per reference) |

### 6.1 Product code format (blocker)

**Implemented.** The backend issues **12-character codes** (`7KQ2-M9XW-4PRT` when grouped), generated with `secrets.choice`. Codes are stored and returned **canonical: upper-case, no dashes or spaces** (`7KQ2M9XW4PRT`); the frontend groups them as `XXXX-XXXX-XXXX` for display. Every lookup (verify, reports, deactivate, USSD, GS1) goes through one helper that trims, upper-cases and strips spaces and dashes, so `7kq2-m9xw 4prt` resolves.

**Frontend: update `lib/api/verify.ts` to this exact format.**

| Rule | Value |
|---|---|
| Length | 12 characters after removing spaces and dashes |
| Alphabet (30 symbols) | `23456789ABCDEFGHJKMNPQRSTVWXYZ` (digits 2-9, then letters without `I L O U`). `0`, `1`, `O`, `I`, `L`, `U` are never valid and must **not** be auto-corrected |
| Check character | The 12th character is a **Luhn mod-30** check over the first 11. Index = position in the alphabet above. From the right, double every other value (starting with 2), add `q + r` of each product divided by 30, and the check is `(30 - sum % 30) % 30` |
| Check algorithm in code | `_check_char()` in `apps/issuance/codes.py` |

```ts
const ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ";
function checkChar(payload: string): string {
  let sum = 0, factor = 2;
  for (let i = payload.length - 1; i >= 0; i--) {
    const add = factor * ALPHABET.indexOf(payload[i]);
    factor = factor === 2 ? 1 : 2;
    sum += Math.floor(add / 30) + (add % 30);
  }
  return ALPHABET[(30 - (sum % 30)) % 30];
}
```

**Errors.** `GET /verify/{code}` returns `400 invalid_code_format` for input that cannot be a code (wrong length, a character outside the alphabet, or a bad check character). It is **not recorded** as a scan. A well-formed code that does not exist returns `404` with `result: "not_found"` and is recorded.

**Existing codes.** Codes issued before the change keep working: they were converted to 12-character codes, and their old 22-character value resolves through `legacy_code` by **exact, case-sensitive match** only (a legacy code that is not found returns `400 invalid_code_format`, since it fails the new format check).

**QR payload (proposed, needs sign-off before labels are printed).** The QR on a pack encodes a **verify URL**, not the bare code, so a phone's native camera opens the check page with no app.

| Rule | Value |
|---|---|
| Payload | `https://<verify-domain>/v/7KQ2M9XW4PRT`: the frontend consumer page, canonical code (upper-case, no dashes), no query string, no tracking parameters |
| Domain | One fixed, owned verify domain, agreed once. Labels are physical and outlive deployments, so the domain must never change; use a redirect on that domain if the frontend moves |
| Not in the payload | Tenant, batch, product, GTIN, expiry or any other data. The code alone identifies the unit, and anything else would go stale or leak |
| Printed alongside | The same code as text, grouped `XXXX-XXXX-XXXX`, beside every QR, as the manual-entry fallback |
| Error correction | Level M or higher; keep the quiet zone of 4 modules; minimum printed size about 15 mm |
| GS1 variant | Packs that already use GS1 Digital Link may encode `https://<verify-domain>/01/{gtin}/21/{code}` instead. It must resolve to the same check (see below) |

**In-app "Scan QR" (frontend).** Read the QR, then accept only (a) a URL on the verify domain whose path is `/v/{code}` or `/01/{gtin}/21/{code}`, or (b) a bare 12-character code. Extract the code, run it through the same `cleanCode()` and `validateCode()` as typed input, and call `GET /verify/{code}`. Any other payload (another domain, another path, free text) is rejected with "This QR code is not an AuthentiCheck code" and is **not** looked up, which stops lookalike or phishing QR codes being opened from inside the app. Scan and manual entry are the same check: both are recorded and rate-limited alike.

**Backend gaps.**
- ~~`GET /01/{gtin}/21/{serial}` redirected to the JSON endpoint.~~ **Fixed:** it now redirects to `{VERIFY_BASE_URL}/v/{code}` (the consumer page), with the code normalised. `VERIFY_BASE_URL` is a setting that defaults to `FRONTEND_URL`; it is set to the temporary test domain `https://authenticheck-web.netlify.app` for now; change it to the permanent domain before real labels are printed.
- The backend does not generate QR images. Batch code exports must give the printer the canonical code (and optionally the full URL) so the label vendor builds the QR.

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VER-8 | **Issue 12-character codes.** Generate from a secure random source (`secrets.choice`, not a derived or sequential value), unique across all tenants as today. Replace `secrets.token_urlsafe(16)` in `issuance/tasks.py` (both the batch path and the collision-retry path). Collision handling stays. | **Required** | Done |
| VER-9 | **Alphabet.** Agree the exact character set; the frontend validates against it. Recommendation: 32 unambiguous characters (Crockford Base32, or `A–Z` and `2–9` without `I L O U`), which gives about 2^60 codes at 12 characters. If `0`/`O` and `1`/`I` are both allowed, the frontend must not auto-correct them. **Optional extension:** a check character (ISO 7064 / Luhn-style) as the 12th character, so typos are caught before a lookup is recorded. | Decision | Done (30 symbols + Luhn mod-30 check) |
| VER-10 | **Normalise before every lookup:** trim, remove spaces and `-`, upper-case. Apply it in `GET /verify/{code}`, `POST /reports` (`code_str`), `POST /issuance/codes/{code}/deactivate`, and the GS1 redirect (VER-13), through one shared helper. Store codes canonically (upper-case, no dashes). The frontend shows and links codes as `XXXX-XXXX-XXXX`, and print exports should use the same grouping. | **Required** | Done |
| VER-11 | **Schema and existing data.** Migration for `Code.code` (`max_length` 12, or 16 for headroom) and for `code_str` on `ScanEvent` and the risk report model (these hold user input, so keep them long enough for malformed values). **Decision:** what happens to codes already issued at 22 characters: regenerate (fine if only test data), or keep them resolvable through a legacy exact-match fallback. | **Required** | Done (`max_length` 16; `legacy_code` fallback) |
| VER-12 | `risk/serializers.py` already upper-cases `code_str` on reports. That was wrong for mixed-case codes and becomes correct once VER-8 lands; make it use the VER-10 helper instead of a bare `.upper()`. | **Required** | Done |
| VER-13 | **GS1 Digital Link** `GET /01/{gtin}/21/{serial}` redirects to `/verify/{serial}`. Confirm that `serial` is the 12-character code (not a longer GS1 serial) and normalise it the same way. If GS1 serials will be longer, say so now. | Decision | Done (`serial` is the 12-character code, normalised the same way) |
| VER-14 | **Malformed input.** Today any string records a `not_found` scan, so typos and bots create noise in analytics and risk rules. Suggested: input that fails the VER-9 format returns `400` with `{ error: { code: "invalid_code_format" } }` and is **not** recorded. The frontend already validates locally, so this protects other clients (USSD, direct links). | Optional | Done for `GET /verify/{code}` (`400 invalid_code_format`, not recorded) |

### 6.2 Response contract for the result states

The design has these consumer states: first check, checked before, withdrawn, deactivated, expired, not recognised, unavailable. The current `GET /verify/{code}` can express only some of them. The frontend parser is `parse()` in `lib/api/verify.ts`; it accepts several shapes, so tell us the real one and it will be tightened.

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VER-15 | **First vs repeat check.** Add `is_first_check` (bool), `checked_at` (this check, ISO UTC) and, when it is a repeat, `first_checked_at`. "Repeat" means a prior recorded check of the same code, counted **before** this request's scan is written (and safe against two simultaneous first checks). No check count, who, where or device (VER-7). Without this the First check and Checked before screens cannot be told apart. | **Required** | Open |
| VER-16 | **Withdrawn.** The API returns `recalled` only. The design shows "The manufacturer has withdrawn this batch" with the manufacturer's instructions. Return `result: "recalled"` or `"withdrawn"` (the frontend maps both to the red screen; see BATCHES_CODES_API_REQUIREMENTS.md section 2 for the recall vs withdraw decision) plus `effective_at`, `consumer_message` (the manufacturer's instructions; the frontend has a fixed fallback) and `approved_by` / `approved_at` when known. | **Required** | Open |
| VER-17 | **Expired.** Add `result: "expired"` when the batch's `expiry` has passed, with `batch.batch_number` and `batch.expiry` (visibility toggles still apply). The product must still resolve normally otherwise. | **Required** | Open |
| VER-18 | **Deactivated.** The response returns only `code`, `deactivated_at`, `deactivation_reason`. The design shows the manufacturer and a way to contact them, so also return `manufacturer.name` and `support_contact` (product override, else tenant default, VER-4). Consider **omitting `deactivation_reason`** from the public response unless it is written to be shown to consumers. | **Required** | Open |
| VER-19 | **Field names to confirm.** The frontend reads: `manufacturer.name`; `product.{name, brand, variant_size, industry}`; `batch.{batch_number, manufacture_date, expiry}`; `product_image.url`; `support_contact.{email, phone, url}`. Regulatory data is returned as a **list** `regulatory_refs[{country_code, reference_number, valid_until, verification_status}]`, while VER-3 describes a single object with `authority`; please reconcile (the frontend can read the list, but needs the authority name, e.g. "NAFDAC", for the label). `product.industry` is needed to show the "speak to a pharmacist" note. | **Required** | Confirm |
| VER-20 | **No caching, and abuse limits.** Every `GET /verify/{code}` records a scan, so responses must carry `Cache-Control: no-store`. Rate-limit the public endpoint per IP and return `429` with `Retry-After`; the frontend shows its "can't check right now" state for `429` and `5xx`. | Optional | Open |

Frontend side of VER-8 to VER-14: the alphabet is fixed, so the constants and validator in `lib/api/verify.ts` change in one place. The full frontend checklist is in "Backend changes to adopt (2026-11-20)" at the top.

---

## 7. Cross-cutting

| ID | Requirement | Priority | Status | Why |
|---|---|---|---|---|
| XC-1 | Make the contract machine-readable for the frontend: either commit `API_CONTRACTS.md` to this repo or give access to the OpenAPI schema (`/api/v1/schema/`) so types can be generated. | **Required** | Open | Removes most `Confirm` rows in this document and the hand-written types in `lib/types` and `lib/api/catalogue.ts`. |
| XC-2 | One status for validation failures (`400` vs `422`) and one `details` format across all endpoints. | **Required** | Confirm | See PRD-7. |
| XC-3 | List endpoints that return unbounded arrays (activity, regulatory refs, batches by product) use the pagination envelope or document their caps. | Optional | Open | – |
| XC-4 | CORS for the Next.js origin with credentials (refresh cookie) in every environment. | **Required** | Confirm | Dev and staging origins differ. |
| XC-5 | Every error includes `request_id`; the frontend will show it on unexpected failures. | Optional | Confirm | Support. |
| XC-6 | Idempotency for state-changing product actions (`submit`, `withdraw`) on double-click: second call returns `409 invalid_status_transition` (current behaviour) with the current status in `details`. | Optional | Confirm | Lets the UI refresh instead of showing an error. |
| XC-15 | CORS must allow the **`Idempotency-Key`** request header (add `idempotency-key` to `CORS_ALLOW_HEADERS`, keeping the `corsheaders` defaults), and expose `Retry-After` if it is read cross-origin. The browser sends it on `POST /issuance/jobs` and `POST /issuance/jobs/{id}/resume`; without it the preflight fails and the page only sees a network error, so no code job can be started from the app. Same failure mode as the custom `X-Tenant-ID` header earlier. | **Required** | **Done 2026-11-17.** `CORS_ALLOW_HEADERS` is the `corsheaders` defaults plus `idempotency-key`; `Retry-After` is added to the exposed headers (`X-Request-ID` and `Idempotent-Replayed` already were). Covered by a preflight test. Redeploy the API for it to take effect. | "Issue codes" fails with "We couldn't reach the server" in every browser. |

---

## 8. Investigations

Not started on the frontend beyond sample data. Full specification: **`INVESTIGATIONS_API_REQUIREMENTS.md`**. Add rows here when that work begins, using prefix `INV-`.

---

## 9. Frontend assumptions register

Where the frontend hard-codes a guess. When an item above is confirmed, update the code and remove the line.

| Code | Assumption | Row |
|---|---|---|
| `lib/api/catalogue.ts` `ProductList` | `status_counts` optional on the list | PRD-2, PRD-26 |
| `lib/api/catalogue.ts` `asList()` | Array or `{ results }` accepted for industries, activity, refs, batches, brands | PRD-22 – PRD-27 |
| `lib/api/catalogue.ts` `platformApi.correct` | Body `{ field, new_value, reason }` | PLT-4 |
| `lib/api/catalogue.ts` `platformApi.verifyRef` | Body `{ method, note }` | PLT-5 |
| `lib/api/client.ts` `fieldErrors()` | `error.details` is `{ field: string \| string[] }` | PRD-7, PRD-8 |
| `lib/auth/permissions.ts` | Editors assumed able to submit unless `can_submit_products === false` | AUTH-1 |
| `components/catalogue/ProductForm.tsx` | `required_fields` keys; `null` clears; nested `regulatory_reference` upserts; only changed fields sent on edit; SKU/GTIN omitted after approval | PRD-9, PRD-10, PRD-22 |
| `components/catalogue/ProductDetail.tsx` | `correction_request`, `gtin_conflict`, `pending_revision` shapes; activity `changes` or `before/after`; consumer preview read flat or under `product` | PRD-14, PRD-15, PRD-23, PRD-24 |
| `lib/api/verify.ts` `CODE_LENGTH`, `cleanCode()`, `validateCode()` | Codes are 12 letters/digits, case-insensitive, dashes and spaces ignored, characters never substituted | VER-8 – VER-11 |
| `lib/api/verify.ts` `parse()` | `/verify` payload fields and `result` values (`genuine`, `recalled`/`withdrawn`, `deactivated`, `expired`, `not_found`); repeat detected from `is_first_check` / `first_checked_at` | VER-15 – VER-19 |
| `components/platform/ProductReviewQueue.tsx` | Row has `tenant_name`, `submitted_at`; `gtin_conflict_resolution` is free text | PLT-1, PLT-6 |

---

## 10. Decisions still open (collected)

1. **Roles**: names and permissions beyond owner/admin/editor (AUTH-2).
2. **Brand creation by editors** (BRD-1).
3. **Support email before go-live**: where it is enforced (TEN-2).
4. **Consumer preview for never-approved products** (VER-6).
5. **Behaviour with several regulatory references** on one product (PRD-10).
6. **Suspension and batches**: does a suspended product block issuing more codes (BCH-10)?
7. ~~Consumer code alphabet, check character and existing 22-character codes (VER-9, VER-11, VER-13).~~ **Decided and implemented:** 30-symbol alphabet, Luhn mod-30 check character, old codes resolve through `legacy_code`. See section 6.1.
8. Batches & codes and Investigations decisions: see their own docs (§11 and §10).

---

## Appendix: status of the original product gaps

From `PRODUCTS_API_REQUIREMENTS.md` §1, measured against `ENDPOINT_REFERENCE.md` on 2026-10-30.

| # | Gap | Result |
|---|---|---|
| 1 | Identity fields | Covered |
| 2 | Status lifecycle | Covered (`changes_requested` added) |
| 3 | Submit with validation | Covered |
| 4 | Approve / reject | Covered, staff-only under `/platform/products/...` |
| 5 | Duplicate protection | Covered |
| 6 | Live duplicate pre-check | **Open** → PRD-4 |
| 7 | Consumer visibility | Covered |
| 8 | Regulatory reference | Covered; shapes → PRD-10, PRD-25, PLT-5 |
| 9 | Industry gating | Covered |
| 10 | Reference image | Covered; shapes → PRD-19, PRD-20 |
| 11 | List filters and counts | Filters covered; counts **Open** → PRD-2, PRD-3 |
| 12 | Batch `manufacture_date`, counts | **Open** → PRD-6, BCH-2 |
| 13 | Activity log | Covered; shape → PRD-23 |
| 14 | Industry metadata | Covered; shape → PRD-22 |
| 15 | Support contact | Covered; enforcement → TEN-2 |
