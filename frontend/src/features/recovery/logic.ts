import type { components } from "../../api/schema";

export type CaseOut = components["schemas"]["CaseOut"];
export type SummaryOut = components["schemas"]["SummaryOut"];
export type SummaryRow = components["schemas"]["SummaryRow"];
export type EventOut = components["schemas"]["EventOut"];
export type AttachmentOut = components["schemas"]["AttachmentOut"];
export type NotificationOut = components["schemas"]["NotificationOut"];
export type MemberOut = components["schemas"]["MemberOut"];

export const CASE_STATUSES = [
  "open",
  "to_reclaim",
  "submitted",
  "recovered",
  "written_off",
  "closed_no_action",
] as const;
export type CaseStatus = (typeof CASE_STATUSES)[number];

export const CASE_STATUS_LABELS: Record<CaseStatus, string> = {
  open: "Open",
  to_reclaim: "To reclaim",
  submitted: "Submitted",
  recovered: "Recovered",
  written_off: "Written off",
  closed_no_action: "Closed, no action",
};

export const CASE_TYPES = [
  "short_paid",
  "rejected",
  "unpaid",
  "overpaid",
  "unmatched_payment",
  "source_changed",
] as const;
export type CaseType = (typeof CASE_TYPES)[number];

export const CASE_TYPE_LABELS: Record<CaseType, string> = {
  short_paid: "Short paid",
  rejected: "Rejected",
  unpaid: "Unpaid",
  overpaid: "Overpaid",
  unmatched_payment: "Unmatched payment",
  source_changed: "Source changed",
};

export const DEADLINE_BASIS_LABELS: Record<NonNullable<CaseOut["deadline_basis"]>, string> = {
  remittance_date: "Remittance date",
  submission_date: "Submission date",
};

/** Every transition target except `written_off`: a write-off has its own four-eyes screen. */
export const TRANSITION_STATUSES = CASE_STATUSES.filter((s) => s !== "written_off");

export const statusLabel = (status: string): string => CASE_STATUS_LABELS[status as CaseStatus] ?? status;

export const caseTypeLabel = (caseType: string): string => CASE_TYPE_LABELS[caseType as CaseType] ?? caseType;

export const reasonLabel = (code: string): string => code.replaceAll("_", " ");

/**
 * Display-only formatting. Amounts are server strings and are never recomputed;
 * dates are shown in US style (the only formatting the brief allows).
 */
export function usDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value.length === 10 ? `${value}T00:00:00Z` : value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: "UTC",
  }).format(date);
}

export function usDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  }).format(date);
}

/**
 * Server amount, lossless: the API sends 4 decimal places ("150.0000"); trailing zeros beyond 2 are dropped.
 * The UI never adds, subtracts or rounds it.
 */
export function amountText(value: string | null | undefined): string {
  if (value == null) return "—";
  const [whole = "", fraction = ""] = value.split(".");
  return `${whole}.${fraction.replace(/0+$/, "").padEnd(2, "0")}`;
}

export const ownerText = (
  ownerUserId: string | null | undefined,
  currentUserId: string | null | undefined,
): string => {
  if (!ownerUserId) return "Unassigned";
  if (currentUserId && ownerUserId === currentUserId) return "You";
  return ownerUserId;
};

export const attachmentFileId = (attachment: AttachmentOut): string => attachment.file_id;

/** Oldest-first or newest-first, the timeline is always shown newest first. */
export const newestFirst = (events: EventOut[]): EventOut[] =>
  [...events].sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0));

/** The notification links to the case only when the server says it is a variance case. */
export const notificationCaseLink = (notification: NotificationOut): string | null => {
  if (notification.entity_type !== "variance_case" || !notification.entity_id) return null;
  return `/recovery/${notification.entity_id}`;
};

/**
 * Forbidden control characters in free text (reasons, notes).
 * Matches backend `NO_CONTROL_TEXT = r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+$"`.
 */
// eslint-disable-next-line no-control-regex
export const CONTROL_CHAR_REGEX = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/;

/** Validates write-off proposal reason (1..1000 characters, no control characters, non-blank). */
export function isValidWriteOffReason(reason: string): boolean {
  const trimmed = reason.trim();
  if (trimmed.length < 1 || trimmed.length > 1000) return false;
  return !CONTROL_CHAR_REGEX.test(reason);
}

/**
 * Formats the read-only proposal label for write-off approval:
 * "Reason proposed by <name/email if available> on <date>".
 */
export function writeOffProposalLabel(
  proposer: { full_name?: string | null; email?: string | null } | string | null | undefined,
  proposedAt: string | null | undefined,
): string {
  const who =
    typeof proposer === "object" && proposer !== null
      ? proposer.full_name?.trim() || proposer.email?.trim() || null
      : typeof proposer === "string" && proposer.trim() !== ""
        ? proposer.trim()
        : null;
  const dateStr = usDate(proposedAt);
  if (who) {
    return `Reason proposed by ${who} on ${dateStr}`;
  }
  return `Reason proposed on ${dateStr}`;
}

/** A batch id in an error detail, when the server supplies one (reclaim_included). */
export function batchIdFromDetails(details: { field: string; message: string }[]): string | null {
  for (const detail of details) {
    if (detail.field === "claim_batch_id" || detail.field === "batch_id") {
      const uuid = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i.exec(detail.message);
      if (uuid) return uuid[0];
    }
  }
  return null;
}
