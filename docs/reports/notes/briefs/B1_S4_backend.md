# B1 — S4 agreements: shared draft pipeline (BACKEND)

Agent id: `b1-s4-backend`. Branch: `s4-backend`. Test DB: `rebate_test_b1`. You MAY run the isolation suite and its
negative control (only DB agent using `rebate_negctl` this wave).

## Read
`CLAUDE.md`; `IMPLEMENTATION_PLAN.md` §S4 (S4.1–S4.7, S4.9 backend parts; acceptance); `PRODUCT_REQUIREMENTS.md` §1.5;
`BUSINESS_LOGIC.md` §3, §8–§9; `DATABASE_SCHEMA.md` §3.6, §7; ADR-009/010/011 (`ARCHITECTURE_DECISIONS.md`);
`API_SPECIFICATION.md` (agreements, drafts, approval, If-Match); `SECURITY.md` (four-eyes, permissions, audit);
`testdata/generated/agreements/manual_payloads/*.json`, `SYNTHETIC_spa_brightline_items.xlsx`.
Existing code to reuse: `modules/masterdata` and `modules/ingestion` (S3, already on main: reference resolution and
the import pipeline/mapping templates for SPA item bulk import), `modules/files`, `core/jobs.py`, isolation suite.
`backend/src/rebate_engine/rules.py`/`models.py` (on main) parse the same payload shape — the Pydantic payload
schema must accept exactly what the engine consumes; do not edit the engine — report mismatches.

## Build (backend)
- S4.1 schema: `agreements`, `agreement_versions` (generated `valid_range`, exclusion constraint against
  overlap, immutability trigger), `agreement_version_items/customers/tiers`, `agreement_drafts`,
  `agreement_draft_revisions`; RLS on all.
- S4.2 Pydantic payload schema with `payload_schema_version`; `AgreementValidator` with every rule in
  BUSINESS_LOGIC §3.3 (errors vs warnings; reference resolution report for unresolved customers/SKUs).
- S4.3 draft service: create (manual / spreadsheet_template), update with `If-Match`, revisions, validate, submit
  (acknowledgements), return, reject.
- S4.4 SPA item bulk import using the S3 import pipeline (template + mapping).
- S4.5 approval: permission + four-eyes, re-validation, content hash, version creation, split-supersede in one
  transaction, audit; cancel.
- S4.6 expiry sweep job (statuses, notification hook via jobs).
- S4.7 version history and field-level diff API.
- Drafts must also accept `source=ai_extraction` rows later (S6) through the same pipeline — keep the source enum
  and validator shared; nothing AI-specific here.
- Permissions on every route, audit on every mutation, isolation suite extended to every new table and route,
  negative control still detecting breaches.

## Tests (S4.9 backend)
Every validator rule (positive and negative); exclusion constraint (overlap rejected, adjacency allowed);
split-supersede v1a/v2 correctness; immutability triggers (active versions cannot be altered even with direct SQL
as `rebate_app`); four-eyes; concurrency (two parallel approvals of the same draft — exactly one wins; real
connections); all `manual_payloads/*.json` accepted and approved end to end via the API; mutated invalid ones
rejected with precise errors; template path (spreadsheet) for the brightline items.

## You own
`backend/src/rebate/modules/agreements/**`, new migrations (start after the current head on main; check
`alembic heads`), registration lines in api/app.py routers / `workers/__main__.py`, permission catalog / audit
`Action` enum (add only), `backend/importlinter.ini` (agreements contract), `backend/tests/**` except
`tests/engine*`, `tests/extraction/**`, `frontend/src/api/**` (regenerate only), `docs/DATABASE_SCHEMA.md`,
`docs/API_SPECIFICATION.md` (S4 sections). Do not edit `modules/masterdata|ingestion` except adding to their public
`__init__` interface if strictly needed (state it in the report).
