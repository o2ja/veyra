# F1 notes: S7.1-S7.3 claims core (branch `s7-claims`)

Deviations, ADR text and doc deltas for the orchestrator to merge. Nothing here has been applied to the main docs.

## Doc deltas

### DATABASE_SCHEMA.md §3.8 / §7
- `claim_export_templates` gains `revision int` (If-Match), `created_by`, `created_at`, `updated_at`; `UNIQUE (tenant_id, manufacturer_id, name)`; never deleted (`active=false` retires).
- `claim_batches.submitted_at` is a **date** (the submission date the user records, FR-CB-04), not a timestamp; `submitted_by` is set with it. `external_reference` is required for `submitted` (CHECK). The moment of freezing is `claim_lines.frozen_at` plus the audit entry.
- `claim_lines.claim_amount >= 0` (CHECK). `calculation_result_id` stays NOT NULL as documented, which means a `rebate_accrual` claim line cannot exist yet (an accrual entry has no calculation result): rebate claims need either a nullable `calculation_result_id` for that source kind or an accrual-backed key. **Open for S7.4 / the owner**; S7.3 batches only build `sales_line` claim lines. The schema (CHECKs, partial unique index on `rebate_accrual_entry_id`) is ready.
- Triggers (names as in migrations `20261115_0910_claim_batches`, `20261115_0920_claim_lines`):
  - batches: insert only as `draft`; update limited to workflow columns; transitions `draft->approved->submitted`, `draft|approved->void` each may set only its own columns (+ export link); `submitted` changes only `external_reference`; `void` never changes; no delete/truncate.
  - lines: insert only into a `draft` batch; afterwards only `frozen_at` (only while the batch is `submitted`) and `released_at` (once, only while the batch is `draft|approved|void`) can be set; frozen lines never change; no delete/truncate.
  - Deferred constraint trigger on batches **and** lines (once per batch and transaction): `line_count`/`total_amount` equal the lines, lines are frozen exactly when the batch is `submitted`, released exactly when it is `void`. Totals therefore tie out in the database at commit.
- `claim_adjustments.claim_line_id` and `.included_in_batch_id` now have composite FKs (placeholders since S5).

### API_SPECIFICATION.md §2.8
- `GET /missed-claims` filters: `manufacturer_id`, `agreement_id`, `invoice_from`, `invoice_to`, `limit`, `cursor`, `format=csv|xlsx` (202 export job). Rows are the **newest result per sales line across completed runs** that is `eligible` and has no active claim line; `id` is the calculation result id (pass it as `result_ids`). `GET /missed-claims/summary`: `totals`, `groups` (manufacturer x agreement), `exceptions` by code, `lost_to_expiry`.
- Export jobs return `{job:{id,status,progress,result_url}, file_id}` where `result_url = /api/v1/files/{file_id}`: the file id is allocated at request time and the `file_objects` row (kind `export`, `clean`, written by the system) appears when the job has run (404 before).
- `POST /claim-batches`: exactly one of `result_ids[]` / `filter{agreement_id,invoice_from,invoice_to}`; `Idempotency-Key` required. 422 details: `not_found`, `wrong_manufacturer`, `not_eligible`, `stale_result` (a newer run supersedes the result), `no_lines`, `too_many_lines` (20,000). 409 `already_claimed` details list `result_id`, `sales_document_line_id`, `claim_reference`, `batch_number`, `batch_id`. `include_adjustment_ids` is intentionally absent (S7.4).
- `POST /claim-batches/{id}/approve`: four-eyes against the creator (403 `four_eyes_violation`, audited and persisted). `export`: body `{template_id?}`; allowed in `approved|submitted` (409 `invalid_state` otherwise); without a template the manufacturer's newest active template, else a built-in CSV layout. A submitted batch can be exported again but the file is not linked (the batch is immutable). `submit`: `{submitted_at (date, not future), external_reference}`, `Idempotency-Key` required; sending the same details again returns the batch unchanged, other details 409 `invalid_state`. `void`: `{reason}`.
- Templates: `GET/POST /claim-export-templates`, `PUT /claim-export-templates/{export_template_id}` (path parameter renamed to avoid clashing with the mapping-template id in the isolation matrix); all three need `claim_batch.create` (A P N as documented); `PUT` needs `If-Match`; columns are `{header, source|constant, format?}`, sources limited to a fixed field registry (`exporting.FIELDS`), formats `iso|us|eu` for dates and `decimal:0..6` for numbers; options `include_header`, `delimiter`, `sheet_name`.
- OpenAPI component names `BatchSubmitIn`, `BatchVoidIn`, `BatchExportIn`, `ExportTemplate*` avoid collisions with agreements/ingestion models. Client regenerated.

### SECURITY.md §4 (exports)
Escaping is applied to *text* cells (headers, template constants, text fields): prefix `'` for `= + - @ TAB CR`. Numbers and dates are typed cells, so a negative amount is never prefixed (CSV: written as plain decimal text, xlsx: numeric cell).

### Audit events added
`claim_batch.created|approved|four_eyes_violation|export_requested|exported|submitted|voided`, `claim_export_template.created|updated`, `missed_claims.export_requested|exported`.

## Interpretations (decided here, recorded for review)
- "Latest completed run for the scope" (BUSINESS_LOGIC §11) is implemented per sales line (newest result over all completed runs) so differently scoped runs compose; superseded source-line versions never count. Cost: a `DISTINCT ON` over the tenant's results per request (comment in `missed.py`; projection if S11 measures it slow).
- Claim reference `CL-YYYY-NNNNNN` and batch number `CB-YYYY-NNNNNN`, year = business-date year, numbering continues per tenant and year, assigned under a per-tenant advisory lock (concurrent creates are serial; the partial unique index remains the real double-claim guard).
- A batch's lines are fixed at creation.

## Not done here (next agents)
- S7.4: adjustments, `include_adjustment_ids`, swapping `claims.frozen.source` (still `NoFrozenClaims`) for a `claim_lines` reader so runs see submitted claims (`prior_claims`, `consumption`, `frozen_claims`), `source_changed` wiring.
- S7.5 frontend; Playwright W8-W9.
- Rebate-accrual claim lines (see schema note above).
- Audit pack report (`/reports/claim-batches/{id}/audit-pack`) belongs to reporting.
