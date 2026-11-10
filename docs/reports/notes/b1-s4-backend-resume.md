# b1-s4-backend: resume notes (WIP, usage limit)

## State (2026-11-10)
- Branch `s4-backend` = main + merge of `origin/s3-backend` (head migration `20261111_1000_import_pipeline`).
- **No S4 code written yet.** Only reading/design done. Everything below is the design to implement.
- Isolation negative control: NOT run (orchestrator said wait for S3 agent).

## Next steps (in order)
1. Migration `20261112_0900_agreements` (down_revision `20261111_1000_import_pipeline`), raw SQL like
   `20261111_0900_masterdata.py`; models in `modules/agreements/models.py`; add the models import to
   `migrations/env.py` (outside owned files: report it).
2. `modules/agreements/{schemas,validator,repository?,service,policies,router,jobs}.py`, register router in
   `api/app.py` MODULE_ROUTERS and module import in `workers/__main__.py`; add `agreements-internals`
   contract to `importlinter.ini`; Action entries + AUDITED_FIELDS (`agreement`, `agreement_version`,
   `agreement_draft`) in `modules/audit/events.py`.
3. `modules/ingestion/__init__.py`: export `readers`, `normalizers`, idempotency `parse_key/replay/store`
   (public-interface addition only; report; recommend moving idempotency to core).
4. Tests (`tests/integration/test_agreements*.py` using `tests/integration/conftest.py` ws fixture), isolation
   extension (TENANT_TABLES, S4 fixture rows in `tests/isolation/conftest.py`, S4 D-series, route PARAM_SOURCES
   `draft_id`, `agreement_id`, `version_id`, `other_version_id`; `_call` must send Idempotency-Key for approve),
   raise `MIN_TESTS` in negative_control.py.
5. Docs: DATABASE_SCHEMA.md §3.6/§7, API_SPECIFICATION.md §2.5; deltas into `docs/reports/notes/b1-s4-backend.md`.
6. `make gen-client` equivalent, lint gates, full suite once, merge origin/s3-backend again, push, PR, CI.

## Design decisions (decided alone, record in report)
- Schema per DATABASE_SCHEMA §3.6 plus: `agreement_versions.created_txid xid8` (BEFORE INSERT forces
  `pg_current_xact_id()`); `agreement_drafts.review_comment` (return comment visible to analysts);
  `agreement_drafts.extraction_id` plain uuid (FK added in S6).
- Exclusion constraint `WHERE status IN ('active','expired')` (stronger than doc: expired versions still
  govern their period; S5 loaders should use active+expired). Report as deviation.
- `agreement_versions` trigger: only `status` (active->superseded/expired/cancelled, expired->superseded),
  `superseded_at`, `superseded_by_version_id` (set once) may change; no DELETE/TRUNCATE grant.
- items/customers/tiers: no UPDATE/DELETE (revoked + fn_forbid_mutation); BEFORE INSERT guard: parent must
  have `created_txid = pg_current_xact_id()`; if parent not visible RETURN NEW (let RLS/FK reject, so the
  isolation D5 test sees "rls").
- drafts/agreements: no DELETE grant. revisions: `make_append_only`.
- Validator: Pydantic strict payload model (extra=forbid, money as decimal strings, StrictInt/StrictBool);
  rules via engine `parse_spa_rules`/`parse_rebate_rules` (UnsupportedRuleError -> `not_supported_until_release_2`,
  incremental+qualify_only RulesError -> `incompatible`); final conformance check through
  `spa_version_from_payload`/`rebate_version_from_payload` with resolved ids. References via
  `masterdata.master_index`. Errors block submit/approve; warnings need acknowledgement at submit.
  Item payload has no customer_code (engine has none) -> `agreement_version_items.customer_id` always NULL;
  tier `flat_amount` never written (engine Tier has no flat_amount): report both as doc/engine mismatches.
- Split-supersede generalised: left piece and right piece copies (`derived_from_version_id`), new version
  `supersedes_version_id`; amended version may be active or expired; agreement row locked FOR UPDATE.
- content_hash: sha256 of canonical terms (type, priority, rules, items by product_id, customers, tiers),
  validity excluded so split copies share the hash.
- Four-eyes: approver not in {created_by} U revision editors; violation audited via a `commit=True` subclass.
- Items import: synchronous, all-or-nothing, max 20,000 rows, `dry_run` preview, explicit `rate_format`
  (fraction|percent), header aliases (Catalog # -> sku, Margin % -> rate); If-Match required. Saved mapping
  templates for `agreement_items` need an entry in ingestion `FIELDS` (not owned): report.
- Expiry sweep: tenant-scoped self-rescheduling job `agreements_expiry_sweep`, dedupe key
  `agreements_expiry_sweep:{tenant}:{date}`, payload `{"since": date}`; first enqueued on approval; expires
  versions with valid_to < business today; alert hook (audit `agreement_version.expiry_alert`, 30 days) for
  versions entering the window since the last run. S9 replaces the hook with notifications.
- API `POST /agreement-drafts` with `source=ai_extraction` -> 409 `ai_not_enabled` until S6; service accepts it.
- preview-calculation is S5 (C1), not built here.
