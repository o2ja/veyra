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

function isPlainObj(v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

export function parseExtractionResult(
  raw: Record<string, unknown> | null | undefined,
): ExtractionResult | null {
  if (!isPlainObj(raw)) return null;
  if (!Array.isArray(raw.fields)) return null;
  if (!Array.isArray(raw.blocking)) return null;
  if (!Array.isArray(raw.needs_acknowledgement)) return null;
  if (!Array.isArray(raw.indicators)) return null;
  if (!isPlainObj(raw.notes)) return null;
  if (!Array.isArray(raw.notes.a) || !Array.isArray(raw.notes.b)) return null;
  if (!isPlainObj(raw.unresolved)) return null;
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

// shortcut: UI-only field ids (nothing sensitive) in localStorage, so a reload keeps them; upgrade to a server column if they must follow the user across devices
// eslint-disable-next-line no-restricted-globals
const storage = (): Storage => localStorage;
const confirmedKey = (extractionId: string) => `veyra.review-confirmed.${extractionId}`;

/** Ids the user confirmed ("Looks right"); only valid while the draft is unchanged since (same updated_at). */
export function readConfirmed(extractionId: string, draftUpdatedAt: string): string[] {
  try {
    const raw = JSON.parse(storage().getItem(confirmedKey(extractionId)) ?? "null") as {
      at?: unknown;
      ids?: unknown;
    } | null;
    if (raw?.at !== draftUpdatedAt || !Array.isArray(raw.ids)) return [];
    return raw.ids.filter((i): i is string => typeof i === "string");
  } catch {
    return [];
  }
}

export function writeConfirmed(extractionId: string, draftUpdatedAt: string, ids: ReadonlySet<string>): void {
  try {
    storage().setItem(confirmedKey(extractionId), JSON.stringify({ at: draftUpdatedAt, ids: [...ids] }));
  } catch {
    // storage unavailable: confirmations just last for this page visit
  }
}
