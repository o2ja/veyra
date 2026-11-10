# b1-s4-backend: S4 agreements backend — notes, deviations, doc deltas

Branch `s4-backend`. Migration `20261112_0900_agreements` (down_revision `20261111_1000_import_pipeline`).
Doc changes already made in this branch (owned): `DATABASE_SCHEMA.md` §3.6/§7 implementation notes,
`API_SPECIFICATION.md` §2.5 implementation notes.

## Deviations from the docs (decided under §18.1; owner/orchestrator to confirm)

1. **Exclusion constraint covers `active` and `expired`** (doc: `WHERE status = 'active'`). Expired versions
   still govern their period; letting a new active version overlap an expired one would create two governing
   versions. S5 loaders must read `active` + `expired` versions.
2. **`agreement_versions.created_txid bigint`** (not in the doc): forced by trigger; the child-table trigger
   accepts items/customers/tiers only from the transaction that created the version. Stronger than "trigger
   checks parent status": terms cannot be added after approval either.
3. **Children are fully append-only** (no UPDATE/DELETE grant + `fn_forbid_mutation`), whatever the parent status.
4. **`agreement_drafts.review_comment`, `created_at`, `updated_at`** added. `extraction_id` without FK until S6.
5. **No DELETE grant** on `agreements`, `agreement_drafts`; versions: UPDATE only on
   `(status, superseded_at, superseded_by_version_id)` (column grant, like the S3 source tables).
6. **Split-supersede generalised** (BUSINESS_LOGIC §9.2 describes the mid-period case only): the amended version
   (active *or* expired) is superseded; a left piece and/or a right piece with identical terms are created
   (`derived_from_version_id`); version numbers are chronological (v1a, v2, v1b). A new version with
   `valid_to` before the business date is created `expired`.
7. **Four-eyes makers = creator ∪ every revision editor** (SECURITY.md: "created or last edited"); stricter.
8. **Drafts are stored even when the payload does not validate** (needed for S6 extraction drafts and
   work-in-progress); PUT returns 200 with `validation`. Errors block submit/approve.
9. **Unresolved customer/SKU references are errors** (block submit, not only approval).
10. **Validator additions beyond §3.3** (all needed to refuse what the engine/DB cannot hold): decimal strings
    must fit their NUMERIC columns (scale/int digits); tiers numbered 1..n; SPA has no tiers and rebate has no
    customers; `customer_scope=listed` needs customers, `all` takes none; rebate `product_scope=listed` needs
    items, `all` takes none; header type = rules type; `rules.adapter` must be registered (registry empty →
    any adapter refused); amendment must match the agreement (number/manufacturer/type), overlap the amended
    version, and the amended version must still be in force; new agreement number must not exist.
    Warnings: `already_expired`, `measurement_period_mismatch`, `product_manufacturer_mismatch`.
11. **Cancel** (`POST /agreements/{id}/cancel`): versions starting on/after `effective_date` → `cancelled`; a
    spanning version is superseded by a copy ending the day before; any date accepted (also retroactive).
12. **Content hash excludes validity** so split copies share the hash of their source.
13. **"By template"** interpreted as: draft `source=spreadsheet_template` with `source_file_id`, header/rules
    entered, items from the spreadsheet via `items:import`. There is no spreadsheet format for the header/rules.
14. **Expiry alert hook = audit entry** `agreement_version.expiry_alert` (30 days, fixed constant); S9 replaces it
    with notifications. Sweep job `agreements_expiry_sweep`, dedupe `agreements_expiry_sweep:{tenant}:{date}`,
    self-rescheduling daily at 00:05 business time.
15. **`policies.py` / `repository.py` not created**: permissions are the existing registry entries
    (`agreement.draft`, `agreement.approve` covers approve/reject/return/cancel), declared per route; queries live
    in the service (same as `masterdata`).
16. **No draft cancel endpoint** (status `cancelled` exists in the CHECK; not in the API spec).

## Doc / engine mismatches (engine not edited)

- Payload item has no `customer_code` and the engine `SpaItem` has no customer → `agreement_version_items.customer_id`
  always NULL in Release 1 (doc column exists for item-level restriction).
- Engine `Tier` has no `flat_amount` → payload tiers accept `tier_number, lower_bound, rate` only;
  `agreement_version_tiers.flat_amount` never written.
- `measurement_period.kind` is free text (≤50) — not enumerated anywhere in the docs; the engine ignores it.
- Engine `parse_rebate_rules` reports incremental+qualify_only as a plain `RulesError`; the validator maps it to
  `incompatible` by message text (an engine-side code would be cleaner).

## Changes outside owned files

- `backend/src/rebate/modules/ingestion/__init__.py`: public interface additions only (readers/normalisers
  functions and `parse_key/replay/store` of the idempotency helper) — import-linter forbids importing the
  submodules. Recommendation: move the idempotency helper into `core/` (second user now exists).
- `backend/migrations/env.py`: import of `rebate.modules.agreements.models` (needed for `alembic check`).
- `docs/API_SPECIFICATION.md` §2.5 and `docs/DATABASE_SCHEMA.md` §3.6/§7: owned per brief.

## Gaps / follow-ups

- Saved mapping templates for SPA items (`mapping_templates.import_type = agreement_items`) need an entry in the
  ingestion field catalogue (`ingestion.validators.FIELDS`, not owned); `items:import` takes an explicit
  `mapping` or recognises column names instead.
- `GET /agreement-drafts/{id}/preview-calculation` is S5.
- Draft payload limit 5 MB is checked in the service; there is no generic JSON body-size middleware.
- `rebate_ai_writer` grants on drafts/revisions: S6.
