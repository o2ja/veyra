# Implementation Plan

> **How to use (for Claude Code):** work one stage at a time, in order. Before starting a stage, read the documents it lists, write the **kickoff report**, and wait for owner approval. Tick boxes (`- [x]`) as tasks are *actually completed and tested*. Never tick a box for work that is stubbed, mocked, or untested. After the stage: run `CODE_REVIEW_CHECKLIST.md`, update `PROJECT_STATUS.md` and any changed docs, write the **completion report**, and wait for approval. Do not build ahead.
> Roadmap rationale: `DEVELOPMENT_ROADMAP.md`. Module layout: `ARCHITECTURE.md` §2–§3.

Common items for **every** stage (copy into each stage's kickoff/completion):

- [ ] Kickoff report written and approved by owner
- [ ] Relevant docs read (listed per stage)
- [ ] Migrations written for every DB change; upgrade-from-empty passes
- [ ] Automated tests for all new behaviour; full suite green; isolation suite green
- [ ] Manual verification performed and recorded (what was clicked/run, what was observed)
- [ ] `CODE_REVIEW_CHECKLIST.md` completed
- [ ] Docs updated where implementation differs from design (and ADR added for architectural changes)
- [ ] `PROJECT_STATUS.md` updated; completion report written; owner approval obtained

---

## S0 Foundation and tooling

**Read:** `CLAUDE.md`, `TECH_STACK.md`, `ARCHITECTURE.md`, `DEPLOYMENT.md` §2.
**Depends on:** nothing.

- [x] S0.1 Repository skeleton
  - [x] `backend/` with `pyproject.toml` (uv), `src/rebate/{core,api,modules,workers}`, `src/rebate_engine`, `src/rebate_matching`, `tests/` layout
  - [x] `frontend/` Vite + React + TS strict, ESLint, Prettier, Vitest
  - [x] `infra/{docker,caddy,scripts,runbooks}`, `.github/workflows`, `.gitignore`, `.env.example`, `Makefile`, `.pre-commit-config.yaml`
- [x] S0.2 Core backend plumbing
  - [x] `core/config.py` (pydantic-settings, fail-fast), `core/logging.py` (structlog JSON, request id), `core/errors.py` (DomainError + envelope), `core/clock.py`, `core/ids.py` (UUIDv7), `core/money.py` (Decimal helpers; rejects floats)
  - [x] `core/db.py`: engine, request-scoped transaction, `SET LOCAL app.tenant_id` hook, startup self-check (not superuser/owner/BYPASSRLS)
  - [x] FastAPI app factory, `/health/live`, `/health/ready`, error handlers, request-id middleware
- [x] S0.3 Database baseline
  - [x] `infra/scripts/init-db.sh` (roles, database, extensions, revokes)
  - [x] Alembic env (owner role), baseline migration, helpers `_rls.py`, `_immutable.py`
  - [x] Schema lint tests (no float types; RLS + composite unique on tenant tables; composite FKs), runnable even with zero tenant tables
- [x] S0.4 Quality gates
  - [x] `ruff`, `mypy` (strict on engine/matching), `import-linter` contracts (module boundaries, engine/matching purity), `bandit`, `pip-audit`, `gitleaks`
  - [x] CI workflow running all gates against a Postgres service container
  - [x] OpenAPI → TypeScript client generation + CI diff check
- [x] S0.5 Docker/dev stack
  - [x] Compose dev stack (postgres, mailpit, api, frontend; clamav behind profile `s2`; storage and worker in S2)
  - [x] Dockerfiles (non-root, multi-stage), `make up/down/db-init/migrate/test/lint`
- [x] S0.6 Docs: `README.md` run instructions verified on a clean checkout

**Acceptance:** `make up && make db-init && make migrate && make test` green; CI green; deliberate import violation fails `import-linter`; empty SPA shell loads and calls `/api/v1/health/live` through the proxy.
**Tests:** health endpoint, config fail-fast, money helper rejects float, startup self-check refuses owner role, migration empty→head, schema-lint tests.
**Status note (2026-11-09):** ticked items are implemented and tested (local scratch PostgreSQL 17 + green CI on PostgreSQL 16.15). All S0 tasks are ticked; Docker checks verified locally on 2026-11-09 after the WSL2 reboot: `make up/db-init/migrate/test/test-isolation/lint/check-client/down` green, and the README flow green on a fresh `git clone` (two fixes found: `make up` now runs db-init first; `make env` uses `python` on Windows). The dev image target runs as root (bind-mounted source); the runtime image is non-root per DEPLOYMENT.md §2.5. Acceptance item "SPA shell calls `/api/v1/health/live` through the proxy": verified via :5173 with curl and in headless Chrome (rendered `API: live`). Money helpers live in `rebate_engine/money.py` with `core/money.py` as the boundary type (C-4). `core/db.py` has the engine, `transaction(engine, tenant_id)` (SET LOCAL semantics, no bleed on a pooled connection) and the startup self-check, all tested; the per-request dependency `api/deps.py::db_session` gives one transaction per request (tested, red-green checked). Worker and object storage are deferred to S2 (owner decision Q5(b); ADR-017).

**Expected files:** `backend/src/rebate/core/*.py`, `backend/migrations/versions/*_baseline.py`, `backend/migrations/_rls.py`, `infra/docker/compose.dev.yml`, `.github/workflows/ci.yml`, `importlinter.ini`.

---

## S1 Identity, tenancy, RBAC, audit

**Read:** `PRODUCT_REQUIREMENTS.md` §1.1–1.2, `SECURITY.md` §1–§3, §7, `DATABASE_SCHEMA.md` §2, §3.1–3.2, `API_SPECIFICATION.md` §2.1.
**Depends on:** S0.

- [x] S1.1 Schema + RLS
  - [x] Migrations: `users`, `sessions`, `user_tokens`, `mfa_recovery_codes`, `tenants`, `tenant_settings`, `memberships`, `invitations`, `audit_log`, `idempotency_keys`
  - [x] RLS policies via helper; `fn_user_memberships`; privilege grants for `rebate_app`; audit immutability triggers. Also `pending_registrations`, `rate_limit_counters`, `rebate_lookup` owner role (kickoff §7/§10)
- [x] S1.2 `identity` module
  - [x] Registration (generic responses), email verification, login with Argon2id, lockout, uniform timing
  - [x] TOTP enrolment/verify, recovery codes (hashed), MFA enforcement setting
  - [x] Sessions: opaque token hashed in DB, cookie flags, idle/absolute timeouts, rotation, revocation (logout, deactivate, password change)
  - [x] CSRF double-submit + Origin check; password reset; email-change protections
- [x] S1.3 `tenancy` module
  - [x] Workspace creation, settings (time zone, MFA required, four-eyes, AI opt-in), memberships, invitations (create/accept/revoke/expire), role changes (last-admin protection), tenant switching
  - [x] Policy layer: permission registry from `SECURITY.md` §3; decorator on every route; four-eyes helper
- [x] S1.4 `audit` module: writer (same transaction), search endpoint, event catalogue constants
- [x] S1.5 Rate limiting (login/register/reset; general), request/response security headers
- [x] S1.6 Email jobs (can use a temporary in-process sender until S2 provides the queue; **replace in S2**, record in status). Done as K-9; forgot-password work also runs in this temporary pool (timing fix)
- [x] S1.7 Frontend: register/login/verify/MFA setup/recovery codes/reset screens; workspace switcher; Users, Invitations, Settings pages; Audit log page; permission-aware nav; session-expiry handling
- [x] S1.8 Tests
  - [x] Auth flows, lockout, no enumeration, session expiry/revocation, CSRF
  - [x] Role × route matrix generated from the registry; every route declares a permission (meta-test)
  - [x] **Isolation suite v1** (routes, DB-level with app role, pool-size-1 context test, meta-test for RLS)
  - [x] Audit immutability and same-transaction writes
  - [x] Playwright W1–W2

**Acceptance:** two workspaces cannot see each other's anything (tests prove it); MFA works end to end; admin invites an analyst and a viewer; role limits enforced server-side; audit shows all actions.
**Expected files:** `modules/identity/*`, `modules/tenancy/*`, `modules/audit/*`, `api/deps.py`, `api/middleware.py` (not created: the small middleware stays in `api/app.py`; paths in `api/paths.py`), migrations, `tests/isolation/*`, frontend `features/auth`, `features/admin`.

---

## S2 Files, scanning, background jobs

**Read:** `PRODUCT_REQUIREMENTS.md` §1.3, `SECURITY.md` §4, `ARCHITECTURE.md` §6, §8, ADR-005.
**Depends on:** S1.

- [x] S2.1 Job system
  - [x] Spike: Procrastinate with sync handlers, transactional enqueue, retries, periodic tasks, tenant-context propagation; **record result in ADR-005** (adopt, or implement the documented fallback behind `core/jobs.py`)
  - [x] Worker entrypoint, job registry, retry/backoff/dead-letter policy, per-job tenant context, admin endpoints (`/jobs`)
  - [x] Move S1 email sending onto the queue
- [x] S2.2 `files` module
  - [x] S3 client wrapper in `files/storage.py` (SeaweedFS locally/CI, ADR-017; add the pinned image behind profile `s2`), quarantine → clean key promotion, presigned downloads (≤ 5 min, audited). Any boto3 checksum-header workaround lives only in this client config and is noted
  - [x] Streaming upload with size limits, SHA-256, extension + MIME + magic-byte agreement, allow-list
  - [x] ClamAV scan job; statuses; admin notification on infected
  - [x] Duplicate detection by `(tenant, kind, sha256)`
- [x] S2.3 Frontend: upload component (progress, scan status polling), file detail, Jobs admin page (failed jobs, retry)
- [x] S2.4 Tests against the **real** storage service: presigned URLs with short expiry (valid, then expired), bucket versioning, server-side encryption, quarantine → clean copy. A failing required feature is reported to the owner before any switch (ADR-017)
- [x] S2.5 Tests: EICAR, `.xlsm`, renamed exe, MIME mismatch, oversized, zip bomb, quarantine promotion, job idempotency/retry/dead-letter, worker without tenant context fails, cross-tenant file/job access (isolation suite extended)

**Acceptance:** upload ends `clean` or `infected`; no reader accepts non-clean files; failed jobs visible and retryable; isolation suite covers files and jobs.
**Expected files:** `core/jobs.py`, `workers/*`, `modules/files/*`, migration `*_files_jobs.py`.

---

**Status note (2026-11-14):** S3-S10 items are ticked on the evidence that the code and its tests (backend) or Playwright spec (UI, `frontend/e2e`) are merged on `origin/main`; evidence list in `PROJECT_STATUS.md` §2 and the 2026-11-14 docs commit. Not ticked: S6.8 (quality bar not agreed). Open owner decisions are in `PROJECT_STATUS.md` §5. Caveat: 1M-row baselines (S3.6, S5.7) ran on a file where no line is eligible (`reports/PERFORMANCE_BASELINES.md`).

## S3 Master data and import pipeline

**Read:** `PRODUCT_REQUIREMENTS.md` §1.4, `DATABASE_SCHEMA.md` §3.3–3.5, §5, `BUSINESS_LOGIC.md` §7 (sign rules), `TEST_DATA_SPEC.md`.
**Depends on:** S2.

- [x] S3.1 Master data
  - [x] Migrations + models + CRUD for manufacturers (+settings), customers (+aliases, parent), products (+aliases), UoM conversions, branches, reason codes seed, reason-code mappings
  - [x] Unmapped-entity queue (customers/products seen in imports without a master match) with resolve actions
  - [x] Frontend grids and forms
- [x] S3.2 Readers and normalisers (pure, heavily unit-tested)
  - [x] CSV reader (encoding/BOM/delimiter detection), XLSX reader (strings only, read-only, header-row detection, title-row skipping), no formulas/macros
  - [x] Normalisers: dates (formats incl. US), numbers (thousands/decimal separators, parentheses negatives), SKUs (preserve leading zeros/case rules), UoM codes, sign canonicalisation for credit notes
  - [x] Header signature hashing; mapping templates (save/reuse/version)
- [x] S3.3 Import pipeline
  - [x] `import_batches`, `import_staged_rows`, `import_row_errors`; job chain `scan → parse → (confirm) → load`
  - [x] Validation (required fields, ranges, referential checks, original-invoice link for credit notes, currency, Luhn/PAN guard)
  - [x] Loader: chunked, resumable, `ON CONFLICT` natural key + row hash → inserted/unchanged/changed; versioned rows; `source_changed` flagging for claimed lines (stub the claim check until S7 with a clearly documented hook; **do not fake**)
  - [x] Import types: sales, purchases, customers, products, uom
  - [x] `suggest-mapping` endpoint returns a clear "AI not enabled" until S6 (no fake output)
- [x] S3.4 Source tables + browse APIs (`sales-lines`, `purchase-lines`) with filters and keyset pagination
- [x] S3.5 Frontend: import wizard (mapping UI, preview, errors, partial-import choice), history, changed-rows report, line browser with lineage links to source file/row
- [x] S3.6 Tests: all `testdata/generated/sales`, `purchases`, `master` files vs `expected_import_results.json`; idempotent re-import; changed rows; order independence; crash/resume; locale formats; credit-note sign; performance baseline (1M rows generated with `--out`)

**Acceptance:** synthetic layout A/B files import with expected counts; re-import no-op; changed rows versioned and reported; every line traceable to file and row.
**Expected files:** `modules/masterdata/*`, `modules/ingestion/{readers,normalizers,validators,loader,service}.py`, migrations, `frontend/src/features/{masterdata,imports}`.

---

## S4 Agreements: shared draft pipeline

**Read:** `PRODUCT_REQUIREMENTS.md` §1.5, `BUSINESS_LOGIC.md` §3, §8–§9, `DATABASE_SCHEMA.md` §3.6, §7, ADR-009/010/011.
**Depends on:** S3.

- [x] S4.1 Schema: `agreements`, `agreement_versions` (generated `valid_range`, exclusion constraint, immutability trigger), `agreement_version_items/customers/tiers`, `agreement_drafts`, `agreement_draft_revisions`
- [x] S4.2 Payload schema (Pydantic, `payload_schema_version`) and `AgreementValidator` (all rules in `BUSINESS_LOGIC.md` §3.3; errors vs warnings; reference resolution report for unresolved customers/SKUs)
- [x] S4.3 Draft service: create (manual / spreadsheet_template), update with `If-Match`, revisions, validate, submit (acknowledgements), return, reject
- [x] S4.4 SPA item bulk import using the import pipeline (template + mapping)
- [x] S4.5 Approval service: permission + four-eyes, re-validation, content hash, version creation, **split-supersede** transaction, audit; `cancel`
- [x] S4.6 Expiry sweep job (statuses, notifications hook)
- [x] S4.7 Version history and field-level diff
- [x] S4.8 Frontend: agreements list/detail, draft editor (header, rules, customers, items grid virtualised, tiers), validation panel, submit/approve/reject/return, version history + diff, bulk items import
- [x] S4.9 Tests: every validator rule; exclusion constraint (overlap, adjacency); split-supersede (v1a/v2 correctness); immutability triggers; four-eyes; concurrency (double approve); all `manual_payloads/*.json` accepted, mutated invalid ones rejected with precise errors; Playwright W5/W7 (manual path)

**Acceptance:** the five SPA/rebate payloads from `testdata` can be created by hand and by template, approved, and activated; active versions cannot be altered even with direct SQL as the app role.
**Expected files:** `modules/agreements/{schemas,validator,service,repository,policies,router,jobs}.py`, migrations, frontend `features/agreements`.

---

## S5 Calculation engine and runs

**Read:** `BUSINESS_LOGIC.md` (entire), `DATABASE_SCHEMA.md` §3.7, `TESTING_STRATEGY.md` §3, `TEST_DATA_SPEC.md` §3.
**Depends on:** S4.

- [x] S5.1 `rebate_engine` package (pure; `mypy --strict`; no imports from `rebate`)
  - [x] `money.py` (context, rounding modes), `models.py` (frozen inputs/outputs), `rules.py` (rules parsing), `uom.py`
  - [x] `spa.py`: gates in the documented order, bases (price difference, percent of sell/cost, flat, guaranteed margin), cap handling, returns netting/adjustments, claim window, exception codes
  - [x] `rebate.py`: tier determination, retroactive/incremental, qualifying vs payout base, accrual deltas, claw-back
  - [x] `rounding.py`: per-line and per-document (largest remainder)
  - [x] `trace.py`: trace builder + canonical hashing; `ENGINE_VERSION` constant
  - [x] `adapters/` registry (empty; mechanism only)
- [x] S5.2 Golden and property tests for the engine (all of `BUSINESS_LOGIC.md` §14; reference implementation for tiers/rounding)
- [x] S5.3 `claims` run orchestration
  - [x] Loaders (lines, versions as-of, consumption, prior claims), per-tenant advisory lock, chunked persistence, run status machine, totals/checksums, partial-run invisibility
  - [x] SPA before rebate ordering; SPA-line identification (§5.4)
  - [x] Recalculation proposals vs frozen claims (needs `claim_lines` from S7: implement the comparison against an interface and test with fixtures; **complete the wiring in S7**)
- [x] S5.4 Schema: `calculation_runs`, `calculation_results`, `rebate_accrual_entries`, `claim_adjustments` (+ append-only triggers)
- [x] S5.5 APIs: runs, results (+filters), result with trace, accrual ledger, tracker data, draft `preview-calculation`
- [x] S5.6 Frontend: run creation/status, results grid with exception filters, trace viewer ("explain this number"), preview panel in agreement editor, accrual/tier views
- [x] S5.7 Tests: `expected_calculation.json` scenarios + variants end to end from imported CSV; volume expectations; idempotent reruns; no-float lint; performance baseline (1M lines)

**Acceptance:** all scenarios/variants in `ground_truth` pass; reruns byte-identical; any amount shows its trace.
**Expected files:** `src/rebate_engine/*`, `modules/claims/{runs,loaders,persistence}.py`, migrations, `tests/engine_golden/*`.

---

## S6 AI extraction

**Read:** `AI_PROCESSING.md` (entire), `SECURITY.md` §4–§5, `AI_PROCESSING.md` §9, ADR-011/012, `TEST_DATA_SPEC.md` §2.2.
**Depends on:** S2, S4 (S5 recommended).
**Owner decision needed:** OD-2 (provider/data terms) before any real document is processed.

- [x] S6.1 Text/table extraction (`pdfplumber`, OCR fallback with limits, caps, encrypted/corrupt handling, Excel serialisation with cell addresses)
- [x] S6.2 AI gateway: provider interface + first adapter, prompt templates with versions, structured output, two passes, repair attempt, retries/backoff, cost tracking and caps, per-tenant opt-in check, redacted logging
- [x] S6.3 Deterministic verification: evidence verifier, value parsers, schema validation, pass-agreement, derived confidence, injection indicators
- [x] S6.4 DB role `rebate_ai_writer` and privileges; migrations `document_extractions`, `ai_invocations`
- [x] S6.5 Job `extract_agreement` → `agreement_drafts(source=ai_extraction)`; failure states (`needs_manual_entry`)
- [x] S6.6 `suggest_mapping` for imports (headers + ≤ 5 sample rows)
- [x] S6.7 Frontend: document viewer + field panel, evidence highlight, conflict/unsupported UX, acknowledgements, AI usage view, AI opt-in setting
- [ ] S6.8 Evaluation harness (`-m live_llm`) + recorded-response fixtures for CI; baseline results recorded; quality bar agreed with owner (**open**: harness, fixtures and baseline are merged; the bar is not agreed)
- [x] S6.9 Tests: deterministic layer; golden docs vs `expected_extraction/*`; injection, missing-info, multipage, Excel; failure injection; gateway role cannot write other tables; AI-disabled workspace fully functional

**Acceptance:** golden-set metrics meet the agreed bar; hallucination rate 0 in surviving fields on the golden set; no AI output affects calculations before approval.

---

## S7 Claims: missed-claims scan, batches, export

**Read:** `PRODUCT_REQUIREMENTS.md` §1.6–1.7, `BUSINESS_LOGIC.md` §7, §9, §11, `DATABASE_SCHEMA.md` §3.8, §7.
**Depends on:** S5, S6.

- [x] S7.1 Migrations: `claim_batches`, `claim_lines`, `claim_export_templates`; freeze/transition triggers; partial unique indexes
- [x] S7.2 Missed-claims query + summary + CSV/XLSX report job
- [x] S7.3 Batch service: create from results/filter, claim reference generation, uniqueness conflict reporting, approve (four-eyes), export (job, template columns, formula-injection protection), submit (freeze, idempotent), void (release lines)
- [x] S7.4 Adjustments: return reversal, agreement change, rebate clawback; approve/reject; inclusion in batches; wire recalculation proposals to frozen claim lines (**completes S5.3 hook**); wire `source_changed` detection from S3
- [x] S7.5 Frontend: missed-claims view, batch builder/detail, export template editor, submit dialog, adjustments queue
- [x] S7.6 Tests: double-claim, concurrency, freeze invariants (direct SQL as app role fails), export format/escaping, adjustments per scenarios SC-08/09, GL-A1, R2 clawback, idempotent submit, totals tie-out; Playwright W8–W9

**Acceptance:** synthetic month → scan → batch → export → submit; submitted lines immutable at DB level; totals tie out.

---

## S8 Remittance and reconciliation

**Read:** `BUSINESS_LOGIC.md` §10, `AI_PROCESSING.md` §8, `DATABASE_SCHEMA.md` §3.9, `TEST_DATA_SPEC.md` §2.6.
**Depends on:** S7 (+ S6 for PDF).

- [x] S8.1 `rebate_matching` package (pure; strict typing): normalisation, tiers T1–T5, scoring/weights config, assignment (Hungarian), bounded subset-sum, evidence output
- [x] S8.2 Golden/property tests for matching (including allocation invariants and re-run preserving confirmed)
- [x] S8.3 Migrations: `remittance_documents`, `remittance_lines`, `remittance_extraction_staging`, `reconciliation_matches`; reason-code mapping usage
- [x] S8.4 Remittance ingestion: CSV/XLSX via mapping templates; PDF via extraction + arithmetic checks + human acceptance; duplicate detection
- [x] S8.5 Reconciliation orchestration job; variance classification; finalisation rules; channel handling (credit memo/check/ACH/deduction)
- [x] S8.6 APIs + frontend workbench: claimed vs paid, confirm/reject/manual match, finalise
- [x] S8.7 Tests: `expected_reconciliation.json` for CM-7781 (CSV and PDF), RA-5521, DN-310; ambiguous/tie cases; arithmetic mismatch; large remittance performance; Playwright W10–W11

**Acceptance:** CM-7781 produces exactly the expected matches, allocations, cases, reasons; only exact tiers auto-confirm.

---

## S9 Recovery queue, deadlines, notifications

**Read:** `PRODUCT_REQUIREMENTS.md` §1.9, `BUSINESS_LOGIC.md` §10.3–10.4, §12, `DATABASE_SCHEMA.md` §3.10.
**Depends on:** S8.

- [x] S9.1 Migrations: `variance_cases`, `variance_case_events`, `case_attachments`, `notifications`
- [x] S9.2 Case service: creation from finalisation, state machine, owner assignment, notes/attachments, reclaim (adjustment proposal), write-off with four-eyes, `unpaid` detection, dispute assessment (deterministic checks using frozen results/versions)
- [x] S9.3 Deadline computation (tenant time zone, calendar days) + `deadline_unknown` handling
- [x] S9.4 Jobs: `deadline_sweep`, notification creation (14/7/3 days, dedupe), daily email digest
- [x] S9.5 Frontend: queue (sorted by deadline), case detail/timeline, notification centre, write-off dialog
- [x] S9.6 Tests: D1/D2 golden; DST/midnight boundaries with injected clock; state machine; four-eyes; dedupe; digest; Playwright W12–W13

**Acceptance:** remittance finalisation → cases → work → recovered/written off, with notifications at the right dates.

---

## S10 Dashboard, tier tracker, reporting

**Read:** `PRODUCT_REQUIREMENTS.md` §1.10, `ARCHITECTURE.md` W14, `DATABASE_SCHEMA.md` §3.11.
**Depends on:** S9.

- [x] S10.1 Summary refresh job + `summary_claims_monthly`; "as of" timestamps
- [x] S10.2 Dashboard API with drill-down endpoint; tier tracker API; trace resolver (`/trace/{kind}/{id}`); audit-pack export
- [x] S10.3 Frontend: dashboard, drill-down, tracker, trace explorer, audit pack download
- [x] S10.4 Tests: dashboard totals = Σ underlying rows (property test on generated data); trace chain completeness from `claim_line`/`remittance_line`/`variance_case` back to source file row; role visibility; Playwright W14

**Acceptance:** every figure drills to rows whose sum equals it; CFO (`viewer`) can follow any number to its source.

---

## S11 Hardening and pilot readiness

**Read:** `SECURITY.md` §12–§13, `DEPLOYMENT.md`, `TESTING_STRATEGY.md` §9–§10.
**Depends on:** S10; decisions OD-2/OD-3/OD-4.

- [ ] S11.1 `audit_seal` job + verification tool; retention/deletion tooling (audited)
  - [x] `audit_seal` job, verify CLI, retention dry-run report (#21: `backend/src/rebate/modules/audit/{seal,jobs,cli}.py`, `backend/tests/db/test_audit_seal.py`)
  - [ ] Deletion path: not built, owner decision D-N4 (legal review)
- [ ] S11.2 Performance: generate 1M/5M-line datasets, record timings and `EXPLAIN ANALYZE`, add indexes/partitioning only where measured; document results
- [ ] S11.3 Security: ASVS L2 review pass, ZAP baseline, upload-abuse suite, header/TLS checks, dependency/licence audit; **schedule external pen test**
- [ ] S11.4 Accessibility pass (axe) on core flows; UX polish; empty/error states
- [ ] S11.5 Deployment: production and staging via CI/CD per `DEPLOYMENT.md`; OD-3/OD-4 implemented; Caddy/TLS; monitoring/alerts tested; log shipping; secrets procedure
- [ ] S11.6 Backups: WAL/PITR + dumps + object versioning; **restore drill executed and timed**; runbooks reviewed
  - [x] Dump backup/restore scripts and a timed local restore drill (#18, #20: `infra/scripts/ops/{backup,restore}.sh`, `docs/reports/RESTORE_DRILL_2026-11-13.md`)
  - [ ] WAL/PITR: not set up or drilled
- [x] S11.7 Documentation (#20: `docs/guides/{OPERATOR_GUIDE,USER_GUIDE,ONBOARDING_PLAYBOOK,SECURITY_OVERVIEW}.md`): operator guide, user guide, onboarding playbook and data-requirements checklist, security overview for customers
- [ ] S11.8 Pilot readiness review with owner (checklist in `DEPLOYMENT.md` §9)

**Acceptance:** release checklist complete; owner signs off pilot readiness.
