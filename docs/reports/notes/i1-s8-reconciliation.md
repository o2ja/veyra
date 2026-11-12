# I1 — S8.3-S8.6 backend: remittance ingestion + reconciliation (branch `s8-reconciliation`)

Status: in progress. Sections "Built", "Tests", "Deviations" are filled in as the steps land.

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
