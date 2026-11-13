# I1 — S8.3-S8.6 backend: remittance ingestion + reconciliation (branch `s8-reconciliation`)

Status: backend complete except the re-chain (orchestrator). Sections below are final as of this run.

## Interface for S9 (finalisation hook, written early for J1)

Module `rebate.modules.reconciliation` (public names exported from its `__init__`):

```python
@dataclass(frozen=True)
class Finding:                      # one §10.3 variance finding; amount is always positive
    case_type: str                  # rejected | short_paid | overpaid | unmatched_payment | unpaid
    amount: Decimal
    reason_code: str                # controlled list, §10.3 (e.g. rejected_price_mismatch, unmapped -> unknown)
    claim_line_id: UUID | None      # set for rejected / short_paid / overpaid / unpaid
    remittance_line_id: UUID | None # set for unmatched_payment and for findings on a remitted claim line
    claimed: Decimal | None         # net claimed (claim + included adjustments); None for unmatched_payment
    paid: Decimal                   # sum of confirmed allocations (the unmatched line's amount_paid for unmatched_payment)
    external_reason_code: str | None

@dataclass(frozen=True)
class FinalisedRemittance:
    remittance_id: UUID
    manufacturer_id: UUID
    document_number: str | None
    document_date: date | None
    payment_date: date | None
    channel: str
    findings: tuple[Finding, ...]

def on_finalise(hook: Callable[[Session, Who, FinalisedRemittance], None]) -> None: ...
```

`finalise(db, who, remittance_id)` (service, behind `POST /remittances/{id}/finalize`) runs inside one transaction:
refuses unless the document is `accepted` and no `suggested` match is left; classifies with `rebate_matching`
(`classify_claim_line`, `classify_unmatched_payment`) for every claim line touched by this remittance and every
overdue unpaid claim line of the manufacturer; sets the document `reconciled` (immutable thereafter); writes the audit
event `remittance.finalised`; then calls every registered hook with the same `Session`, so the S9 case rows are created
in the same transaction. A hook that raises rolls the whole finalisation back. The hook is the only coupling: S9 owns
`variance_cases`; the reconciliation module never writes to it.

Tables S9 may read (never write): `remittance_documents`, `remittance_lines`, `reconciliation_matches`
(confirmed rows: `claim_line_id` or `claim_adjustment_id`, `allocated_amount`). Migration head after S8.3:
`20261117_0910_recon_matches` (S9 migrations chain after it).

