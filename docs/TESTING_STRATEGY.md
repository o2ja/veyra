# Testing Strategy

> Financial software needs far stronger testing than CRUD. **Rule: nothing is "done" until its tests exist, pass, and the behaviour was verified manually where specified.** Test data: `TEST_DATA_SPEC.md`. Calculation semantics: `BUSINESS_LOGIC.md`. Review gate: `CODE_REVIEW_CHECKLIST.md`.

## 1. Principles

1. **Test against a real PostgreSQL** (service container locally and in CI), never SQLite or mocks for anything that touches persistence, RLS, constraints, or triggers.
2. **Independent oracles.** Expected financial results come from hand calculation, the spec examples, or a separately written reference, never from the code under test.
3. **Deterministic tests.** Inject time (`core.clock`), seed randomness, no network. LLM calls are replaced by recorded fixtures in CI; live-model evaluation is a separate, explicit run.
4. **Test pyramid weighted to the core:** the engine and matching packages have the densest coverage.
5. **A bug found = a regression test added** (and a synthetic scenario if it is data-shaped).
6. **No fake passes:** tests must fail when the feature is broken (verify by temporarily breaking it when writing important tests). Skipped tests need a recorded reason and owner in `PROJECT_STATUS.md`.

Coverage gates: `rebate_engine` and `rebate_matching` ≥ 90% line and branch; backend overall ≥ 80%; every route has an authorization test; every migration is exercised.

## 2. Test layers

| Layer | Tools | Scope | Runs |
|---|---|---|---|
| Unit | pytest, hypothesis | Pure functions: money helpers, UoM, rounding, parsers/normalisers, validators, state machines, engine, matching | Every commit |
| Integration (service/DB) | pytest + Postgres | Services + repositories + transactions, constraints, triggers, RLS, job handlers, audit writes | Every commit |
| API | pytest + FastAPI `TestClient`/httpx | Auth, CSRF, permissions, validation, error envelope, pagination, idempotency, concurrency (`If-Match`, 409s) | Every commit |
| Database | pytest + Postgres | Migrations, exclusion constraint, immutability triggers, composite FKs, RLS, money types lint | Every commit |
| Financial/golden | pytest (`engine_golden/`) | `BUSINESS_LOGIC.md` §14 catalogue + `ground_truth/` | Every commit |
| Extraction (deterministic parts) | pytest | Evidence verifier, schema, validators, parsers, injection indicators; recorded model outputs | Every commit |
| Extraction (live model) | pytest `-m live_llm` | Golden documents vs ground truth with a real provider (needs approval OD-2; synthetic docs only until then) | On demand; before prompt/model/provider change |
| Frontend unit/component | Vitest + Testing Library | Components, forms, formatters (money strings never converted to float) | Every commit |
| End-to-end | Playwright | Key workflows in a browser against a seeded stack | CI on main/PR + nightly |
| Security | See §9 | | CI + before pilot |
| Isolation | pytest (`isolation/`) | Two-tenant suite | **Every commit** |
| Performance | pytest + locust/custom | NFR-04 targets | Before pilot and on schema changes |

## 3. Financial calculation testing

### 3.1 Golden tests
Implement every row of `BUSINESS_LOGIC.md` §14 as a test with the exact expected figures. They run the **pure engine** with in-memory inputs (fast) and again through the **service layer** on the DB (end-to-end from imported synthetic CSV to stored `calculation_results`) using `ground_truth/expected_calculation.json`. Assert: status, exception code, amount, eligible quantity, flags, and **trace content** (key steps and values present), not only totals.

### 3.2 Property-based tests (Hypothesis)
- **Order independence:** shuffling the order of imported files/rows never changes results for a given processing order key (results depend on the documented total order, not insertion order).
- **Idempotence:** same inputs → byte-identical results and checksums; a second accrual run yields delta 0.
- **Additivity:** batch total = Σ line amounts; accrual deltas sum to the final rounded cumulative.
- **Monotonic tiers:** with retroactive mode and non-decreasing rates, earned cumulative is non-decreasing in qualifying volume.
- **Rounding bounds:** |rounded − exact| ≤ 0.5 × 10^(−scale) per line; `per_document` allocation sums exactly to the rounded total.
- **Allocation invariants:** Σ confirmed allocations ≤ amount paid; no cross-manufacturer matches; re-running reconciliation preserves confirmed matches.
- **No-change recalculation:** recalculating with an identical rule set yields zero adjustments.
- **Money safety:** random decimal strings parse/serialise losslessly; floats are rejected by the money type.

