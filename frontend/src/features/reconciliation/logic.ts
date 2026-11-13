import type { components } from "../../api/schema";

export type AllocationOut = components["schemas"]["AllocationOut"];
export type FinaliseOut = components["schemas"]["FinaliseOut"];
export type FindingOut = components["schemas"]["FindingOut"];
export type LineCorrection = components["schemas"]["LineCorrection"];
export type ManualMatchIn = components["schemas"]["ManualMatchIn"];
export type MatchOut = components["schemas"]["MatchOut"];
export type ReasonCodeMappingIn = components["schemas"]["ReasonCodeMappingIn"];
export type ReasonCodeMappingList = components["schemas"]["ReasonCodeMappingList"];
export type ReasonCodeMappingOut = components["schemas"]["ReasonCodeMappingOut"];
export type ReasonCodeMappingsIn = components["schemas"]["ReasonCodeMappingsIn"];
export type ReconciliationLineOut = components["schemas"]["ReconciliationLineOut"];
export type ReconciliationOut = components["schemas"]["ReconciliationOut"];
export type RemittanceAcceptIn = components["schemas"]["RemittanceAcceptIn"];
export type RemittanceAccepted = components["schemas"]["RemittanceAccepted"];
export type RemittanceExtractionOut = components["schemas"]["RemittanceExtractionOut"];
export type RemittanceIn = components["schemas"]["RemittanceIn"];
export type RemittanceIssue = components["schemas"]["RemittanceIssue"];
export type RemittanceLineList = components["schemas"]["RemittanceLineList"];
export type RemittanceLineOut = components["schemas"]["RemittanceLineOut"];
export type RemittanceList = components["schemas"]["RemittanceList"];
export type RemittanceOut = components["schemas"]["RemittanceOut"];
export type StagedLineOut = components["schemas"]["StagedLineOut"];

export const REMITTANCE_STATUSES = ["uploaded", "pending_review", "accepted", "reconciled", "void"] as const;

export type RemittanceStatus = (typeof REMITTANCE_STATUSES)[number];

export const REMITTANCE_STATUS_LABELS: Record<string, string> = {
  uploaded: "Uploaded",
  pending_review: "Pending review",
  accepted: "Accepted",
  reconciled: "Reconciled",
  void: "Void",
};

export const REMITTANCE_CHANNELS = ["credit_memo", "check", "ach", "deduction"] as const;
export type RemittanceChannel = (typeof REMITTANCE_CHANNELS)[number];

export const REMITTANCE_CHANNEL_LABELS: Record<string, string> = {
  credit_memo: "Credit memo",
  check: "Check",
  ach: "ACH",
  deduction: "Deduction",
};

export const REMITTANCE_DOC_TYPES = ["credit_memo", "remittance_advice", "deduction_notice"] as const;
export type RemittanceDocType = (typeof REMITTANCE_DOC_TYPES)[number];

export const REMITTANCE_DOC_TYPE_LABELS: Record<string, string> = {
  credit_memo: "Credit memo",
  remittance_advice: "Remittance advice",
  deduction_notice: "Deduction notice",
};

export const RECONCILIATION_STATES = ["matched", "suggested", "unmatched"] as const;
export type ReconciliationState = (typeof RECONCILIATION_STATES)[number];

export const RECONCILIATION_STATE_LABELS: Record<string, string> = {
  matched: "Matched",
  suggested: "Suggested",
  unmatched: "Unmatched",
};

export const MATCH_METHODS = [
  "exact_reference",
  "invoice_line",
  "composite",
  "fuzzy",
  "subset_sum",
  "manual",
] as const;
export type MatchMethod = (typeof MATCH_METHODS)[number];

export const MATCH_METHOD_LABELS: Record<string, string> = {
  exact_reference: "T1 Exact reference",
  invoice_line: "T2 Invoice line",
  composite: "T3 Composite",
  fuzzy: "T4 Fuzzy",
  subset_sum: "T5 Subset sum",
  manual: "Manual",
};

