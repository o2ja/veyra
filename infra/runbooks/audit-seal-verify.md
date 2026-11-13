# Audit seal verification and retention dry run (S11.1)

Design: ADR-026, `docs/DATABASE_SCHEMA.md` (audit_seals), `docs/SECURITY.md` (audit).

The `audit_seal` job runs hourly per workspace and seals audit entries older than 5 minutes into a SHA-256 chain.
Check a tenant from the api image (application role, tenant context set by the tool):

    docker compose exec api python -m rebate.modules.audit.cli verify --tenant <tenant-uuid> --anchor <seal_hash kept off-box>

`--anchor` is optional but is the only protection against deleted newest seals or a re-sealed history: it fails unless the chain contains a verified seal with that hash.

Output is JSON (`ok`, `seals`, `head_hash`, `unsealed_entries`, `failure`, `failed_seal_id`). Exit 0 = chain intact; exit 1 = the
first broken seal is named. `unsealed_entries` above a few hundred for long means the job is stuck: look at the
Jobs page for a failed `audit_seal` job and retry it.

On exit 1: do not edit anything. Treat as a security incident (`incident-tenant-data-exposure.md`), take a database snapshot,
and compare with the latest backup (`backup-restore.md`) to see which entry or seal differs.

Limit (read this): There is no key and the algorithm is public: anyone who can bypass the append-only trigger (the table owner or a superuser) can rewrite entries and re-seal them. Integrity is proven only back to the last seal hash kept off-box (`verify --anchor`); everything after the last anchor is tamper-evident only against people who cannot do that. Copy `head_hash` from the output to storage the database operators cannot write to (monthly), and pass the newest copy as `--anchor`.

Retention dry run (deletes nothing):

    docker compose exec api python -m rebate.modules.audit.cli retention-report --tenant <tenant-uuid>
