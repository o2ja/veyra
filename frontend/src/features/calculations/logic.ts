import type { components } from "../../api/schema";
import { formatMoney, formatQty } from "../../lib/format";

export type AccrualList = components["schemas"]["AccrualList"];
export type AccrualOut = components["schemas"]["AccrualOut"];
export type AdjustmentOut = components["schemas"]["AdjustmentOut"];
export type DraftPreviewIn = components["schemas"]["DraftPreviewIn"];
export type DraftPreviewLine = components["schemas"]["DraftPreviewLine"];
export type DraftPreviewOut = components["schemas"]["DraftPreviewOut"];
export type ProposalsOut = components["schemas"]["ProposalsOut"];
export type ResultDetail = components["schemas"]["ResultDetail"];
export type ResultList = components["schemas"]["ResultList"];
export type ResultOut = components["schemas"]["ResultOut"];
export type RunAccepted = components["schemas"]["RunAccepted"];
export type RunIn = components["schemas"]["RunIn"];
export type RunList = components["schemas"]["RunList"];
export type RunOut = components["schemas"]["RunOut"];
export type TrackerOut = components["schemas"]["TrackerOut"];
export type TrackerRow = components["schemas"]["TrackerRow"];

export const RUN_STATUSES = ["queued", "running", "completed", "failed", "cancelled"] as const;

export const RUN_STATUS_LABELS: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const RESULT_STATUSES = ["eligible", "exception", "netted", "adjustment", "not_applicable"] as const;

export const RESULT_STATUS_LABELS: Record<string, string> = {
  eligible: "Eligible",
  exception: "Exception",
  netted: "Netted",
  adjustment: "Adjustment",
  not_applicable: "Not applicable",
};

/** Engine exception codes (BUSINESS_LOGIC.md A8) in plain English, with the next step to clear each one. */
export const EXCEPTION_INFO: Record<string, { label: string; action?: { text: string; to: string } }> = {
  unknown_product: { label: "Unknown product", action: { text: "Map it", to: "/master-data/unmapped" } },
  unknown_customer: { label: "Unknown customer", action: { text: "Map it", to: "/master-data/unmapped" } },
  no_agreement: { label: "No contract on file", action: { text: "Add contract", to: "/agreements" } },
  ineligible_customer: {
    label: "Customer not covered by the contract",
    action: { text: "Check contract", to: "/agreements" },
  },
  ineligible_product: {
    label: "Product not covered by the contract",
    action: { text: "Check contract", to: "/agreements" },
  },
  agreement_not_started: { label: "Sale is before the contract starts" },
  expired_agreement: { label: "Contract had expired", action: { text: "Add renewal", to: "/agreements" } },
  conflicting_agreements: {
    label: "More than one contract applies",
    action: { text: "Review contracts", to: "/agreements" },
  },
  missing_spa_reference: { label: "SPA number missing on the invoice" },
  spa_reference_mismatch: { label: "SPA number on the invoice does not match the contract" },
  missing_cost: { label: "Cost missing on the sales line", action: { text: "Re-import", to: "/imports" } },
  missing_replacement_cost: {
    label: "Replacement cost missing",
    action: { text: "Re-import", to: "/imports" },
  },
  missing_uom_conversion: {
    label: "Unit of measure can't be converted",
    action: { text: "Add conversion", to: "/master-data/uom" },
  },
  price_mismatch: { label: "Sell price differs from the contract price" },
  cap_exhausted: { label: "Contract quantity cap already used up" },
  no_claim_due: { label: "Nothing to claim on this line" },
  negative_claim_amount: { label: "Claim would be negative" },
  outside_claim_window: { label: "Too late to claim (window closed)" },
  return_original_missing: {
    label: "Return without its original sale",
    action: { text: "Find the sale", to: "/lines/sales" },
  },
  return_exceeds_original: { label: "Return is larger than the original sale" },
};

export const exceptionLabel = (code: string | null | undefined): string =>
  code ? (EXCEPTION_INFO[code]?.label ?? code.replaceAll("_", " ")) : "—";

/** "35 lines · 7 eligible · $8,472.50 · 26 exceptions" from a run's stored totals (claims/runs.py Totals). */
export function runSummary(totals: Record<string, unknown> | null | undefined): string {
  if (!totals || typeof totals.lines !== "number") return "No results yet";
  const byStatus = isPlainObject(totals.by_status) ? totals.by_status : {};
  const n = (k: string) => (typeof byStatus[k] === "number" ? byStatus[k] : 0);
  const plural = (count: number, word: string) =>
    `${count.toLocaleString("en-US")} ${word}${count === 1 ? "" : "s"}`;
  return [
    plural(totals.lines, "line"),
    `${n("eligible").toLocaleString("en-US")} eligible`,
    formatMoney(typeof totals.eligible_amount === "string" ? totals.eligible_amount : null),
    plural(n("exception"), "exception"),
  ].join(" · ");
}

/** One plain line above the lineage tree. Only restates the server's own figures; computes nothing. */
export function explainFormula(r: {
  status: string;
  claim_amount?: string | null;
  claim_unit_amount?: string | null;
  eligible_quantity?: string | null;
  claim_uom?: string | null;
  exception_code?: string | null;
}): string {
  if (r.status === "exception") return `Nothing claimed: ${exceptionLabel(r.exception_code).toLowerCase()}.`;
  if (!r.claim_amount) return "No claim amount for this line.";
  if (!r.eligible_quantity || !r.claim_unit_amount) return `Claim ${formatMoney(r.claim_amount)}.`;
  const uom = r.claim_uom ? ` ${r.claim_uom}` : "";
  return `Claim ${formatMoney(r.claim_amount)} = ${formatQty(r.eligible_quantity)}${uom} eligible × ${formatMoney(r.claim_unit_amount)} per unit, after the contract's rounding rule.`;
}

export function isPlainObject(v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

export function formatPrimitive(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v === "" ? "—" : v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

export function flattenKeyValues(obj: unknown, prefix = ""): { key: string; value: string }[] {
  if (obj === null || obj === undefined) return [];
  if (!isPlainObject(obj)) {
    return [{ key: prefix || "value", value: formatPrimitive(obj) }];
  }
  const out: { key: string; value: string }[] = [];
  for (const [k, v] of Object.entries(obj)) {
    const label = prefix ? `${prefix}.${k}` : k;
    if (isPlainObject(v)) {
      const nested = flattenKeyValues(v, label);
      if (nested.length === 0) out.push({ key: label, value: "{}" });
      else out.push(...nested);
    } else if (Array.isArray(v)) {
      out.push({ key: label, value: v.length === 0 ? "[]" : JSON.stringify(v) });
    } else {
      out.push({ key: label, value: formatPrimitive(v) });
    }
  }
  return out;
}

export interface TraceNode {
  id: string;
  key: string;
  value: string | null;
  children: TraceNode[];
}

export function buildTraceTree(value: unknown, prefix = "trace"): TraceNode[] {
  if (isPlainObject(value)) {
    return Object.entries(value).map(([k, v]) => {
      const id = `${prefix}.${k}`;
      if (isPlainObject(v) || Array.isArray(v)) {
        const children = buildTraceTree(v, id);
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
        const children = buildTraceTree(item, id);
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

export function collectExpandableIds(nodes: TraceNode[]): string[] {
  const ids: string[] = [];
  for (const n of nodes) {
    if (n.children.length > 0) {
      ids.push(n.id);
      ids.push(...collectExpandableIds(n.children));
    }
  }
  return ids;
}