### 3.3 Reference implementation
Retroactive tier and rounding logic are implemented **twice** (production engine; independent simple reference in tests) and compared on generated inputs.

### 3.4 Differential testing with customers' own workbooks
When a pilot customer provides their current spreadsheet logic, reproduce it as a test oracle on their anonymised data; every disagreement is investigated (either side may be wrong).

### 3.5 Verifying the documentation
`testdata/verify_business_logic_examples.py` must pass in CI so spec examples cannot drift from arithmetic.

## 4. Document extraction and AI output validation

- **Deterministic layer (always on):** evidence-quote verification (found in source text), value-from-quote parsing (percent, date, money), schema validation, validators, injection-indicator detection (white text, tiny fonts, instruction phrases), pass-agreement comparison — tested with crafted inputs and recorded model responses (including malformed JSON, hallucinated values, values without evidence, conflicting passes).
- **Golden documents:** each file in `agreements/` has a ground-truth JSON. Metrics: field precision/recall, evidence-valid rate, critical-field conflict rate, document fully-correct rate, **hallucination rate** (values not supported by evidence that survive to the draft; target 0).
- **Adversarial set:** injection PDF (hidden instruction must not change output; indicator flagged), missing-info PDF (nulls, never guesses), multipage table (all 48 rows), Excel with title rows, plus planned: scans, rotated pages, encrypted, oversized.
- **Invariant tests:** an extraction can only ever write `document_extractions`, `ai_invocations`, `agreement_drafts` (verified by running the gateway with the restricted DB role); a draft never affects `calculation_runs` until approved.
- **Failure injection:** provider timeout/429/5xx, invalid JSON, empty output → expected states (`failed`, `needs_manual_entry`) and UI messages; no orphaned half-drafts.
- **Quality bar:** agreed with the owner at S6 using the golden set; any prompt/model/provider change must not reduce it.

## 5. Database testing

- Migrations: upgrade from empty to head; from the previous release's schema plus data; drift check (models vs migrations).
- Constraints: exclusion constraint rejects overlapping active versions but allows adjacent ones and superseded overlaps; composite FKs reject cross-tenant references; CHECKs (rate range, sign rules); partial unique indexes (`claim_lines` active uniqueness, one live case per line/type).
- Triggers: immutability of `calculation_results`, `agreement_versions` terms, frozen `claim_lines`, `audit_log`; allowed transitions only.
- RLS: with the app role and wrong/missing `app.tenant_id`, queries return zero rows and writes fail; owner/superuser connection at startup is refused; pooled-connection test (pool size 1) proves no context bleed.
- Lint tests over the schema: no float types, every tenant table has RLS (enabled + forced) and `UNIQUE (tenant_id, id)`, every FK between tenant tables is composite.
- Concurrency: two simultaneous approvals, two simultaneous calculation runs on one scope, concurrent batch creation claiming the same line → exactly one succeeds, others get the documented 409.

## 6. API testing

For every route: unauthenticated → 401; each role → expected 200/403 (generated from the permission matrix in `SECURITY.md` §3); other tenant's id → 404; invalid body → 422 with field details; extra fields → 422; CSRF missing/invalid → 403; idempotency replay/conflict; pagination cursor stability; rate-limit behaviour; error envelope shape; money fields are strings; OpenAPI schema stays in sync with the frontend client (CI regenerates and diffs).

## 7. End-to-end tests (Playwright)

Seeded with synthetic data; workflows from `ARCHITECTURE.md` §11:
1. Register → verify → MFA setup → invite user → accept invite (W1–W2).
2. Import sales with saved mapping, error review, partial import choice, re-import no-op (W3–W4).
3. Create an agreement manually → submit → approve by a second user (W5, W7).
4. Upload an agreement PDF → AI draft (recorded model) → review with evidence → edit → approve (W5–W7).
5. Calculation run → missed-claims scan → explain-this-number drill-down (W8).
6. Build batch → approve → export → submit → verify frozen (W9).
7. Upload credit memo → reconcile → confirm suggestions → finalize → cases → work and write off with approval (W10–W13).
8. Dashboard totals equal underlying rows (W14).
Plus accessibility checks (axe) on core pages and a role-visibility test per role.

## 8. Multi-tenant isolation tests (`tests/isolation/`)

