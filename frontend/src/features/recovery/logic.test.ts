import { describe, expect, it } from "vitest";

import {
  TRANSITION_STATUSES,
  batchIdFromDetails,
  disputeSentences,
  disputeSummary,
  isValidWriteOffReason,
  newestFirst,
  notificationCaseLink,
  ownerText,
  reasonLabel,
  statusLabel,
  writeOffProposalLabel,
  type EventOut,
  type NotificationOut,
} from "./logic";

const event = (id: string, createdAt: string): EventOut => ({
  id,
  actor_user_id: null,
  created_at: createdAt,
  event_type: "status_changed",
  from_status: "open",
  to_status: "to_reclaim",
  note: null,
  payload: null,
});

describe("recovery display helpers", () => {
  it("offers every transition target except written_off", () => {
    expect(TRANSITION_STATUSES).toEqual(["open", "to_reclaim", "submitted", "recovered", "closed_no_action"]);
  });

  it("renders server labels and reason codes as text", () => {
    expect(statusLabel("closed_no_action")).toBe("Closed, no action");
    expect(statusLabel("not_a_status")).toBe("not_a_status");
    expect(reasonLabel("price_difference")).toBe("price difference");
  });

  it("names the owner from the session id only", () => {
    expect(ownerText(null, "u1")).toBe("Unassigned");
    expect(ownerText("u1", "u1")).toBe("You");
    expect(ownerText("u2", "u1")).toBe("u2");
  });

  it("orders the timeline newest first", () => {
    const rows = newestFirst([event("a", "2026-11-01T10:00:00Z"), event("b", "2026-11-02T10:00:00Z")]);
    expect(rows.map((row) => row.id)).toEqual(["b", "a"]);
  });

  it("links only variance-case notifications", () => {
    const base: NotificationOut = {
      id: "n1",
      title: "Reminder",
      body: null,
      kind: "deadline",
      due_date: "2026-11-13",
      created_at: "2026-11-01T10:00:00Z",
      read_at: null,
      entity_type: "variance_case",
      entity_id: "c1",
    };
    expect(notificationCaseLink(base)).toBe("/recovery/c1");
    expect(notificationCaseLink({ ...base, entity_type: "claim_batch" })).toBeNull();
  });

  it("finds a batch id only when the server includes one", () => {
    const uuid = "3f0a6f4e-2b7b-4c02-9f5d-4f6a1b2c3d4e";
    expect(batchIdFromDetails([{ field: "claim_batch_id", message: `batch ${uuid}` }])).toBe(uuid);
    expect(batchIdFromDetails([{ field: "reason", message: "included" }])).toBeNull();
  });

  it("validates write-off reasons with 1000 max length and no control characters", () => {
    expect(isValidWriteOffReason("Valid reason for write-off")).toBe(true);
    expect(isValidWriteOffReason("Multiline reason\nLine 2\r\nLine 3\twith tab")).toBe(true);
    expect(isValidWriteOffReason("a".repeat(1000))).toBe(true);
    expect(isValidWriteOffReason("a".repeat(1001))).toBe(false);
    expect(isValidWriteOffReason("")).toBe(false);
    expect(isValidWriteOffReason("   \n\t  ")).toBe(false);
    expect(isValidWriteOffReason("Invalid\x00NUL character")).toBe(false);
    expect(isValidWriteOffReason("Invalid\x07BEL character")).toBe(false);
    expect(isValidWriteOffReason("Invalid\x1bESC character")).toBe(false);
    expect(isValidWriteOffReason("Invalid\x7fDEL character")).toBe(false);
  });

  it("formats the write-off proposal label with available proposer info and date", () => {
    expect(
      writeOffProposalLabel(
        { full_name: "Jane Approver", email: "jane@example.com" },
        "2026-11-14T07:30:00Z",
      ),
    ).toBe("Reason proposed by Jane Approver on Nov 14, 2026");

    expect(writeOffProposalLabel({ full_name: "", email: "jane@example.com" }, "2026-11-14T07:30:00Z")).toBe(
      "Reason proposed by jane@example.com on Nov 14, 2026",
    );

    expect(writeOffProposalLabel("user-uuid-1234", "2026-11-14T07:30:00Z")).toBe(
      "Reason proposed by user-uuid-1234 on Nov 14, 2026",
    );

    expect(writeOffProposalLabel(null, "2026-11-14T07:30:00Z")).toBe("Reason proposed on Nov 14, 2026");

    expect(writeOffProposalLabel({ full_name: "Jane Approver" }, null)).toBe(
      "Reason proposed by Jane Approver on —",
    );
  });
});

describe("disputeSentences", () => {
  it("turns the stored checks into plain English", () => {
    const out = disputeSentences({
      assessment: "supported",
      reason_code: "rejected_price_mismatch",
      checks: [
        {
          name: "unit_price_matches_sell_price",
          inputs: { unit_price: "12.5000", sell_price: "12.50", tolerance: "0.01" },
          passed: true,
        },
        { name: "customer_in_scope", inputs: { customer_in_scope: "" }, passed: null },
      ],
    });
    expect(out?.headline).toMatch(/^Worth disputing/);
    expect(out?.checks[0]?.text).toBe(
      "Invoiced unit price $12.50 matches the agreed sell price $12.50 (tolerance $0.01).",
    );
    expect(out?.checks[1]?.text).toBe("Whether the customer is covered is not on file.");
    expect(disputeSentences(null)).toBeNull();
  });
});

describe("disputeSummary", () => {
  it("fills the fixed template from server figures", () => {
    expect(
      disputeSummary({
        claim_reference: "CLM-0042",
        invoice: "INV-10060, line 1",
        claimed_amount: "2080.0000",
        paid_amount: "0.0000",
        variance_amount: "2080.0000",
        reason_code: "rejected_price_mismatch",
        external_reason_code: "E12",
      }),
    ).toBe(
      [
        "Dispute summary",
        "Claim reference: CLM-0042",
        "Invoice: INV-10060, line 1",
        "Claimed: $2,080.00",
        "Paid: $0.00",
        "Variance: $2,080.00",
        "Reason given: Rejected: price mismatch (your code E12)",
        "",
        "Please review and pay the variance, or tell us what is missing.",
      ].join("\n"),
    );
  });
});
