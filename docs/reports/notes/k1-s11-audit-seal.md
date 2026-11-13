# K1 / S11.1 audit seal, verification, retention (notes)

Branch `s11-audit-seal`. Migration `20261115_0900_audit_seals` (down_revision `20261114_0900_calc_runs`; rebase the
down_revision if another branch lands a migration first).

## Built
- `audit_seals` (RLS, append-only trigger, no UPDATE/DELETE/TRUNCATE for the app role). The table was not in any migration yet.
  The existing audit table is `audit_log` (docs say `audit_logs` in places).
- `modules/audit/seal.py`: canonical form + chain (documented in the module docstring and `DATABASE_SCHEMA.md`), `seal_tenant`, `verify_tenant`, `retention_report`.
- Job `audit_seal` (tenant-scoped, hourly, self-rescheduling, dedupe key per run time, immediate continuation when more than 50 chunks of 1000 are due). First run: migration
  backfills one job per existing tenant; `create_workspace` enqueues it for new ones.
- CLI `python -m rebate.modules.audit.cli verify|retention-report --tenant <uuid>` (verify exits 1 on a break). Runbook `infra/runbooks/audit-seal-verify.md`.
- Isolation: `audit_seals` in `TENANT_TABLES`, seeded per tenant, app-role mutation attempts added to D9, table-set meta test updated.

## Decisions taken
- One running hash chain per tenant (ADR-026 formula `H(prev_hash || canonical_entry)`); the seal stores the last hash. Consequence: a break is located to a seal range (up to 1000 entries), not a single entry.
- Entries younger than 5 minutes are not sealed (a lower id committing late must not land inside a sealed range). Ceiling: transactions longer than 5 min (API timeout is 30 s).
- No FK from audit_seals to audit_log (so a future audited purge can remove entries with their seals).
- Retention report covers audit_log, file_objects, import_batches, calculation_runs by their own timestamp.

## Not done: deletion path. Owner decisions needed (docs do not specify)
1. Which tables/dates count as "financial records" and when they age out (document date? claim settlement? run date?).
2. Workspace deletion: no status/`deleted_at` on tenants, no request/approval flow, no definition of the 30-day grace trigger, who may cancel it.
3. Deletion-certificate record: no table, fields, or retention for it (it must outlive the tenant, so it cannot be a tenant-RLS row).
4. Purge needs a DB role with DELETE (app role has none; tables are append-only/immutable) and object-store purge rules incl. backups.
5. Audit-log purge vs seals: how a purged range is represented so verification stays meaningful (proposal: certificate records the last purged seal hash as the new chain anchor).

## Proposed ADR-026 amendment (owner to accept; the ADR's decision text is not edited)
ADR-026 says "per-tenant monotonic `seq`" and `entry_hash = H(prev_hash || canonical_entry)`. As built:
- No per-tenant `seq`: `audit_log.id` is a global identity column; a tenant's chain orders by `id` (gaps are normal). Ranges are `[from_audit_id, to_audit_id]`.
- One running hash per tenant; `audit_seals` stores one hash per seal (the last `h`), not one `entry_hash` per entry. A break is located to a seal range.
- No key (public algorithm): anyone who can bypass the trigger can rewrite and re-seal; integrity is proven only back to the last off-box anchor (`verify --anchor`).
- Sealing waits 5 minutes and is skipped while another transaction older than that is open; verification checks range coverage.

## Review round 1 fixes
B1 migration lifts and restores FORCE RLS on tenants around the backfill (test: previous revision -> head gives exactly one queued job). S2 settle check + coverage check. S3 `--anchor`. S4 plain no-key warning in docs and runbook. M6 jsonb columns hashed as `::text`. M7 `on_dead` logs and schedules a new run. M8 tests added.
