"""The published reason-code enum equals the seeded list and BUSINESS_LOGIC.md's controlled list."""

import importlib.util
from pathlib import Path
from typing import get_args

from rebate.modules.masterdata.schemas import ReasonCodeName

ROOT = Path(__file__).resolve().parents[2]
# BUSINESS_LOGIC.md §10.3, copied by hand (the docs are not mounted in the test container).
DOC_LIST = (
    "rejected_agreement_expired rejected_customer_ineligible rejected_product_ineligible rejected_price_mismatch "
    "rejected_duplicate_claim rejected_outside_claim_window rejected_missing_documentation "
    "rejected_quantity_exceeds_authorization rejected_uom_mismatch rejected_other short_paid_unexplained "
    "unpaid_no_remittance overpaid unmatched_payment source_changed unknown"
).split()


def test_enum_matches_migration_seed_and_docs() -> None:
    spec = importlib.util.spec_from_file_location(
        "m", ROOT / "migrations/versions/20261111_0900_masterdata.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    enum = list(get_args(ReasonCodeName))
    assert enum == [c for c, _, _ in mod.REASON_CODES]
    assert enum == DOC_LIST
