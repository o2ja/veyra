import type { components } from "../../api/schema";

export type DashboardOut = components["schemas"]["DashboardOut"];
export type Figures = components["schemas"]["Figures"];
export type MonthFigures = components["schemas"]["MonthFigures"];
export type DrilldownOut = components["schemas"]["DrilldownOut"];
export type DrillRow = components["schemas"]["DrillRow"];
export type TraceOut = components["schemas"]["TraceOut"];
export type TraceNode = components["schemas"]["TraceNode"];
export type BatchOut = components["schemas"]["BatchOut"];

export const METRIC_KEYS = [
  "claimed",
  "collected",
  "short_paid",
  "recovered",
  "written_off",
  "at_risk",
  "expiring",
] as const;

export type MetricKey = (typeof METRIC_KEYS)[number] | "recovered_after_write_off";

export const METRIC_LABELS: Record<string, string> = {
  claimed: "Claimed",
  collected: "Collected",
  short_paid: "Short paid",
  recovered: "Recovered",
  written_off: "Written off",
  recovered_after_write_off: "Recovered after write-off",
  at_risk: "At risk",
  expiring: "Expiring",
};

export const METRIC_DESCRIPTIONS: Record<string, string> = {
  claimed: "Total claim batches including adjustments",
  collected: "Cash on matched remittance lines",
  short_paid: "Lines with partial payments received",
  recovered: "Recovered amounts through disputes",
  written_off: "Written-off uncollectible claims",
  recovered_after_write_off: "Cash recovered after previous write-off",
  at_risk: "Amounts at risk of deadline breach",
  expiring: "Claims approaching submission deadlines",
};

export const TRACE_KINDS = [
  "claim_line",
  "claim_adjustment",
  "remittance_line",
  "variance_case",
  "calculation_result",
] as const;

export type TraceKind = (typeof TRACE_KINDS)[number];

export function isTraceKind(v: string): v is TraceKind {
  return (TRACE_KINDS as readonly string[]).includes(v);
}

export interface KeyValueItem {
  key: string;
  value: string;
}

export function formatFields(fields: Record<string, string | null> | null | undefined): KeyValueItem[] {
  if (!fields) return [];
  return Object.entries(fields).map(([k, v]) => ({
    key: k.replace(/_/g, " "),
    value: v === null || v === undefined || v === "" ? "—" : String(v),
  }));
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed";

/** What a job poll should do next: keep polling, finish (offer the result), or stop with the error. */
export function jobPollDecision(status: string | null | undefined): "poll" | "succeeded" | "failed" {
  if (status === "succeeded") return "succeeded";
  if (status === "failed") return "failed";
  return "poll";
}