Automatic and exhaustive; runs on every build:
- Create Tenant A and B with data in every table type (fixtures generated from the schema).
- **Every route** (enumerated from the app): call as A's user with B's resource ids → 404; list endpoints never include B's rows; searches/filters/exports/audit queries do not leak.
- **Jobs:** enqueue a job for A with B's ids → fails; job without tenant → fails.
- **Files:** A cannot obtain a download URL for B's file; storage key prefix assertions.
- **DB level:** as `rebate_app` with `app.tenant_id = A`, `SELECT/UPDATE/DELETE/INSERT` against B's rows → 0 rows/permission error; inserting a row with a foreign tenant's parent id fails (composite FK).
- **Meta-test:** fails if a new table with `tenant_id` lacks RLS or the composite unique key, or if a new route is not covered by the isolation enumerator.

## 9. Security testing

| Area | Tests |
|---|---|
| AuthN | Lockout, rate limits, no enumeration (identical responses and timing within tolerance), session fixation, idle/absolute expiry, revocation on deactivation/password change, MFA bypass attempts, token single-use/expiry |
| AuthZ | Full role × route matrix; privilege escalation attempts (role change, mass assignment); four-eyes enforcement |
| CSRF / XSS | Missing/forged tokens; stored XSS payloads in names, notes, file names rendered safely; CSP present |
| Injection | SQL injection payloads on every parameter (parameterised queries); path traversal in file names; header injection |
| Uploads | EICAR, renamed executables, `.xlsm`, zip bomb, XML entity bomb, oversized, mismatched MIME, polyglot files, encrypted PDF |
| Exports | Formula-injection cells (`=cmd|…`, `+`, `-`, `@`) neutralised in CSV/XLSX |
| AI | Prompt-injection corpus; confirm no write path outside drafts |
| Secrets | `gitleaks` in CI; tests that logs/errors never contain secrets or tokens |
| Dependencies | `pip-audit`, `npm audit`, licence check (no AGPL/GPL surprises) |
| Static | `bandit`, `ruff` S-rules, `mypy`, ESLint security |
| Dynamic | OWASP ZAP baseline on staging in CI; external penetration test before significant customer data |
| Infra | TLS config check, header check, open-port check, backup encryption check |

## 10. Performance testing

Targets (NFR-04, to be confirmed with evidence): 1M-line import < 30 min; calculation of 1M lines < 15 min; list endpoints p95 < 500 ms on 5M-line tenant. Method: generate volume data (`TEST_DATA_SPEC.md` §4), run on a reference VPS size, record `EXPLAIN ANALYZE` of the main queries, memory use of workers, and lock contention during concurrent imports/runs. Findings recorded in `PROJECT_STATUS.md`; indexes/partitioning decided from measurements, not guesses.

## 11. Critical edge cases that cause financial loss (must have tests)

1. Retroactive tier crossed mid-period, then a return/credit drops volume under the threshold (claw-back).
2. Cliff/retroactive vs incremental mistaken for each other; tier boundary exactly at a threshold (inclusive lower bound).
3. Invoice date vs ship date straddling agreement start/end.
4. UoM conversion with rounding per line vs per document; missing or inverse conversions.
5. Return against an invoice already claimed (previous batch, or previous fiscal year) → adjustment, not silent change.
6. Overlapping/conflicting agreements; priority ties.
7. Duplicate invoice lines from overlapping exports; changed rows after claim.
8. Payment by deduction rather than credit memo; one payment covering several batches; payment net of adjustments.
9. Quantity cap exhausted mid-invoice; cap restored by return.
10. Time zone and date-boundary deadline errors (UTC vs business time zone near midnight; DST changes).
11. Locale-formatted numbers (`1.234,56`), Excel serial dates, leading-zero SKUs, scientific notation, trailing spaces, BOM/encoding.
12. Credit memo referencing invoice numbers formatted differently (`INV 10099`, `inv-10099`, leading zeros).
13. Amended agreement applied retroactively to already-submitted claims (recalculation proposal, no silent change).
14. Currency mismatch rows; negative or zero unit prices; deviated cost greater than reference cost.
15. Float contamination (any float on a money path) and `Decimal` context/rounding mode differences.
16. Claim submitted twice (double-click, retry): idempotency and unique constraints.
17. Concurrent reconciliation and manual match edits; confirmed matches overwritten by a re-run.
18. Partial payments where the sum of allocations exceeds the payment.

## 12. CI pipeline (summary)

`lint → type-check → unit → db/integration → api → isolation → golden → frontend unit → build images → e2e (PR labelled or main) → security scans`. Failing any gate blocks merge. Coverage and test counts are published as CI artefacts. Details in `DEPLOYMENT.md` §CI/CD.
