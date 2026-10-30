# AuthentiCheck – Products API Requirements

Backend requirements for the frontend **Products** tab (list, register, detail, edit, batches), derived from the approved "Register a product" design. It lists everything the current API does not yet cover.

**Legend**

- **Required**: the frontend cannot ship the feature without it.
- **Optional**: improves the experience or can be faked on the client for now. The "Without it" note says what the frontend does instead.
- **Decision**: a product or policy choice the backend needs answered first. Resolved in [section 12](#12-decisions).

**Caveat on "exists today".** `API_CONTRACTS.md` is not in the frontend repo. "Today" is based on `ENDPOINT_REFERENCE.md` and the frontend's current `Product` type (`id, name, brand, brand_id, is_active, created_at`). If the contracts already cover an item, strike it.

**Drafts.** A draft is a half-finished product. Validation of "Required to submit" fields happens at **submit**, not at save. Drafts may have those fields empty.

---

## 1. Gap summary

| # | Capability | Exists today | Priority |
|---|---|---|---|
| 1 | Product identity fields: industry, variant, size, category, SKU, GTIN | name, brand only | **Required** (GTIN optional) |
| 2 | Status lifecycle with drafts | `is_active` only | **Required** |
| 3 | Submit-for-review action with full validation | none | **Required** |
| 4 | Approve / reject actions | none | **Required** (who approves = Decision) |
| 5 | Duplicate protection: SKU uniqueness + similar-product warning | none | **Required** |
| 6 | Live duplicate pre-check endpoint | none | Optional |
| 7 | Consumer-visibility toggles, honoured by `GET /verify/{code}` | none | **Required** |
| 8 | Regulatory reference with source + verification state | `/regulatory-refs` endpoints, shape unknown | **Required** |
| 9 | Industry gating (pharmaceuticals locked) | none | **Required** |
| 10 | Reference image upload | none | Optional |
| 11 | Product list filters, search, counts | list endpoint, filters unknown | Filters **Required**, counts Optional |
| 12 | Batch `manufacture_date` and product/batch counts | batch has `batch_number`, `expiry` | **Required** (date), counts Optional |
| 13 | Per-product activity log | platform audit only | Write **Required**, read endpoint Optional |
| 14 | Industry / category metadata endpoint | none | Optional |
| 15 | Tenant support contact (for the "Support contact" toggle) | none | Optional |

---

## 2. Product data model

`R` = required to submit. `O` = optional. "Visible" = whether the consumer-visibility toggle in [section 7](#7-consumer-visibility) controls it.

| Field | Type | R/O | Validation and notes | Visible |
|---|---|---|---|---|
| `id` | uuid | server | | |
| `name` | string | R | 2–120 chars, trimmed | always shown |
| `brand_id` | uuid | R | must belong to the tenant and be active (exists today) | toggle |
| `industry` | enum `cosmetics` \| `pharmaceuticals` | R | immutable once approved (Decision). `pharmaceuticals` rejected with `industry_locked` until the release gate is open ([section 9](#9-industry-gating)) | |
| `variant` | string | R (cosmetics) | shade / scent, 1–80 chars. Required fields change with industry ([section 11](#11-industry-and-category-metadata)) | toggle (with size) |
| `size` | string | R (cosmetics) | e.g. `400 ml`, 1–40 chars. Decision: free text vs `size_value` + `size_unit` | toggle (with variant) |
| `category` | string (slug) | R | must belong to the industry's category list, e.g. `body-care-lotion` | |
| `sku` | string | R | 3–64 chars, `[A-Z0-9_-]`, normalised to upper case. Unique per tenant, see [section 5](#5-duplicate-protection) | |
| `gtin` | string | **O** | 13 or 14 digits, GS1 check digit validated. Empty string stored as `null` | |
| `reference_image_id` | uuid \| null | **O** | from image upload, [section 6](#6-reference-image) | toggle |
| `regulatory_reference` | object \| null | **O** | see [section 8](#8-regulatory-reference) | toggle |
| `consumer_visibility` | object | R (default all `true`) | see [section 7](#7-consumer-visibility) | |
| `status` | enum | server | see [section 3](#3-status-lifecycle) | |
| `is_active` | bool | existing | archive flag, see [section 3](#3-status-lifecycle) | |
| `review_note` | string \| null | server | reason given on reject | |
| `created_at`, `updated_at` | ISO 8601 UTC | server | frontend renders in WAT | |
| `created_by`, `submitted_by`, `submitted_at`, `reviewed_by`, `reviewed_at` | user ref / timestamp | server | for detail page and audit | |

**Read-only extras on list and detail responses** (Optional, see [section 10](#10-batches-counts-and-activity)): `brand_name`, `batch_count`, `active_batch_count`, `codes_issued`.

---

## 3. Status lifecycle

**Required.** Replace the bare `is_active` with a `status` field. Keep `is_active` for archiving.

```
draft ──submit──► in_review ──approve──► approved
  ▲                   │
  │                   └──reject──► rejected ──edit + submit──► in_review
  └──────── (Decision: withdraw in_review → draft)

any status ──archive──► archived (is_active = false) ──restore──► previous status
```

| Transition | Who | Rules |
|---|---|---|
| create → `draft` | owner/admin (👑) | partial data allowed |
| `draft`/`rejected` → `in_review` (submit) | 👑 | full validation, writes audit entry |
| `in_review` → `approved` / `rejected` | Decision: platform staff (🛡️) or a second approver in the company | reject requires `review_note` |
| edit | 👑 | `draft`/`rejected`: any field. `approved`: Decision (see [section 12](#12-decisions)). `in_review`: locked |
| archive / restore | 👑 | via `PATCH is_active`. Archived products stay resolvable for codes already issued |

Invalid transitions return `409 invalid_status_transition`. Edits to a locked product return `409 product_not_editable`.

**Consumer visibility of a product requires `status = approved` AND at least one batch with issued codes.** Otherwise `GET /verify/{code}` behaves as "not recognised".

---

## 4. Endpoints

`Existing` = already documented, needs extending. `New` = to be built.

| Method | Path | Auth | State | R/O | Purpose |
|---|---|---|---|---|---|
| `GET` | `/catalogue/products` | 🏢 | Existing, extend | **R** | List with filters (below) |
| `POST` | `/catalogue/products` | 👑 | Existing, extend | **R** | Create. Always starts as `draft` |
| `GET` | `/catalogue/products/{id}` | 🏢 | Existing, extend | **R** | Full detail incl. visibility, regulatory ref, image URL, review note |
| `PATCH` | `/catalogue/products/{id}` | 👑 | Existing, extend | **R** | Edit per status rules; archive via `is_active` |
| `POST` | `/catalogue/products/{id}/submit` | 👑 | New | **R** | `draft`/`rejected` → `in_review`; full validation |
| `POST` | `/catalogue/products/{id}/approve` | Decision | New | **R** | `in_review` → `approved` |
| `POST` | `/catalogue/products/{id}/reject` | Decision | New | **R** | `in_review` → `rejected`; body `{ "review_note": "..." }` |
| `GET` | `/catalogue/products/check-duplicate` | 🏢 | New | O | Live duplicate pre-check, [section 5](#5-duplicate-protection) |
| `POST` | `/catalogue/images` | 👑 | New | O | Upload reference image (`multipart/form-data`) |
| `DELETE` | `/catalogue/images/{id}` | 👑 | New | O | Remove an uploaded image |
| `GET` | `/catalogue/products/{id}/activity` | 🏢 | New | O | Per-product change history |
| `GET` | `/catalogue/products/{id}/consumer-preview` | 🏢 | New | O | Exactly what `/verify` would show |
| `GET` | `/catalogue/industries` | 🏢 | New | O | Industries, required fields, categories |
| `POST` | `/platform/products/{id}/regulatory-refs/{ref_id}/verify` | 🛡️ | New | **R** | Record a verification, [section 8](#8-regulatory-reference) |

### List filters (`GET /catalogue/products`) — Required

| Param | Type | Notes |
|---|---|---|
| `q` | string | matches name, SKU, brand name (case-insensitive, partial) |
| `status` | enum, repeatable | `draft`, `in_review`, `approved`, `rejected` |
| `brand_id` | uuid | |
| `industry` | enum | |
| `is_active` | bool | default `true` (archived hidden) |
| `ordering` | string | `-updated_at` (default), `name`, `-created_at` |
| `page`, `page_size` | int | existing pagination envelope (`count/next/previous/results`) |

The dashboard also needs a count of drafts. Either a `status` aggregate on the list response (`"status_counts": {"draft": 3, ...}`) or `GET /analytics/catalogue`. Optional: without it the frontend calls the list with `status=draft&page_size=1` and reads `count`.

### Create / update body (example)

```json
{
  "name": "Shea Butter Body Lotion",
  "brand_id": "b3f1...",
  "industry": "cosmetics",
  "variant": "Unscented",
  "size": "400 ml",
  "category": "body-care-lotion",
  "sku": "EN-SBL-400",
  "gtin": null,
  "reference_image_id": null,
  "regulatory_reference": {
    "authority": "NAFDAC",
    "number": "A1-1234",
    "source": "entered_by_manufacturer"
  },
  "consumer_visibility": {
    "brand": true, "variant_size": true, "batch_number": true,
    "manufacture_date": true, "expiry": true, "regulatory_reference": true,
    "product_image": true, "support_contact": true
  },
  "confirm_not_duplicate": false
}
```

Accepting `regulatory_reference` nested in create/patch makes the save atomic. Optional: without it the frontend creates the product, then calls `POST .../regulatory-refs` (two requests, can half-fail).

### Validation response — Required

`422 validation_error` with per-field details, using the existing error envelope:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields need attention.",
    "details": { "sku": ["This field is required."], "size": ["This field is required."] },
    "request_id": "..."
  }
}
```

On **draft save**, only structural errors are returned (wrong type, too long, bad GTIN). On **submit**, missing required fields are returned as well.

---

## 5. Duplicate protection

**Required.** Two separate rules, matching the design's banner and SKU field message.

**Hard rule: SKU is unique per tenant.**
- Compare trimmed, case-insensitive. Applies on create, update and submit.
- Violation returns `409 sku_conflict`.
- Decision: include archived products in the uniqueness check? Recommended: yes, so a retired SKU is never reused.

**Soft rule: similar product.**
- Same tenant, same `brand_id` + `name` + `variant` + `size` (normalised) as another product.
- Returns `409 possible_duplicate` unless the request carries `"confirm_not_duplicate": true` (the "It's a different product" button).
- Products are **never merged automatically.**

Both 409s include the match so the banner can render without another call:

```json
{
  "error": {
    "code": "sku_conflict",
    "message": "SKU is already used by another product in this company.",
    "details": {
      "existing_product": {
        "id": "...", "name": "Shea Butter Body Lotion", "variant": "Unscented",
        "size": "400 ml", "sku": "EN-SBL-400", "batch_count": 6
      }
    }
  }
}
```

**Optional: live pre-check**, so the banner appears as the user types instead of on save.

`GET /catalogue/products/check-duplicate?sku=&name=&brand_id=&variant=&size=&exclude_id=` returns:

```json
{ "sku_conflict": { "...product summary..." }, "similar": [ { "...product summary..." } ] }
```

`exclude_id` is the product being edited. Without this endpoint the frontend learns about duplicates only when saving. Debounce on the client; rate-limit on the server.

---

## 6. Reference image

**Optional** (the design's "Ready to submit" checklist flags a missing image as a warning, not a blocker).

Upload happens before the product exists, so use a standalone upload that returns an id:

`POST /catalogue/images` (`multipart/form-data`, field `file`) →

```json
{ "id": "img_...", "url": "https://.../...", "width": 1200, "height": 1200 }
```

| Requirement | Detail |
|---|---|
| Types | PNG or JPEG only. Detect by content, not file extension or `Content-Type` header |
| Size | 5 MB max → `413 file_too_large` |
| Dimensions | Suggest min 400 px, max 4000 px on the longest side → `422 image_invalid` |
| Privacy | **Strip all EXIF/metadata (GPS location, camera data) by re-encoding.** The upload UI promises this to the user |
| Storage | Public via CDN or signed URL. Only exposed by `/verify` when `consumer_visibility.product_image` is true |
| Lifecycle | Unattached uploads are deleted after ~24 h. Replacing or deleting detaches the old image |
| Errors | `415 unsupported_media_type`, `413 file_too_large`, `422 image_invalid` |

Without it the frontend hides the upload control and shows a placeholder tile.

---

## 7. Consumer visibility

**Required.** Product name is always shown; the other fields are per-product toggles, default `true`.

```json
"consumer_visibility": {
  "brand": true,
  "variant_size": true,
  "batch_number": true,
  "manufacture_date": true,
  "expiry": true,
  "regulatory_reference": true,
  "product_image": true,
  "support_contact": true
}
```

Rules:

- `GET /verify/{code}` must **omit** (not null) any hidden field. Hidden data must never reach the browser.
- A product that is not `approved` is never resolvable: `/verify` treats it as not recognised.
- Never return who checked or where. Flags and cases are never in `/verify` (existing design rule).
- `batch_number`, `manufacture_date` and `expiry` live on the batch but are controlled by the product's toggles.
- `support_contact` needs a source: a tenant-level contact (phone/email/URL). Optional: add `support_contact` to the tenant settings. If absent, the toggle is disabled with a hint.
- Optional: soft-reject obvious personal data (email addresses, phone numbers) in free-text product fields. The UI warns users not to put staff personal data in product fields.

**Optional: `GET /catalogue/products/{id}/consumer-preview`** returns exactly the `/verify` payload for an approved product with a sample batch, so the on-screen preview can never drift from the real result. Without it the frontend renders the preview from the product itself.

---

## 8. Regulatory reference

**Required** (the "unverified" copy is a design rule). Existing endpoints: `GET/POST /catalogue/products/{product_id}/regulatory-refs`. Their body shapes need to cover:

| Field | Type | R/O | Notes |
|---|---|---|---|
| `authority` | enum | R | `NAFDAC` for cosmetics in the pilot; extendable |
| `number` | string | **O** on the product, R if a ref is supplied | free-form, trimmed. Format check Decision (pharma needs a stricter one) |
| `source` | enum | R | `entered_by_manufacturer` (default), `imported`, `verified_by_reviewer` |
| `verification_status` | enum | server | `unverified` (default) \| `verified` |
| `verified_by` | user ref | server | AuthentiCheck reviewer, staff only |
| `verified_at` | ISO 8601 | server | |
| `verification_method` | string | server | e.g. "NAFDAC register lookup" |
| `verification_note` | string | server, O | |

Rules:

- Any change to `number` or `authority` resets `verification_status` to `unverified`.
- Verification is staff-only: `POST /platform/products/{id}/regulatory-refs/{ref_id}/verify` (🛡️) with `{ "method": "...", "checked_at": "...", "note": "..." }`. Records method and date, as the design promises.
- `GET /verify/{code}` returns the **state**, and the frontend owns the wording:

```json
"regulatory_reference": {
  "authority": "NAFDAC",
  "number": "A1-1234",
  "verification": "unverified"
}
```

When `verification` is `verified`, also return `method` and `checked_at`. Required consumer copy for unverified: "Provided by the manufacturer · not independently checked". A registered code does not prove genuineness.

---

## 9. Industry gating

**Required.** Pharmaceuticals are locked behind the release gate.

- `POST/PATCH` with `industry: "pharmaceuticals"` returns `403 industry_locked` until the gate is open for the tenant (or globally).
- **Optional:** expose gate state so the dashboard's "Release gates" card and the industry picker are real: `GET /tenants/me/release-gates` →

```json
{
  "cosmetics": { "status": "live" },
  "pharmaceuticals": {
    "status": "locked",
    "items": [
      { "key": "nafdac_gs1_review", "label": "Qualified NAFDAC / GS1 review", "state": "in_progress" },
      { "key": "identifier_decision", "label": "Identifier & data-carrier decision", "state": "not_started" }
    ]
  }
}
```

`state` is `not_started | in_progress | done`. Without it the frontend hard-codes the Cosmetics-live / Pharma-locked state.

---

## 10. Batches, counts and activity

**Batch fields** (`/catalogue/products/{id}/batches`): the frontend type has `batch_number`, `expiry`, `status`. Add:

| Field | R/O | Notes |
|---|---|---|
| `manufacture_date` | **R** field exists; value may be optional per industry | needed by the "Manufacture date" toggle |
| `expiry` | existing | label differs by industry ("Expiry" / "Best before") |

**Counts on product list/detail** — Optional, cheap, avoid N+1 calls from the table:

| Field | Meaning |
|---|---|
| `batch_count` | all batches |
| `active_batch_count` | batches not recalled/expired |
| `codes_issued` | total codes across the product's batches |

**Activity log.**

- **Required (write):** record an audit entry on create, update, submit, approve, reject, archive/restore. The design states: *"Submitting records your name, the time and every field value in the audit log."* So the submit entry must store a snapshot of every field value.
- **Optional (read):** `GET /catalogue/products/{id}/activity` →

```json
{ "results": [
  { "at": "2026-10-23T09:14:00Z",
    "actor": { "id": "...", "name": "Funmi Adeyemi" },
    "action": "product.submitted",
    "changes": [ { "field": "sku", "from": null, "to": "EN-SBL-400" } ] }
] }
```

Without the read endpoint the frontend hides the Activity tab. All timestamps are ISO 8601 UTC; the frontend displays WAT.

---

## 11. Industry and category metadata

**Optional.** The form says "Required fields change with the industry". Without this endpoint the frontend hard-codes the cosmetics field set and category list, so every change needs a frontend deploy.

`GET /catalogue/industries` →

```json
[
  {
    "key": "cosmetics",
    "label": "Cosmetics & personal care",
    "description": "Skincare, soaps, haircare, perfume, body care",
    "available": true,
    "required_fields": ["name", "brand_id", "variant", "size", "category", "sku"],
    "categories": [ { "key": "body-care-lotion", "label": "Body care · lotion" } ]
  },
  { "key": "pharmaceuticals", "label": "Pharmaceuticals", "available": false }
]
```

The pharmaceutical field set (dosage form, strength, etc.) is a Decision.

---

## 12. Decisions

Answered by the Product Owner (2026-10-30, "Product Workflow & Data Requirements"). Status shows what the backend does today.

| # | Topic | Decision | Status |
|---|---|---|---|
| 1 | Approver | AuthentiCheck platform staff. Flow `draft → in_review → approved \| changes_requested`; reviewers can also reject. Approval does not unlock pharma, batches or code issuance. | Done: `POST /platform/products/{id}/approve \| request-changes \| reject`, review queue `GET /platform/products` |
| 2 | Edit approved product | Industry locked at creation. SKU and GTIN locked after approval (controlled correction). Consumer-visible edits create a new version and return to `in_review`; last approved version stays live. Internal fields editable with audit. | Done: `409 field_locked`, `pending_revision`, `approved_version`; `POST .../correction-request` + `POST /platform/products/{id}/correct`; `suspend` / `unsuspend` |
| 3 | Withdraw | `in_review → draft` before a reviewer decision; records user, time, reason. | Done: `POST .../withdraw` |
| 4 | Size | `size_value` (positive) + `size_unit` (`ml L g kg mg units`); display string generated; pack configuration separate; unit checked against the category. | Done (`size` is read-only) |
| 5 | GTIN | Optional, format + check digit, globally unique with manual conflict review; blocks approval, not saving. SKU unique per tenant. | Done: `409 gtin_conflict` on approve until staff record `gtin_conflict_resolution` |
| 6 | Taxonomy | Served by API, managed by platform admins, no frontend release, deactivate never delete. | Done: `GET /catalogue/industries`, `/platform/industries`, `/platform/categories` |
| 7 | Pharma | Dedicated field set + separate release gate; NAFDAC number is text with verification metadata, no regex. | Fields added (`dosage_form`, `strength`, `pack_configuration`); gate stays locked (`403 industry_locked`) |
| 8 | Support contact | Tenant settings with optional product override; email required, phone encouraged, URL optional. | Done: `GET/PATCH /tenants/me/support-contact`, `support_contact` on product, exposed by `/verify`. **Not enforced yet:** email required before tenant go-live |
| 9 | Roles | Owner, admin, `editor` (drafts; submit only if enabled), least privilege, enforced in the backend. | `editor` + `PATCH /tenants/me/members/{id}` done. Production/Quality and Investigator roles **not added yet** |

**Not covered by the PO answers (defaults kept):** SKU conflict on draft save returns `409 sku_conflict`; SKU uniqueness includes archived products.

## 13. Error codes introduced

| HTTP | `code` | When |
|---|---|---|
| 422 | `validation_error` | field errors; `details` keyed by field |
| 409 | `sku_conflict` | SKU already used in the tenant |
| 409 | `possible_duplicate` | similar product and no `confirm_not_duplicate` |
| 409 | `invalid_status_transition` | e.g. approving a draft |
| 409 | `product_not_editable` | editing a product that is in review |
| 409 | `field_locked` | changing industry, or SKU/GTIN after approval |
| 409 | `gtin_conflict` | approving a product whose GTIN is registered elsewhere |
| 403 | `industry_locked` | pharmaceuticals before the gate opens |
| 413 | `file_too_large` | image over 5 MB |
| 415 | `unsupported_media_type` | image not PNG/JPEG |
| 422 | `image_invalid` | unreadable or out-of-range image |
