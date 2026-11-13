import type { components } from "../../api/schema";

export type AccrualList = components["schemas"]["AccrualList"];
export type AccrualOut = components["schemas"]["AccrualOut"];
export type AdjustmentList = components["schemas"]["AdjustmentList"];
export type AdjustmentOut = components["schemas"]["AdjustmentOut"];
export type BatchExportIn = components["schemas"]["BatchExportIn"];
export type BatchFilter = components["schemas"]["BatchFilter"];
export type BatchIn = components["schemas"]["BatchIn"];
export type BatchList = components["schemas"]["BatchList"];
export type BatchOut = components["schemas"]["BatchOut"];
export type BatchSubmitIn = components["schemas"]["BatchSubmitIn"];
export type BatchVoidIn = components["schemas"]["BatchVoidIn"];
export type ColumnIn = components["schemas"]["ColumnIn"];
export type ExportAccepted = components["schemas"]["ExportAccepted"];
export type ExportTemplateBody = components["schemas"]["ExportTemplateBody"];
export type ExportTemplateCreate = components["schemas"]["ExportTemplateCreate"];
export type ExportTemplateList = components["schemas"]["ExportTemplateList"];
export type ExportTemplateOut = components["schemas"]["ExportTemplateOut"];
export type JobOut = components["schemas"]["JobOut"];
export type LineList = components["schemas"]["LineList"];
export type LineOut = components["schemas"]["LineOut"];
export type MissedList = components["schemas"]["MissedList"];
export type MissedOut = components["schemas"]["MissedOut"];
export type MissedSummary = components["schemas"]["MissedSummary"];
export type OptionsIn = components["schemas"]["OptionsIn"];

export const BATCH_STATUSES = ["draft", "approved", "submitted", "void"] as const;

export const BATCH_STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  approved: "Approved",
  submitted: "Submitted",
  void: "Void",
};

export const ADJUSTMENT_TYPES = [
  "return_reversal",
  "agreement_change",
  "manual_correction",
  "rebate_clawback",
  "recovery_reclaim",
] as const;

export type AdjustmentType = (typeof ADJUSTMENT_TYPES)[number];

export const ADJUSTMENT_TYPE_LABELS: Record<string, string> = {
  return_reversal: "Return reversal",
  agreement_change: "Agreement change",
  manual_correction: "Manual correction",
  rebate_clawback: "Rebate clawback",
  recovery_reclaim: "Recovery reclaim",
};

export const ADJUSTMENT_STATUSES = ["proposed", "approved", "included", "rejected"] as const;

export type AdjustmentStatus = (typeof ADJUSTMENT_STATUSES)[number];

export const ADJUSTMENT_STATUS_LABELS: Record<string, string> = {
  proposed: "Proposed",
  approved: "Approved",
  included: "Included",
  rejected: "Rejected",
};

/**
 * Format a server decimal string with thousands grouping only.
 * Preserves exact fractional digits; never parses to float or rounds.
 */
export function formatDecimal(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const trimmed = value.trim();
  const match = /^(-?)(\d+)(\.\d+)?$/.exec(trimmed);
  if (!match) return trimmed;
  const [, sign, intPart, fracPart] = match;
  const grouped = (intPart ?? "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${sign ?? ""}${grouped}${fracPart ?? ""}`;
}

export type SourceKind = "text" | "int" | "date" | "decimal";

export const TEMPLATE_SOURCES: { name: string; kind: SourceKind; label: string }[] = [
  // text
  { name: "line_type", kind: "text", label: "Line type" },
  { name: "claim_reference", kind: "text", label: "Claim reference" },
  { name: "batch_number", kind: "text", label: "Batch number" },
  { name: "manufacturer_code", kind: "text", label: "Manufacturer code" },
  { name: "manufacturer_name", kind: "text", label: "Manufacturer name" },
  { name: "agreement_number", kind: "text", label: "Agreement number" },
  { name: "document_number", kind: "text", label: "Document number" },
  { name: "sku", kind: "text", label: "SKU" },
  { name: "product_description", kind: "text", label: "Product description" },
  { name: "manufacturer_part_number", kind: "text", label: "Manufacturer part number" },
  { name: "customer_code", kind: "text", label: "Customer code" },
  { name: "customer_name", kind: "text", label: "Customer name" },
  { name: "spa_reference", kind: "text", label: "SPA reference" },
  { name: "project_reference", kind: "text", label: "Project reference" },
  { name: "claim_uom", kind: "text", label: "Claim UoM" },
  { name: "currency_code", kind: "text", label: "Currency code" },
  // int
  { name: "line_number", kind: "int", label: "Line number" },
  // date
  { name: "invoice_date", kind: "date", label: "Invoice date" },
  { name: "ship_date", kind: "date", label: "Ship date" },
  // decimal
  { name: "quantity", kind: "decimal", label: "Quantity" },
  { name: "unit_amount", kind: "decimal", label: "Unit amount" },
  { name: "claim_amount", kind: "decimal", label: "Claim amount" },
  { name: "unit_price", kind: "decimal", label: "Unit price" },
  { name: "extended_amount", kind: "decimal", label: "Extended amount" },
];

export const SOURCE_KIND_MAP: Record<string, SourceKind> = Object.fromEntries(
  TEMPLATE_SOURCES.map((s) => [s.name, s.kind]),
);

export const DATE_FORMAT_OPTIONS = [
  { value: "iso", label: "ISO (YYYY-MM-DD)" },
  { value: "us", label: "US (MM/DD/YYYY)" },
  { value: "eu", label: "EU (DD/MM/YYYY)" },
];

export const DECIMAL_FORMAT_OPTIONS = [
  { value: "decimal:0", label: "0 decimals (decimal:0)" },
  { value: "decimal:1", label: "1 decimal (decimal:1)" },
  { value: "decimal:2", label: "2 decimals (decimal:2)" },
  { value: "decimal:3", label: "3 decimals (decimal:3)" },
  { value: "decimal:4", label: "4 decimals (decimal:4)" },
  { value: "decimal:5", label: "5 decimals (decimal:5)" },
  { value: "decimal:6", label: "6 decimals (decimal:6)" },
];

export function getFormatOptionsForSource(
  sourceName: string | null | undefined,
): { value: string; label: string }[] {
  if (!sourceName) return [];
  const kind = SOURCE_KIND_MAP[sourceName];
  if (kind === "date") return DATE_FORMAT_OPTIONS;
  if (kind === "decimal") return DECIMAL_FORMAT_OPTIONS;
  return [];
}

export function defaultTemplateColumn(): ColumnIn {
  return {
    header: "",
    source: "claim_reference",
    constant: null,
    format: null,
  };
}

export function defaultTemplateOptions(): OptionsIn {
  return {
    include_header: true,
    delimiter: ",",
    sheet_name: "Claims",
  };
}

export function canApproveBatch(
  batch: BatchOut,
  currentUserId: string | undefined,
  hasPerm: boolean,
): {
  allowed: boolean;
  reason?: string;
} {
  if (batch.status !== "draft") return { allowed: false, reason: "Batch is not in draft status." };
  if (!hasPerm) return { allowed: false, reason: "Permission denied." };
  if (currentUserId && batch.created_by === currentUserId) {
    return {
      allowed: false,
      reason: "Four-eyes rule: you created this batch, so a second approver must approve it.",
    };
  }
  return { allowed: true };
}
