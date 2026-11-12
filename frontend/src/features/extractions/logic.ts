import type { components } from "../../api/schema";

export type ExtractionDetail = components["schemas"]["ExtractionDetail"];
export type ExtractionEvidence = components["schemas"]["ExtractionEvidence"];
export type ExtractionFieldView = components["schemas"]["ExtractionFieldView"];
export type ExtractionIndicator = components["schemas"]["ExtractionIndicator"];
export type ExtractionList = components["schemas"]["ExtractionList"];
export type ExtractionOut = components["schemas"]["ExtractionOut"];
export type ExtractionPassView = components["schemas"]["ExtractionPassView"];
export type ExtractionResult = components["schemas"]["ExtractionResult"];
export type ExtractionStart = components["schemas"]["ExtractionStart"];
export type UsageDay = components["schemas"]["UsageDay"];
export type UsageOut = components["schemas"]["UsageOut"];
export type UsageToday = components["schemas"]["UsageToday"];

export const EXTRACTION_STATUS_LABELS: Record<string, string> = {
  queued: "Queued",
  processing: "Processing",
  completed: "Completed",
  failed: "Failed",
};

export function parseExtractionResult(
  raw: Record<string, unknown> | null | undefined,
): ExtractionResult | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  if (!Array.isArray(raw.fields)) return null;
  return raw as unknown as ExtractionResult;
}

export function groupAndSortFields(fields: ExtractionFieldView[]): {
  critical: ExtractionFieldView[];
  other: ExtractionFieldView[];
} {
  const critical: ExtractionFieldView[] = [];
  const other: ExtractionFieldView[] = [];
  for (const f of fields) {
    if (f.critical) critical.push(f);
    else other.push(f);
  }
  return { critical, other };
}

export function isFieldFlagged(field: ExtractionFieldView): boolean {
  if (field.reasons.length > 0) return true;
  if (field.pass_a && field.pass_b && field.pass_a.status !== field.pass_b.status) return true;
  return false;
}

export function confidenceText(confidence: string | null | undefined): string {
  if (!confidence) return "Confidence: —";
  return `Confidence: ${confidence}`;
}

export function formatEvidenceLocation(
  ev: { page?: number | null; sheet?: string | null; cell?: string | null } | null | undefined,
): string {
  if (!ev) return "";
  const parts: string[] = [];
  if (ev.page !== null && ev.page !== undefined) parts.push(`Page ${ev.page}`);
  if (ev.sheet) parts.push(`Sheet "${ev.sheet}"`);
  if (ev.cell) parts.push(`Cell ${ev.cell}`);
  return parts.join(" · ");
}

export function formatExtractionValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value === "" ? "—" : value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}
