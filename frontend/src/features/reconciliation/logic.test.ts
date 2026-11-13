import { describe, expect, it } from "vitest";

import {
  CASE_TYPE_LABELS,
  MATCH_METHOD_LABELS,
  REASON_CODES,
  RECONCILIATION_STATE_LABELS,
  REMITTANCE_CHANNEL_LABELS,
  REMITTANCE_DOC_TYPE_LABELS,
  REMITTANCE_STATUS_LABELS,
  buildEvidenceTree,
  collectExpandableIds,
  formatDecimal,
} from "./logic";

describe("reconciliation logic", () => {
  it("formatDecimal formats correctly without float rounding", () => {
    expect(formatDecimal("1234567.890123")).toBe("1,234,567.890123");
    expect(formatDecimal("-98765.43")).toBe("-98,765.43");
    expect(formatDecimal("0.00")).toBe("0.00");
    expect(formatDecimal(1500.5)).toBe("1,500.5");
    expect(formatDecimal(null)).toBe("—");
    expect(formatDecimal(undefined)).toBe("—");
    expect(formatDecimal("")).toBe("—");
  });

  it("constants and labels are mapped", () => {
    expect(REMITTANCE_STATUS_LABELS["pending_review"]).toBe("Pending review");
    expect(REMITTANCE_CHANNEL_LABELS["credit_memo"]).toBe("Credit memo");
    expect(REMITTANCE_DOC_TYPE_LABELS["remittance_advice"]).toBe("Remittance advice");
    expect(RECONCILIATION_STATE_LABELS["suggested"]).toBe("Suggested");
    expect(MATCH_METHOD_LABELS["exact_reference"]).toBe("T1 Exact reference");
    expect(MATCH_METHOD_LABELS["subset_sum"]).toBe("T5 Subset sum");
    expect(CASE_TYPE_LABELS["short_paid"]).toBe("Short-paid");
    expect(REASON_CODES).toContain("rejected_agreement_expired");
    expect(REASON_CODES).toContain("short_paid_unexplained");
  });

  it("buildEvidenceTree parses arbitrary nested objects", () => {
    const raw = {
      score: 0.95,
      tokens: ["CLM-001", "INV-100"],
      details: { matched_on: "claim_reference", confidence: "high" },
    };
    const tree = buildEvidenceTree(raw);
    expect(tree.length).toBe(3);
    const scoreNode = tree.find((n) => n.key === "score");
    expect(scoreNode?.value).toBe("0.95");
    const tokensNode = tree.find((n) => n.key === "tokens");
    expect(tokensNode?.children.length).toBe(2);
    const expandable = collectExpandableIds(tree);
    expect(expandable).toContain("root.tokens");
    expect(expandable).toContain("root.details");
  });
});

