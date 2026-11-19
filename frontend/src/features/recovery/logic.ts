import type { components } from "../../api/schema";
import { formatDate, formatMoney, formatQty } from "../../lib/format";

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

interface DisputeCheck {
  name: string;
  inputs: Record<string, string>;
  passed: boolean | null;
}

const ASSESSMENT_TEXT: Record<string, string> = {
  supported: "Worth disputing: the frozen claim data contradicts the manufacturer's reason.",
  weak: "Weak dispute: the frozen claim data agrees with the manufacturer's reason.",
  unknown: "Not enough data on file to judge the manufacturer's reason.",
};

function checkSentence({ name, inputs: i, passed }: DisputeCheck): string {
  const yes = passed === true;
  switch (name) {
    case "line_date_in_valid_range":
      return passed === null
        ? "The invoice date or the agreement period is not on file."
        : `Invoice date ${formatDate(i.line_date)} is ${yes ? "inside" : "outside"} the agreement period (${formatDate(i.valid_from)} – ${formatDate(i.valid_to)}).`;
    case "customer_in_scope":
      return passed === null
        ? "Whether the customer is covered is not on file."
        : `The customer is ${yes ? "" : "not "}covered by the agreement.`;
    case "unit_price_matches_sell_price":
      return passed === null
        ? "The invoiced price or the agreed sell price is not on file."
        : `Invoiced unit price ${formatMoney(i.unit_price)} ${yes ? "matches" : "does not match"} the agreed sell price ${formatMoney(i.sell_price)} (tolerance ${formatMoney(i.tolerance)}).`;
    case "consumption_within_max_quantity":
      return passed === null
        ? "The authorized maximum quantity is not on file."
        : `Quantity claimed to date (${formatQty(i.consumption)}) is ${yes ? "within" : "over"} the authorized maximum of ${formatQty(i.max_quantity)}.`;
    case "no_other_active_claim_or_payment":
      return passed === null
        ? "Whether this sale was claimed or paid elsewhere is not on file."
        : yes
          ? "No other claim or payment exists for this sale."
          : `${i.other_active_claims} other claim(s) or payment(s) exist for this sale.`;
    case "submitted_within_claim_window":
      return passed === null
        ? "The submission date or the claim window is not on file."
        : `The claim was submitted on ${formatDate(i.submitted_on)}, ${yes ? "before" : "after"} the claim window closed (${formatDate(i.claim_window_end)}).`;
    default:
      return `${name.replaceAll("_", " ")}: ${passed === null ? "unknown" : yes ? "passed" : "failed"}.`;
  }
}

/** Plain-English reading of the stored dispute assessment (recovery/assessment.py). */
export function disputeSentences(
  doc: Record<string, unknown> | null | undefined,
): { headline: string; checks: { text: string; passed: boolean | null }[] } | null {
  if (!doc) return null;
  const checks = Array.isArray(doc.checks) ? (doc.checks as DisputeCheck[]) : [];
  return {
    headline: ASSESSMENT_TEXT[String(doc.assessment)] ?? String(doc.assessment),
    checks: checks.map((c) => ({ text: checkSentence(c), passed: c.passed })),
  };
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
  const dateStr = formatDate(proposedAt);
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
