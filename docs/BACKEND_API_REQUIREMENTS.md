# AuthentiCheck – Backend API Requirements (consolidated)

One living list of everything the frontend needs from the backend that `ENDPOINT_REFERENCE.md` does not yet give it, grouped by area. It is meant to be **kept and edited**, not rewritten each time.

- Last reviewed against `ENDPOINT_REFERENCE.md`: **2026-10-30**
- Frontend state at that date: **Products tab built** against the endpoints below. Batches & codes and Investigations are still on sample data; their detailed specs stay in their own docs and are indexed in [section 5](#5-batches--codes) and [section 8](#8-investigations).

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
| AUTH-2 | Production / Quality lead and Investigator roles, with the permission table agreed (see `BATCHES_CODES_API_REQUIREMENTS.md` §9 and `INVESTIGATIONS_API_REQUIREMENTS.md`). `PATCH /tenants/me/members/{id}` must accept them. | **Required** (Decision) | Open (PO decision 9: not added yet) | Only `owner`, `admin`, `editor` are modelled; batches and investigations stay owner/admin. |
| AUTH-3 | Switch the active company for a user with several memberships (the sidebar has a switcher). Needs an endpoint that re-issues the access token with another `tid`, e.g. `POST /auth/switch-tenant { tenant_id }`. | Optional | Open | The frontend uses the first membership only. |
| AUTH-4 | Return the current user's `id` in `/auth/me` and `submitted_by` / `created_by` (id + name) on products, so "submitter or admin" can withdraw is decidable in the UI (see PRD-12). | **Required** | Confirm | Withdraw is shown to every ✏️ user; the API answers `403` for the rest. |

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
| PLT-2 | A staff product detail: `GET /platform/products/{id}` returning the full field snapshot, a **diff against the approved version**, reference image URL, regulatory references (with ids), the activity log, and the tenant's support contact. `GET /catalogue/products/{id}` is 🏢-only, so staff can't read it today. | **Required** | Open | The reviewer decides from the row alone. |
| PLT-3 | Staff-readable activity (`GET /platform/products/{id}/activity` or covered by PLT-2). | **Required** | Open | – |
| PLT-4 | Body of `POST /platform/products/{id}/correct`: `{ field, new_value, reason }`. | Confirm | Confirm | The frontend mirrors the tenant's `correction-request` body with `new_value`. |
| PLT-5 | Body of `POST .../regulatory-refs/{ref_id}/verify`: `{ method, note? }` plus an optional `checked_at` (default now). | Confirm | Confirm | A "Verify" action is not yet in the review UI (needs PLT-2 for ref ids). |
| PLT-6 | `gtin_conflict_resolution` format: free text, or `{ action: "allow_duplicate"\|"..." , note }`? Also: a conflict preview (`conflicting_product` summary) so the reviewer can see what clashes. | Confirm | Confirm | Frontend sends a free-text string. |
| PLT-7 | Notify the submitter on a decision (email and/or webhook event `product.approved`, `product.changes_requested`, `product.rejected`). | Optional | Open | Users find out when they open the product. |
| PLT-8 | Queue filters: `tenant_id`, `industry`, `has_pending_revision`, `ordering` (oldest first is the default). | Optional | Open | – |

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
| BCH-10 | **Gating rule, written down.** A batch can be created and codes issued only when the product is `approved`, **not** suspended, and active. Error codes: `409 product_not_approved`, `409 product_suspended`, `409 product_archived`. Say what happens to existing batches when a product is suspended (codes stay issued but `/verify` returns 404) or when a revision is in review (nothing changes). | **Required** | Open | Products doc §3 |
| BCH-11 | `GET /catalogue/products/{id}/batches` is paginated and returns `codes_issued` per batch. | Optional | Open | – |

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
| `components/platform/ProductReviewQueue.tsx` | Row has `tenant_name`, `submitted_at`; `gtin_conflict_resolution` is free text | PLT-1, PLT-6 |

---

## 10. Decisions still open (collected)

1. **Roles**: names and permissions beyond owner/admin/editor (AUTH-2).
2. **Brand creation by editors** (BRD-1).
3. **Support email before go-live**: where it is enforced (TEN-2).
4. **Consumer preview for never-approved products** (VER-6).
5. **Behaviour with several regulatory references** on one product (PRD-10).
6. **Suspension and batches**: does a suspended product block issuing more codes (BCH-10)?
7. Batches & codes and Investigations decisions: see their own docs (§11 and §10).

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
