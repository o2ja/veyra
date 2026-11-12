import type { components } from "../../api/schema";

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

/**
 * Format a server decimal string with thousands grouping only.
 * Never parses to float or rounds; preserves exact fractional digits.
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
