"""S7.4 owner decision 1 in the database: negative claims need ``allow_negative_claims``; included adjustments need a frozen line.

- Batch tie-out (deferred): a non-void batch with ``total_amount <= 0`` is refused unless the manufacturer's
  ``allow_negative_claims`` is true.
- Adjustment trigger: a negative ``return_reversal`` cannot move to ``approved`` or ``included`` while the setting is
  false; an adjustment of a claim line is included only while that line is frozen and not released.

The existing functions are patched in place (``pg_get_functiondef``); forward-only like the rest of the chain.

Revision ID: 20261117_0900_neg_claims_guard
Revises: 20261116_0900_adjustments_wiring
"""

from alembic import op

revision = "20261117_0900_neg_claims_guard"
down_revision = "20261116_0900_adjustments_wiring"
branch_labels = None
depends_on = None

_ALLOWED = (
    "COALESCE((SELECT allow_negative_claims FROM manufacturer_settings "
    "WHERE tenant_id = {r}.tenant_id AND manufacturer_id = {r}.manufacturer_id), false)"
)

_BATCH = """
            IF b.status <> 'void' AND b.total_amount <= 0 AND NOT @ALLOWED@ THEN
                RAISE EXCEPTION 'claim_batches: a batch that claims nothing needs a manufacturer that allows negative claims'
                    USING ERRCODE = 'check_violation';
            END IF;
            SELECT count(*), COALESCE(sum(claim_amount), 0),
""".replace("@ALLOWED@", _ALLOWED.format(r="b"))

_ADJ = """
            IF NEW.status <> OLD.status AND NEW.status IN ('approved', 'included') AND OLD.status <> 'included'
               AND NEW.adjustment_type = 'return_reversal' AND NEW.amount < 0 AND NOT @ALLOWED@ THEN
                RAISE EXCEPTION 'claim_adjustments: the manufacturer does not accept negative claims'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.status = 'included' AND OLD.status <> 'included' AND NEW.claim_line_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM claim_lines WHERE tenant_id = NEW.tenant_id AND id = NEW.claim_line_id
                   AND frozen_at IS NOT NULL AND released_at IS NULL) THEN
                RAISE EXCEPTION 'claim_adjustments: the corrected claim line must be frozen and not released'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.status = 'included' THEN
""".replace("@ALLOWED@", _ALLOWED.format(r="NEW"))  # nosec B608 (module constants and fixed column names; every value is a bound parameter)


def _patch(fn: str, old: str, new: str) -> None:
    src = op.get_bind().exec_driver_sql(f"SELECT pg_get_functiondef('{fn}()'::regprocedure)").scalar_one()
    if src.count(old) != 1:
        raise RuntimeError(f"{fn}: expected exactly one match to patch")
    op.execute(src.replace(old, new))


def upgrade() -> None:
    anchor = "\n            SELECT count(*), COALESCE(sum(claim_amount), 0),\n"
    _patch("fn_claim_batch_ties_out", anchor, _BATCH)
    _patch("fn_claim_adjustment_update", "\n            IF NEW.status = 'included' THEN\n", _ADJ)
