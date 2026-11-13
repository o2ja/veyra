# Production plan

Decisions that shape the first production environment. Hosting of Postgres and the provider/region stay **PROPOSED** (OD-3/OD-4, ADR-023).

## Object storage (owner decision 2026-11-13, ADR-017)
- **Production:** managed S3-compatible object storage. First choice AWS S3. Cloudflare R2 is allowed only after the S2 storage tests (presigned URL expiry, bucket versioning, server-side encryption, quarantine → clean copy) pass against it.
- **Dev/CI and interim:** SeaweedFS (ADR-017). The production compose from PR #18 keeps it until the managed bucket exists.
- **Switch:** set `S3_ENDPOINT_URL`, `S3_REGION`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_PUBLIC_ENDPOINT_URL` (`DEPLOYMENT.md`). Credentials: Read/Write/List on that one bucket; versioning and default encryption provisioned out of band.
- **Before the first pilot:** run the storage test suite against the chosen bucket (risk R-1, `PROJECT_STATUS.md`); add `X-Content-Type-Options: nosniff` on downloads (K-10).
- **Not done tonight:** provisioning a bucket or account (spending/new account: owner).
