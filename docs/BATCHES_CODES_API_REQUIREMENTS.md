# AuthentiCheck – Batches & Codes API Requirements

Backend requirements for the frontend **Batches & codes** tab: the batches list, the batch detail page (issuance job, reconciliation, print exports, deactivation), and the "Issue codes" flow. It lists everything the current API does not yet cover, measured against `ENDPOINT_REFERENCE.md`.

Companion docs: `PRODUCTS_API_REQUIREMENTS.md` (product and batch identity fields) and `INVESTIGATIONS_API_REQUIREMENTS.md`.

**Legend**

- **Required**: the frontend cannot ship the feature without it.
- **Optional**: improves the experience or can be faked on the client for now. The "Without it" note says what the frontend does instead.
- **Decision**: a product or policy choice the backend needs answered first. Collected in [section 11](#11-open-decisions).

**Caveat on "exists today".** `API_CONTRACTS.md` is not in the frontend repo. "Today" is based on `ENDPOINT_REFERENCE.md` and the frontend types (`Batch`: `id, product_id, batch_number, expiry, status, created_at`; `IssuanceJob`: `id, batch_id, quantity_requested, quantity_issued, status, is_reconciled, created_at`; `AuthCode`: `id, code_str, batch_id, job_id, status, scan_count, created_at`). If the contracts already cover an item, strike it.

**Error format.** All new errors use the existing envelope `{ "error": { "code", "message", "details", "request_id" } }`. Codes introduced here are listed in [section 12](#12-error-codes-introduced).

**Times.** ISO 8601 UTC everywhere; the frontend renders WAT.

---

## 1. Gap summary

| # | Capability | Exists today | Priority |
|---|---|---|---|
| 1 | Batches across all products (list + fetch by id) | batches only under `/catalogue/products/{product_id}/batches` | **Required** |
| 2 | New batch fields: `declared_quantity`, `pack_format`, `manufacture_date` | `batch_number`, `expiry`, `status` | **Required** |
| 3 | Per-batch issuance summary counts | none (codes list capped at 500) | **Required** |
| 4 | Job fields: `partial` status, failed count, started by/at, masked key | `quantity_requested`, `quantity_issued`, `status` | **Required** |
| 5 | Resume a partial job | none | **Required** |
| 6 | Job list with batch and product names, filters | `GET /issuance/jobs` shape unknown | **Required** |
| 7 | Label reconciliation (read, update, balance, damaged-active) | `is_reconciled` flag on job | **Required** |
| 8 | Print exports as an entity (list, create, re-download, checksum, download log) | `GET /issuance/jobs/{id}/export` CSV, only when `done` | **Required** |
| 9 | PDF export format and templates | CSV only | **Required** for PDF, optional for extra templates |
| 10 | Deactivation requests with reason, scope and second approver | immediate single and whole-batch deactivate | **Required** |
| 11 | Codes list: cursor pagination, search, filters by export / range / damaged | max 500, filters `batch_id`, `job_id`, `status` | **Required** |
| 12 | Roles beyond owner/admin (Quality lead, Production) | `owner`, `admin` | **Required** (Decision) |
| 13 | Server-computed "needs attention" markers for the list | none | Optional |
| 14 | Per-batch scan activity | `/analytics/scans` (tenant-wide) | Optional |
| 15 | Per-batch history (audit trail) | platform audit only | Write **Required**, read Optional |
| 16 | Replacement codes linked to deactivated originals | none | Optional |
| 17 | Cancel a job | none | Optional (Decision) |

---

## 2. Batches

### 2.1 Endpoints

| Method | Path | Auth | State | R/O | Purpose |
|---|---|---|---|---|---|
| `GET` | `/catalogue/batches` | 🏢 | New | **R** | List batches across all products |
| `GET` | `/catalogue/batches/{id}` | 🏢 | New | **R** | Fetch one batch by id, without needing `product_id` |
| `GET/POST/PATCH` | `/catalogue/products/{product_id}/batches[/{id}]` | 🏢 / 👑 | Existing, extend | **R** | Keep; add the new fields below |
| `POST` | `.../batches/{id}/recall` | 👑 | Existing, extend | **R** | See [section 2.4](#24-recall-and-withdraw) |

`GET /catalogue/batches` query params (all **Required** unless noted):

| Param | Type | Notes |
|---|---|---|
| `q` | string | matches batch number and product name, case-insensitive, partial |
| `status` | enum, repeatable | `active`, `recalled`, `withdrawn`, `expired` |
| `product_id` | uuid | |
| `needs_attention` | bool | batches with any attention marker ([section 2.3](#23-attention-markers)) |
| `ordering` | string | `-created_at` (default), `batch_number`, `best_before` |
| `page`, `page_size` | int | existing pagination envelope |

### 2.2 Batch fields

`R` = required. `O` = optional. (Product-side fields are in `PRODUCTS_API_REQUIREMENTS.md` section 10.)

| Field | Type | R/O | Notes |
|---|---|---|---|
| `id` | uuid | server | |
| `batch_number` | string | R | existing. Unique per tenant. Used in URLs, so no `/` or spaces |
| `product_id` | uuid | R | existing |
| `product_name` | string | server | denormalised "Shea Butter Body Lotion · 400 ml" (name + variant + size) so the list needs no extra calls |
| `manufacture_date` | date | R | new. Feeds the consumer-visibility toggle |
| `expiry` | date | R | existing. Labelled "Best before" in the UI |
| `declared_quantity` | int > 0 | R | new. Units the manufacturer plans to produce |
| `pack_format` | string | R | new. e.g. `Bottle · QR + scratch panel`. Decision: free text vs structured ([section 11](#11-open-decisions)) |
| `status` | enum | server | `active`, `recalled`, `withdrawn`, `expired`. `expired` can be computed from `expiry` |
| `issuance` | object | server | counts, see [section 3](#3-issuance-summary-counts) |
| `reconciled` | bool | server | true once a reconciliation exists and balances |
| `attention` | string[] | O | see [section 2.3](#23-attention-markers) |
| `created_at`, `created_by` | | server | |

### 2.3 Attention markers

**Optional.** The list shows chips: *Partial · resume available*, *Unreconciled*, *Expires soon*, *Approval pending*. Return them as a computed array so the frontend and the `needs_attention` filter agree:

```json
"attention": ["partial_job", "unreconciled", "expiring_soon", "approval_pending"]
```

| Marker | Condition |
|---|---|
| `partial_job` | latest job has `status = partial` |
| `unreconciled` | no reconciliation yet, or it does not balance |
| `expiring_soon` | best-before within 60 days (matches the dashboard's "expire within 60 days") |
| `approval_pending` | at least one deactivation request awaiting approval |

Without it, the frontend derives the first three from the counts and expiry date, and cannot show `approval_pending`.

### 2.4 Recall and withdraw

The mock's button reads **"Recall or withdraw…"**. Existing `POST .../recall` "sets status + deactivates all codes".

- **Decided 2026-11-05: recall and withdraw are the same action.** One `recalled` status, one red consumer screen. `POST .../recall` accepts `reason` (internal) and an optional `consumer_message` (shown to consumers), and stores `recalled_at`. The button can keep its "Recall or withdraw…" label; there is no `action` field. The text below is kept for history.
- ~~**Decision:** are *recall* and *withdraw* two actions? Suggested: *withdraw* = stop sale, codes stay resolvable and consumers see a "withdrawn" message; *recall* = safety/quality recall, consumers see the red recall message. Both are consumer-visible states (design: "withdrawn (red, recall)").~~
- **Required (as built, no `action` field):** body `{ "reason": "...", "consumer_message": "..." }`, an audit entry, and the batch `status` updated. Consumer copy text is **Optional**; without it the frontend uses fixed wording.
- **Decision:** does recall still deactivate every code immediately, or does it go through the approval flow in [section 7](#7-deactivation-with-second-approval)?

---

## 3. Issuance summary counts

**Required.** The batch detail shows six tiles; they cannot be derived from a 500-row code list. Return them on `GET /catalogue/batches/{id}` and on the list rows (list uses `declared_quantity`, `generated`, `failed`).

```json
"issuance": {
  "requested": 20000,
  "generated": 19950,
  "failed": 50,
  "exported": 19950,
  "cancelled": 0,
  "deactivated": 12
}
```

| Count | Meaning |
|---|---|
| `requested` | sum of `quantity_requested` across the batch's non-cancelled jobs |
| `generated` | codes actually created |
| `failed` | `requested − generated` for partial or failed jobs that can still be resumed |
| `exported` | distinct codes included in at least one export |
| `cancelled` | requested codes in cancelled jobs (Decision: see [section 11](#11-open-decisions)) |
| `deactivated` | codes with `status = deactivated` |

---

## 4. Issuance jobs

### 4.1 Endpoints

| Method | Path | Auth | State | R/O | Purpose |
|---|---|---|---|---|---|
| `GET` | `/issuance/jobs` | 🏢 | Existing, extend | **R** | Add filters `batch_id`, `status`; add `batch_number`, `product_name` to each row (the dashboard's "Recent issuance jobs" table needs both) |
| `POST` | `/issuance/jobs` | 👑 | Existing | **R** | Keeps `Idempotency-Key`. See validation below |
| `GET` | `/issuance/jobs/{id}` | 🏢 | Existing, extend | **R** | New fields below |
| `POST` | `/issuance/jobs/{id}/resume` | 👑 | New | **R** | Generate only the missing codes |
| `POST` | `/issuance/jobs/{id}/cancel` | 👑 | New | O | Decision |

### 4.2 Job fields

| Field | Type | R/O | Notes |
|---|---|---|---|
| `status` | enum | R | `pending`, `processing`, `partial`, `done`, `failed`, `cancelled`. **`partial` is new**: some codes generated, some failed, resumable |
| `quantity_requested`, `quantity_generated`, `quantity_failed` | int | R | `quantity_issued` exists today; rename or alias to `quantity_generated` |
| `started_at`, `started_by` | ISO 8601, user ref | R | shown as "Started 22 Oct 2026, 14:05 WAT by Kemi Okoro" |
| `idempotency_key_hint` | string | O | masked, e.g. `idem-5c1e…a9`. Never return the full key |
| `batch_id`, `batch_number`, `product_name` | | R | |
| `is_reconciled` | bool | existing | derive from [section 5](#5-label-reconciliation) |

### 4.3 Resume — Required

`POST /issuance/jobs/{id}/resume` creates **only the missing codes** and must never duplicate codes already made. Requirements:

- Safe to call repeatedly: a second call while a resume is running returns the running state, not a second run. Accept an `Idempotency-Key` header like job creation.
- Allowed only when `status` is `partial` (or `failed` with some codes generated). Otherwise `409 job_not_resumable`.
- Returns `202` with the job in `processing`, then `done` or `partial` again.

### 4.4 Create-job validation — Required

`POST /issuance/jobs` body: `{ "batch_id", "quantity" }` (optionally `"replaces_request_id"`, see [section 8](#8-replacement-codes-optional)).

- Batch must be `active` and its product `approved` (see products doc, status lifecycle). Otherwise `409`.
- **Decision:** may total generated codes exceed `declared_quantity`? Suggested: reject with `409 over_issuance` unless the declared quantity is raised first (audited).

---

## 5. Label reconciliation

**Required.** The team reports what happened to printed labels; the system checks it against what was exported.

### 5.1 Endpoints

| Method | Path | Auth | R/O | Purpose |
|---|---|---|---|---|
| `GET` | `/issuance/batches/{batch_id}/reconciliation` | 🏢 | **R** | Current counts, balance, who reported |
| `PUT` | `/issuance/batches/{batch_id}/reconciliation` | 👑 / Production roles | **R** | Replace the counts ("Update counts") |

`PUT` body and `GET` response:

```json
{
  "printed_applied": 19700,
  "damaged_in_print": 180,
  "unused": 70,
  "reported_by": { "id": "...", "name": "Kemi Okoro" },
  "updated_at": "2026-10-23T09:20:00Z",
  "exported_total": 19950,
  "balance": { "total": 19950, "difference": 0, "balanced": true },
  "damaged_active": 168
}
```

Rules:

- All counts are non-negative integers (`422 reconciliation_invalid`).
- **Unbalanced counts are accepted** and reported (`balanced: false`, `difference`). The UI shows "Off by N"; rejecting would stop teams recording what happened.
- Each update is audited with the previous and new values. Show only the latest on the page.
- Last update wins; send `If-Match` / `version` to avoid silent overwrites (Optional).

### 5.2 Damaged labels still active — Decision first

The mock warns "168 damaged labels are still active" and offers *Review & deactivate*. The system can only know **which codes** are damaged if it is told.

- **Decision:** how are damaged codes identified? Options: (a) team marks serial ranges or uploads a CSV of damaged codes, (b) printer feedback file, (c) count only (no code list, so no "Review & deactivate" pre-selection).
- With (a) or (b): `damaged_active = damaged codes − deactivated damaged codes` (180 − 12 = 168 in the mock), and `GET /issuance/codes?batch_id=&damaged=true&status=active` returns them for pre-selection.
- With (c): drop `damaged_active` and the one-click review; the frontend shows the count only.

---

## 6. Print exports

**Required.** An export is an immutable, auditable artifact.

### 6.1 Endpoints

| Method | Path | Auth | State | R/O | Purpose |
|---|---|---|---|---|---|
| `GET` | `/issuance/batches/{batch_id}/exports` | 🏢 | New | **R** | List exports for a batch |
| `POST` | `/issuance/batches/{batch_id}/exports` | 👑 | New | **R** | Create an export. Accepts `Idempotency-Key` |
| `GET` | `/issuance/exports/{id}` | 🏢 | New | **R** | Status and metadata |
| `GET` | `/issuance/exports/{id}/download` | 🏢 | New | **R** | Download the file; each call is logged |
| `GET` | `/issuance/jobs/{id}/export` | 🏢 | Existing | O | Keep for compatibility, or point at the new entity |

### 6.2 Export fields

| Field | Type | R/O | Notes |
|---|---|---|---|
| `id` | string | server | human-readable, e.g. `EXP-3301` |
| `format` | enum | R | `print_pdf`, `printer_csv` |
| `template` | string | R | e.g. `v3`. Decision: who owns templates ([section 11](#11-open-decisions)) |
| `layout` | string | R for PDF | e.g. `40x40mm` |
| `code_count` | int | server | |
| `first_serial`, `last_serial` | string \| int | server | the code range this export covers (needed for "A range from an export" in [section 7](#7-deactivation-with-second-approval)) |
| `status` | enum | server | `pending`, `ready`, `failed`. PDF generation of ~20,000 labels is slow, so make it async |
| `created_at`, `created_by` | | server | |
| `sha256` | string | server | of the delivered file. Shown shortened (`9f2c…e41a`) |
| `download_count` | int | server | |

### 6.3 Rules — Required

- **Re-download returns the byte-identical file and the same codes.** Store the generated file; never regenerate on download.
- **Every download is logged** against the user and time (the page promises this). Optional: `GET /issuance/exports/{id}/downloads` to show who.
- **Exports are allowed for partial jobs.** The mock exports 19,950 of 20,000. Today export is allowed only when the job is `done`.
- Creating an export does not change code statuses, but `exported` in [section 3](#3-issuance-summary-counts) must count them.
- **Decision:** may the same codes appear in two exports (the mock shows a PDF and a CSV of the same 19,950)? Yes in the mock; confirm it does not count twice in `exported`.

### 6.4 Create body

```json
{ "format": "print_pdf", "template": "v3", "layout": "40x40mm", "job_id": "..." }
```

`job_id` is optional (default: all generated, not-yet-exported codes for the batch). Optional: `first_serial` / `last_serial` to export a sub-range.

---

## 7. Deactivation with second approval

**Required.** The design: *"A second approver (Owner or Quality lead) must confirm. Consumers checking these codes will see 'This code is no longer active'."* Today `POST /issuance/codes/{code}/deactivate` and `POST /issuance/batches/{batch_id}/deactivate` act immediately, with no reason and no approval.

### 7.1 Endpoints

| Method | Path | Auth | R/O | Purpose |
|---|---|---|---|---|
| `POST` | `/issuance/deactivation-requests` | 👑 / Quality roles | **R** | Request deactivation |
| `GET` | `/issuance/deactivation-requests` | 🏢 | **R** | List (filters `status`, `batch_id`) |
| `GET` | `/issuance/deactivation-requests/{id}` | 🏢 | **R** | Detail |
| `POST` | `/issuance/deactivation-requests/{id}/approve` | Owner / Quality lead | **R** | Confirm; deactivation runs |
| `POST` | `/issuance/deactivation-requests/{id}/reject` | Owner / Quality lead | **R** | Reject with a reason |
| `POST` | `/issuance/deactivation-requests/{id}/cancel` | requester | O | Withdraw a pending request |

### 7.2 Create body

```json
{
  "batch_id": "...",
  "scope": { "type": "codes", "codes": ["7KQ2-M9XW-4PRT", "..."] },
  "reason": "damaged_during_printing",
  "note": "Roll 14 jammed on line 2"
}
```

`scope.type`:

| Type | Payload | Mock label |
|---|---|---|
| `codes` | `codes: string[]` or `code_ids` (cap, e.g. 5,000; larger sets use `damaged` or an export range) | "Selected codes (168)" |
| `damaged` | none; resolves to the batch's damaged-and-active codes ([section 5.2](#52-damaged-labels-still-active--decision-first)) | "Review & deactivate" shortcut |
| `export_range` | `export_id`, `from`, `to` (serials within that export) | "A range from an export" |
| `batch` | none; all active codes in the batch | "Whole batch (19,950)" |

`reason` enum: `damaged_during_printing`, `exposed_or_leaked`, `misprinted`, `misapplied`, `other`. `note` is **optional**, but **required when `reason = other`**. The note goes to the audit log; remind users not to include personal data.

The response returns the request with the **resolved code count** so the approver sees exactly what will be deactivated: `{ "id", "status": "pending", "code_count": 168, "requested_by", "requested_at", "scope", "reason", "note" }`.

### 7.3 Rules — Required

- `status`: `pending` → `approved` | `rejected` | `cancelled` | `expired` (Decision: expiry window).
- **The approver must be a different user from the requester** (`403 self_approval_not_allowed`).
- Approver roles: Owner or Quality lead (see [section 9](#9-roles-and-permissions)).
- **Nothing is deactivated until approval.** On approve, deactivation is applied atomically for ≤ some threshold, or as an async job for large scopes (return `202` and a `status: applying`).
- Deactivated codes are **never reused**.
- Consumer-facing result: `GET /verify/{code}` returns the existing `deactivated` result.
- Re-requesting codes already deactivated returns them as `already_deactivated` and does not count them.
- Audit: request, approval or rejection, and the final applied count, each with user and time.
- **Decision:** are the old immediate endpoints removed, restricted to Owners, or kept for emergencies? Same question for **batch recall**.

---

## 8. Replacement codes (optional)

**Optional.** The design says *"replacements get new codes linked to the originals."* Suggested: `POST /issuance/jobs` accepts `replaces_request_id`, and each new code stores `replaces_code_id`. Without it, replacements are ordinary new codes and the link is lost.

---

## 9. Roles and permissions

**Required (Decision).** The documented roles are `owner` and `admin` (👑). The mock's sidebar shows a user as **"Production · quality"**, and deactivation approvals name **Owner or Quality lead**.

| Capability | Suggested minimum role |
|---|---|
| View batches, jobs, exports, requests | any active member (🏢) |
| Create batch, issue codes, resume a job, create exports | owner, admin, `production` |
| Update reconciliation counts | owner, admin, `production` |
| Request deactivation | owner, admin, `quality_lead`, `production` |
| Approve or reject deactivation | owner, `quality_lead` (never the requester) |
| Recall or withdraw a batch | owner, `quality_lead` |

`GET /tenants/me/members` is 👑-only today; the app also needs the member's role on `/auth/me` memberships (already present as `role`).

---

## 10. Codes list, scan activity and history

### 10.1 Codes list — Required

`GET /issuance/codes` is capped at 500. Needed for the "selected codes" workflow:

| Param | Notes |
|---|---|
| `cursor`, `limit` | cursor pagination with no 500 cap (page size ≤ 500) |
| `q` | find a code by string (full or partial, ignoring dashes) |
| `batch_id`, `job_id`, `status` | existing |
| `export_id` | codes included in an export |
| `serial_from`, `serial_to` | a range within the batch |
| `damaged` | bool, only if [section 5.2](#52-damaged-labels-still-active--decision-first) option (a) or (b) |

**Decision:** which roles may see full code strings? Suggested: full codes only for roles that print and approve; others see a masked form (`7KQ2-…-4PRT`).

### 10.2 Scan activity per batch — Optional

`GET /analytics/scans?batch_id=&days=` returns first vs repeat checks per day for one batch. Without it the Scan activity tab stays a placeholder.

### 10.3 History per batch

- **Required (write):** audit entries for batch create/update, job start/resume, export create and download, reconciliation update, deactivation request/approve/reject, recall/withdraw.
- **Optional (read):** `GET /catalogue/batches/{id}/history`:

```json
{ "results": [
  { "at": "2026-10-22T13:05:00Z", "actor": { "id": "...", "name": "Kemi Okoro" },
    "action": "issuance.job_started", "summary": "Started issuance job JOB-8K2F for 20,000 codes" }
] }
```

Without it the History tab is hidden.

---

## 11. Open decisions

1. **Recall vs withdraw:** one action or two? Do they bypass the approval flow? ([section 2.4](#24-recall-and-withdraw))
2. **Over-issuance:** may generated codes exceed `declared_quantity`? ([section 4.4](#44-create-job-validation--required))
3. **Damaged codes:** how does the system learn which codes are damaged? ([section 5.2](#52-damaged-labels-still-active--decision-first))
4. **Cancelled:** what produces a cancelled count, and is there a cancel-job action?
5. **Pack format:** free text, or structured (`pack_type` + `data_carrier`)? The mock's "Bottle · QR + scratch panel" reads like two fields.
6. **Export templates:** who owns them (AuthentiCheck-defined, or per tenant)? Which formats beyond PDF 40×40 mm and printer CSV?
7. **Same codes in two exports:** confirm allowed and counted once in `exported`.
8. **Approval:** expiry window for pending requests; approver roles; do old immediate deactivate endpoints stay?
9. **Roles:** names and permissions in [section 9](#9-roles-and-permissions).
10. **Code visibility:** who sees full code strings?
11. **Large scopes:** size above which deactivation runs as an async job.

## 12. Error codes introduced

| HTTP | `code` | When |
|---|---|---|
| 409 | `job_not_resumable` | resume on a job that is not `partial` |
| 409 | `over_issuance` | job would exceed `declared_quantity` (if Decision 2 = reject) |
| 409 | `batch_not_active` | issuing codes for a recalled, withdrawn or expired batch |
| 422 | `reconciliation_invalid` | negative or non-integer counts |
| 409 | `export_not_ready` | download requested while `status != ready` |
| 422 | `export_range_invalid` | range outside the export, or `from > to` |
| 422 | `code_not_in_batch` | selected code does not belong to `batch_id` |
| 403 | `self_approval_not_allowed` | requester tries to approve their own request |
| 409 | `request_not_pending` | approving or rejecting a request that is not `pending` |
| 403 | `insufficient_role` | role cannot perform the action |
