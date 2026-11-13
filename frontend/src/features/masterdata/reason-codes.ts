import type { components } from "../../api/schema";

export type ReasonCode = components["schemas"]["ReasonCodeMappingOut"]["reason_code"];

/** The controlled list (BUSINESS_LOGIC.md §10.3). Typed against the generated enum: the compiler fails if the
 * backend list and this one drift in either direction. */
export const REASON_CODE_LABELS: Record<ReasonCode, string> = {
  rejected_agreement_expired: "Rejected: agreement expired",
  rejected_customer_ineligible: "Rejected: customer not eligible",
  rejected_product_ineligible: "Rejected: product not eligible",
  rejected_price_mismatch: "Rejected: price mismatch",
  rejected_duplicate_claim: "Rejected: duplicate claim",
  rejected_outside_claim_window: "Rejected: outside claim window",
  rejected_missing_documentation: "Rejected: missing documentation",
  rejected_quantity_exceeds_authorization: "Rejected: quantity exceeds authorization",
  rejected_uom_mismatch: "Rejected: unit of measure mismatch",
  rejected_other: "Rejected: other reason",
  short_paid_unexplained: "Short paid without explanation",
  unpaid_no_remittance: "Unpaid: no remittance received",
  overpaid: "Overpaid",
  unmatched_payment: "Payment without a matching claim",
  source_changed: "Source data changed after claiming",
  unknown: "Unknown or unmapped manufacturer reason",
};

export const REASON_CODES = Object.keys(REASON_CODE_LABELS) as ReasonCode[];
