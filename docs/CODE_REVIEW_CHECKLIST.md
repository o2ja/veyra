# Code Review Checklist

> Run this at the end of every stage (and on any substantial change) before the completion report. Record results in the completion report: for each section say *pass*, *fail (fixed)* or *not applicable (why)*. A box is ticked only after you looked at the code or ran the check, not from memory.

## 1. Functionality
- [ ] Every requirement ID in scope (`PRODUCT_REQUIREMENTS.md`) is implemented and traceable to a test
- [ ] Behaviour matches `BUSINESS_LOGIC.md` / `API_SPECIFICATION.md` / `DATABASE_SCHEMA.md`; any difference is documented and flagged to the owner
- [ ] No mocked, stubbed, hard-coded or simulated production behaviour; no fake success paths (rules 4–5)
- [ ] No TODO/placeholder in critical business logic (rule 6)
- [ ] Failure paths implemented (invalid input, missing data, dependency down, retries, partial failure)
- [ ] Feature exercised manually end to end; what was done and observed is recorded (rule 15)

## 2. Code quality and architecture
- [ ] Module boundaries respected (`import-linter` green); no cross-module table access outside the owning module's service
- [ ] Layering: router → service → repository; no business logic in routers or React components
- [ ] `rebate_engine` and `rebate_matching` remain pure (no DB, network, clock, randomness)
- [ ] No new dependency or technology unless approved; licences checked (rule 2)
- [ ] No unnecessary abstraction or speculative generality (rule 13)
- [ ] Naming and structure follow `CLAUDE.md`; lint, format and type checks pass (`ruff`, `mypy`, ESLint, `tsc`)
- [ ] No dead code, commented-out blocks or leftover debug output

## 3. Security
- [ ] Every route declares a permission and enforces it on the server (rule 11)
- [ ] Tenant isolation: queries scoped, RLS applied to every new tenant table, composite FKs, isolation suite extended and green
- [ ] Cross-tenant and unauthorised access returns 404/403 as specified, with no data leakage in messages
- [ ] All input validated on the backend (rule 10): types, ranges, sizes, enums, file types
- [ ] No secrets, tokens, PII or financial data in logs, errors, URLs or frontend bundles (rule 12)
- [ ] Parameterised queries only; no string-built SQL; output escaped; exports protected from formula injection
- [ ] Sessions, CSRF and cookie flags unchanged or improved; rate limits on sensitive endpoints
- [ ] Audit events written (same transaction) for every state-changing business action
- [ ] AI: no LLM output feeds calculations or eligibility; prompts treat document text as untrusted

## 4. Database integrity
- [ ] Every schema change has a migration; upgrade from empty and from previous head works (rule 9)
- [ ] Constraints enforce invariants (NOT NULL, CHECK, unique, FK, exclusion); not only application code
- [ ] Money columns `NUMERIC` with the specified precision; no floats anywhere in the money path
- [ ] Indexes support the new queries; checked with `EXPLAIN` on realistic volume
- [ ] Immutability triggers in place for frozen or append-only data; app role cannot bypass them
- [ ] Transactions: boundaries correct, idempotent where required, locking and concurrency considered
- [ ] Downgrade/rollback consequences understood and documented where destructive

## 5. Financial correctness
- [ ] All amounts use `Decimal` with specified rounding; JSON as strings
- [ ] Every financial calculation has automated tests including edge cases (rule 8)
- [ ] Expected values come from independent sources (hand/reference script), not the code under test
- [ ] Results are deterministic and reproducible; trace explains each amount
- [ ] No financial figure produced by an LLM (rule 7)

## 6. Error handling and observability
- [ ] Errors use the standard envelope and stable codes; messages actionable and non-leaky
- [ ] Unexpected errors logged with request id and context (no sensitive data); jobs fail visibly and are retryable
- [ ] Idempotency keys / unique constraints protect retried operations
- [ ] User-facing errors in the UI are understandable; loading, empty and error states exist

## 7. Testing
- [ ] New behaviour has unit, integration and (for user flows) e2e tests as `TESTING_STRATEGY.md` requires
- [ ] Tests assert behaviour and would fail if the feature were broken (spot-check by temporarily breaking it)
- [ ] Tests run against real PostgreSQL; no mocking of the database or tenant isolation
- [ ] Synthetic-data scenarios and ground truth updated together with rule changes
- [ ] Full suite, isolation suite and CI gates are green; failures are not skipped or muted
- [ ] Coverage of changed code reviewed; gaps listed in the report

## 8. Performance
- [ ] No N+1 queries or unbounded result sets; keyset pagination used for large lists
- [ ] Large operations are chunked and run in jobs, with progress and resumability
- [ ] Measured against NFR targets where the stage touches them; numbers recorded

## 9. Frontend (when applicable)
- [ ] Generated API client used; no hand-typed duplicate types
- [ ] Money rendered from strings; no floating-point arithmetic on amounts
- [ ] Permission-aware UI (but the server remains the authority)
- [ ] Accessible: labels, focus order, keyboard use, contrast; axe check on touched pages
- [ ] Sensitive data not stored in `localStorage` or exposed in the bundle

## 10. Documentation
- [ ] Docs changed where implementation changed the design; ADR added for architecture-level changes (rule 14)
- [ ] `PROJECT_STATUS.md` and `IMPLEMENTATION_PLAN.md` checkboxes updated truthfully
- [ ] API reference/OpenAPI regenerated; `.env.example` and runbooks updated
- [ ] Doc/implementation conflicts are listed for the owner rather than silently resolved

## 11. Regression
- [ ] Earlier stages' tests still pass; migrations apply on top of the previous release
- [ ] Previously approved behaviour unchanged unless explicitly agreed
- [ ] Known issues recorded in `PROJECT_STATUS.md` with impact and plan
