import { expect, test } from "vitest";

import {
  canApproveBatch,
  formatDecimal,
  getFormatOptionsForSource,
  SOURCE_KIND_MAP,
  TEMPLATE_SOURCES,
  type BatchOut,
} from "./logic";

test("formatDecimal groups thousands without rounding", () => {
  expect(formatDecimal("1234567.8900")).toBe("1,234,567.8900");
  expect(formatDecimal("-42000.5")).toBe("-42,000.5");
  expect(formatDecimal("999")).toBe("999");
  expect(formatDecimal("0.0000")).toBe("0.0000");
  expect(formatDecimal(null)).toBe("—");
  expect(formatDecimal(undefined)).toBe("—");
  expect(formatDecimal("")).toBe("—");
});

test("TEMPLATE_SOURCES and SOURCE_KIND_MAP map field kinds accurately", () => {
  expect(TEMPLATE_SOURCES.length).toBeGreaterThan(15);
  expect(SOURCE_KIND_MAP["claim_reference"]).toBe("text");
  expect(SOURCE_KIND_MAP["line_number"]).toBe("int");
  expect(SOURCE_KIND_MAP["invoice_date"]).toBe("date");
  expect(SOURCE_KIND_MAP["claim_amount"]).toBe("decimal");
});

test("getFormatOptionsForSource returns correct format options per kind", () => {
  expect(getFormatOptionsForSource("invoice_date").map((f) => f.value)).toEqual(["iso", "us", "eu"]);
  expect(getFormatOptionsForSource("claim_amount").length).toBe(7);
  expect(getFormatOptionsForSource("claim_reference")).toEqual([]);
  expect(getFormatOptionsForSource(null)).toEqual([]);
});

test("canApproveBatch enforces four-eyes and permissions", () => {
  const batch: BatchOut = {
    id: "b1",
    batch_number: "CLM-2026-0001",
    manufacturer_id: "m1",
    status: "draft",
    line_count: 5,
    total_amount: "500.00",
    period_from: null,
    period_to: null,
    export_template_id: null,
    exported_file_id: null,
    external_reference: null,
    void_reason: null,
    created_by: "user-author",
    created_at: "2026-01-01T00:00:00Z",
    approved_by: null,
    approved_at: null,
    submitted_by: null,
    submitted_at: null,
    voided_at: null,
  };

  // Author cannot approve
  const authorCheck = canApproveBatch(batch, "user-author", true);
  expect(authorCheck.allowed).toBe(false);
  expect(authorCheck.reason).toContain("Four-eyes");

  // Non-author with permission can approve
  const approverCheck = canApproveBatch(batch, "user-approver", true);
  expect(approverCheck.allowed).toBe(true);

  // User without permission cannot approve
  const noPermCheck = canApproveBatch(batch, "user-approver", false);
  expect(noPermCheck.allowed).toBe(false);

  // Non-draft cannot approve
  const approvedBatch: BatchOut = { ...batch, status: "approved" };
  const notDraftCheck = canApproveBatch(approvedBatch, "user-approver", true);
  expect(notDraftCheck.allowed).toBe(false);
});