export const REASON_CODES: ReasonCodeMappingIn["reason_code"][] = [
  "rejected_agreement_expired",
  "rejected_customer_ineligible",
  "rejected_product_ineligible",
  "rejected_price_mismatch",
  "rejected_duplicate_claim",
  "rejected_outside_claim_window",
  "rejected_missing_documentation",
  "rejected_quantity_exceeds_authorization",
  "rejected_uom_mismatch",
  "rejected_other",
  "short_paid_unexplained",
  "unpaid_no_remittance",
  "overpaid",
  "unmatched_payment",
  "source_changed",
  "unknown",
];

export const REASON_CODE_LABELS: Record<ReasonCodeMappingIn["reason_code"], string> = {
  rejected_agreement_expired: "Rejected: Agreement expired",
  rejected_customer_ineligible: "Rejected: Customer ineligible",
  rejected_product_ineligible: "Rejected: Product ineligible",
  rejected_price_mismatch: "Rejected: Price mismatch",
  rejected_duplicate_claim: "Rejected: Duplicate claim",
  rejected_outside_claim_window: "Rejected: Outside claim window",
  rejected_missing_documentation: "Rejected: Missing documentation",
  rejected_quantity_exceeds_authorization: "Rejected: Quantity exceeds authorization",
  rejected_uom_mismatch: "Rejected: UoM mismatch",
  rejected_other: "Rejected: Other",
  short_paid_unexplained: "Short-paid: Unexplained",
  unpaid_no_remittance: "Unpaid: No remittance",
  overpaid: "Overpaid",
  unmatched_payment: "Unmatched payment",
  source_changed: "Source changed",
  unknown: "Unknown",
};

export const CASE_TYPE_LABELS: Record<FindingOut["case_type"], string> = {
  rejected: "Rejected",
  short_paid: "Short-paid",
  overpaid: "Overpaid",
  unmatched_payment: "Unmatched payment",
  unpaid: "Unpaid",
};

/**
 * Format a server decimal string with thousands grouping only.
 * Preserves exact fractional digits; never parses to float or rounds.
 */
export function formatDecimal(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const str = String(value).trim();
  const match = /^(-?)(\d+)(\.\d+)?$/.exec(str);
  if (!match) return str;
  const [, sign, intPart, fracPart] = match;
  const grouped = (intPart ?? "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${sign ?? ""}${grouped}${fracPart ?? ""}`;
}

export interface EvidenceNode {
  id: string;
  key: string;
  value: string | null;
  children: EvidenceNode[];
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function formatPrimitive(value: unknown): string {
  if (value === null) return "null";
  if (value === undefined) return "undefined";
  if (typeof value === "string") return `"${value}"`;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return String(value);
}

/**
 * Recursively builds a generic tree of labelled nodes from any untyped object/array.
 */
export function buildEvidenceTree(value: unknown, prefix = "root"): EvidenceNode[] {
  if (isPlainObject(value)) {
    return Object.entries(value).map(([k, v]) => {
      const id = `${prefix}.${k}`;
      if (isPlainObject(v) || Array.isArray(v)) {
        const children = buildEvidenceTree(v, id);
        return {
          id,
          key: k,
          value: children.length === 0 ? (Array.isArray(v) ? "[]" : "{}") : null,
          children,
        };
      }
      return { id, key: k, value: formatPrimitive(v), children: [] };
    });
  }
  if (Array.isArray(value)) {
    return value.map((item, idx) => {
      const id = `${prefix}[${idx}]`;
      const k = `[${idx}]`;
      if (isPlainObject(item) || Array.isArray(item)) {
        const children = buildEvidenceTree(item, id);
        return {
          id,
          key: k,
          value: children.length === 0 ? (Array.isArray(item) ? "[]" : "{}") : null,
          children,
        };
      }
      return { id, key: k, value: formatPrimitive(item), children: [] };
    });
  }
  return [];
}

export function collectExpandableIds(nodes: EvidenceNode[]): string[] {
  const ids: string[] = [];
  for (const n of nodes) {
    if (n.children.length > 0) {
      ids.push(n.id);
      ids.push(...collectExpandableIds(n.children));
    }
  }
  return ids;
}
