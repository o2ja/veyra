# Audit seal verification and retention dry run (S11.1)

Design: ADR-026, `docs/DATABASE_SCHEMA.md` (audit_seals), `docs/SECURITY.md` (audit).

The `audit_seal` job runs hourly per workspace and seals audit entries older than 5 minutes into a SHA-256 chain.
Check a tenant from the api image (application role, tenant context set by the tool):

    docker compose exec api python -m rebate.modules.audit.cli verify --tenant <tenant-uuid>

Output is JSON (`ok`, `seals`, `head_hash`, `unsealed_entries`, `failure`, `failed_seal_id`). Exit 0 = chain intact; exit 1 = the
first broken seal is named. `unsealed_entries` above a few hundred for long means the job is stuck: look at the
Jobs page for a failed `audit_seal` job and retry it.

On exit 1: do not edit anything. Treat as a security incident (`incident-tenant-data-exposure.md`), take a database snapshot,
and compare with the latest backup (`backup-restore.md`) to see which entry or seal differs.

Limit: deleting only the newest seal(s) is not visible in the database. Record `head_hash` off-box regularly (monthly) and compare.

Retention dry run (deletes nothing):

    docker compose exec api python -m rebate.modules.audit.cli retention-report --tenant <tenant-uuid>
