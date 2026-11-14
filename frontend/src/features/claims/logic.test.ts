import { expect, test } from "vitest";

import {
  ADJUSTMENT_STATUS_LABELS,
  ADJUSTMENT_STATUSES,
  ADJUSTMENT_TYPE_LABELS,
  ADJUSTMENT_TYPES,
  canApproveBatch,
  formatDecimal,
  getFormatOptionsForSource,
  getJobRefetchInterval,
  isJobActive,
  isJobTerminal,
  JOB_STATUSES,
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
  expect(SOURCE_KIND_MAP["line_type"]).toBe("text");
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

test("ADJUSTMENT_TYPES and ADJUSTMENT_TYPE_LABELS provide plain english names", () => {
  expect(ADJUSTMENT_TYPES).toEqual([
    "return_reversal",
    "agreement_change",
    "manual_correction",
    "rebate_clawback",
    "recovery_reclaim",
  ]);
  expect(ADJUSTMENT_TYPE_LABELS["return_reversal"]).toBe("Return reversal");
  expect(ADJUSTMENT_TYPE_LABELS["agreement_change"]).toBe("Agreement change");
  expect(ADJUSTMENT_TYPE_LABELS["manual_correction"]).toBe("Manual correction");
  expect(ADJUSTMENT_TYPE_LABELS["rebate_clawback"]).toBe("Rebate clawback");
  expect(ADJUSTMENT_TYPE_LABELS["recovery_reclaim"]).toBe("Recovery reclaim");
});

test("ADJUSTMENT_STATUSES and ADJUSTMENT_STATUS_LABELS cover all queue statuses", () => {
  expect(ADJUSTMENT_STATUSES).toEqual(["proposed", "approved", "included", "rejected"]);
  expect(ADJUSTMENT_STATUS_LABELS["proposed"]).toBe("Proposed");
  expect(ADJUSTMENT_STATUS_LABELS["approved"]).toBe("Approved");
  expect(ADJUSTMENT_STATUS_LABELS["included"]).toBe("Included");
  expect(ADJUSTMENT_STATUS_LABELS["rejected"]).toBe("Rejected");
});

test("job polling logic continues for queued and running, and terminates for succeeded and failed", () => {
  expect(JOB_STATUSES).toEqual(["queued", "running", "succeeded", "failed"]);

  // Active status checks
  expect(isJobActive("queued")).toBe(true);
  expect(isJobActive("running")).toBe(true);
  expect(isJobActive("pending")).toBe(true);
  expect(isJobActive("succeeded")).toBe(false);
  expect(isJobActive("failed")).toBe(false);
  expect(isJobActive(null)).toBe(false);
  expect(isJobActive(undefined)).toBe(false);

  // Terminal status checks
  expect(isJobTerminal("succeeded")).toBe(true);
  expect(isJobTerminal("failed")).toBe(true);
  expect(isJobTerminal("queued")).toBe(false);
  expect(isJobTerminal("running")).toBe(false);
  expect(isJobTerminal("pending")).toBe(false);

  // Polling refetch interval: continues (returns ms) for active, terminates (returns false) for terminal
  expect(getJobRefetchInterval("queued")).toBe(2000);
  expect(getJobRefetchInterval("running")).toBe(2000);
  expect(getJobRefetchInterval("pending")).toBe(2000);
  expect(getJobRefetchInterval("queued", 1500)).toBe(1500);

  // Polling terminates (returns false) for both succeeded and failed
  expect(getJobRefetchInterval("succeeded")).toBe(false);
  expect(getJobRefetchInterval("failed")).toBe(false);
  expect(getJobRefetchInterval("succeeded", 1500)).toBe(false);
  expect(getJobRefetchInterval("failed", 1500)).toBe(false);

  // Simulated status sequences
  const successSequence = ["queued", "running", "succeeded"];
  const intervalsForSuccess = successSequence.map((st) => getJobRefetchInterval(st));
  expect(intervalsForSuccess).toEqual([2000, 2000, false]);

  const failureSequence = ["queued", "running", "failed"];
  const intervalsForFailure = failureSequence.map((st) => getJobRefetchInterval(st));
  expect(intervalsForFailure).toEqual([2000, 2000, false]);
});