## Built (S8.3-S8.6, this run completes the tests and gates)
- Migrations (head `20261117_0910_recon_matches`, `20261117_0900_remittances` still chains after `20261116_0900_adjustments_wiring`; RE-CHAIN left to the orchestrator): remittance_documents, remittance_lines, remittance_extraction_staging, reconciliation_matches (RLS ENABLE+FORCE, immutability triggers).
- `rebate.modules.reconciliation`: ingest (CSV/XLSX through the mapping path, PDF via the AI gateway with recorded fixtures), matching run (calls `rebate_matching`), decisions, finalisation hook, router (13 routes, see API_SPECIFICATION.md 2.7).
- This run: PDF path fixed (the document_extractions request row is inserted with the column list of rebate_app's INSERT grant, as `extraction.service` does; the ORM insert hit "permission denied"); OpenAPI + `frontend/src/api` regenerated (additive only: +13 paths, +19 schemas, nothing removed or changed); schema names made unique (`Remittance*`; `AcceptIn`, `ExtractionOut`, `LineOut`, `LineList`, `Issue` collided with identity/extraction/other schemas and silently overwrote them in the OpenAPI components); API_SPECIFICATION.md 2.7 updated.
- import-linter: the module imported other modules' internals (masterdata.service, ingestion.{normalizers,readers,service,validators}, extraction.{gateway,models,prompts,service}, claims.{recon_reads,schemas}); 4 contracts were broken. Fixed by re-exporting the needed names from the owning modules' `__init__` (additive, files outside the ownership list: `masterdata`, `ingestion`, `extraction`, `claims` `__init__.py`). `extraction.write_result` is the public name of `service._write`, `PROMPT_RULES`/`serialise_document` of prompts.

## Tests (S8.7 backend)
- `tests/reconciliation/test_reconciliation_csv.py`: CM-7781 CSV vs `expected_reconciliation.json`; ambiguous two-lines-one-claim never auto-confirmed; stated total mismatch refuses acceptance; duplicate document number refused; same file twice refused; re-run keeps confirmed; decisions with If-Match; finalisation findings (short_paid 150.00, rejected 2080.00, unmatched_payment 75.00, unpaid 660.00); finalised is final.
- `test_reconciliation_pdf.py` (recorded fixture, no live LLM): AI disabled -> 409 `ai_not_enabled`; a model answer 2150.00 not in the text + sum 7645.00 vs 7545.00 flagged and acceptance refused; recorded extraction staged for review; accepted by a person -> same matches/findings as the CSV; duplicate refused.
- `test_reconciliation_other.py`: RA-5521 (XLSX; the "Your ref" column is no header synonym so a saved mapping template is used; the one cell `CL-2026-000101` is replaced by the claim's real reference because numbering is the system's): exact_reference confirmed 448.00, `inv-10099` unmatched, finalise gives one unmatched_payment 120.00. DN-310 (deduction): invoice_line confirmed 60.00 on the COPR claim, no findings.
- `tests/isolation/test_routes.py`: seeds a remittance and a match per tenant; r1 parametrised over all new routes (foreign id == missing id, 404) plus `test_s8_foreign_remittances_lines_and_matches_are_unreachable` (lists, filters, manual match with foreign ids, other tenant unchanged). Tables: already registered in isolation conftest/test_db_rls/schema-lint in S8.3.
- `test_reconciliation_performance.py`: 5,000-line remittance (CM-7781 lines + 4,994 unmatched 10.00 lines, hand total 57,485.00). Recorded: ingest 2.5 s, reconcile 1.0 s (local Docker, one run). Limitation: the claim side is only the 8 ground-truth claim lines, so this measures remittance size, not a large claim pool.

## Deviations / findings to flag
- RA-5521 ground truth names the claim `CL-2026-000101`; the system numbers claims itself, so the test substitutes the real reference in that cell (documented above). Not a money discrepancy.
- No financial discrepancy found between BUSINESS_LOGIC, ground truth and code in the items tested.
- BROKEN ON MERGED BASE, NOT MINE: `tests/integration/test_end_to_end.py` (E2, from main) imports `FixtureClaims` from `calc_support`, which s7-adjustments removed (the file in `origin/s7-adjustments` does not use it; `origin/main` still has both). Collection error blocks a plain full run and mypy reports one error. The orchestrator must reconcile E2 with S7 at merge. My full run ignores that single file.
- IMPLEMENTATION_PLAN S8 boxes I consider truthful once the full run below is green: S8.3 (migrations + isolation/schema-lint), S8.4 (ingestion incl. PDF recorded, arithmetic check, duplicates), S8.5 (run + finalisation hook), S8.6 (API + OpenAPI + client). Not ticked by me; S8 UI and the owner's S8 sign-off are open. The isolation negative control (`rebate_negctl`) was NOT run here (owner of the night: another agent / orchestrator at merge).

## Verification run (commands and counts)
Commands run in the one-off api container (`REBATE_TEST_DB=rebate_test_i1`, `-e REBATE_AI_WRITER_PASSWORD`):
- Full backend: `pytest -q --no-cov --ignore=tests/integration/test_end_to_end.py` -> 2004 passed, 4 deselected (13 min). The one ignored file is E2's, broken on this base by the S7 `FixtureClaims` removal (orchestrator: fixed on s7-adjustments).
- Isolation + boundary + schema lint: `pytest tests/unit/test_boundaries.py tests/isolation tests/db/test_schema_lint.py tests/db/test_migration_helpers.py` -> 394 passed (after adding the `reconciliation-internals` protected contract to `backend/importlinter.ini`, requested by the orchestrator).
- `ruff check .` and `ruff format --check .` clean (281 files); `mypy` clean except the same E2 `FixtureClaims` error; `lint-imports` 14 contracts kept, 0 broken.
- Not run: isolation negative control (`rebate_negctl`), bandit, pip-audit, frontend suite, gitleaks full history (pre-commit gitleaks on staged changes passed).
- Not tested manually against the dev stack beyond the API tests.
