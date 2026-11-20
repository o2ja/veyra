import { expect, test } from "vitest";

import type { Validation } from "../agreements/logic";
import type { ExtractionFieldView, ExtractionResult } from "./logic";
import {
  buildReview,
  countsText,
  displayValue,
  fractionToPercent,
  getAt,
  isPending,
  matchSentence,
  parseEdit,
  pendingCount,
  percentToFraction,
  previewSentence,
  readLabel,
  setAt,
  type DraftPreview,
} from "./review";

const field = (
  key: string,
  path: string | null,
  over: Partial<ExtractionFieldView> = {},
): ExtractionFieldView => ({
  key,
  payload_path: path,
  critical: true,
  value: null,
  confidence: "high",
  reasons: [],
  evidence: { page: 1, quote: "…a quote…" } as ExtractionFieldView["evidence"],
  pass_a: null,
  pass_b: null,
  ...over,
});

const issue = (field: string, code: string, message: string, id?: string) => ({ field, code, message, id });

const need = (r: ReturnType<typeof buildReview>, id: string) => {
  const found = r.needs.find((i) => i.id === id);
  if (!found) throw new Error("no needs item " + id);
  return found;
};

test("percent and fraction conversion is exact decimal-string arithmetic", () => {
  expect(fractionToPercent("0.05")).toBe("5%");
  expect(fractionToPercent("0.025")).toBe("2.5%");
  expect(fractionToPercent("1")).toBe("100%");
  expect(percentToFraction("5")).toBe("0.05");
  expect(percentToFraction("2.5%")).toBe("0.025");
  expect(percentToFraction("0.5")).toBe("0.005");
  expect(percentToFraction("100")).toBe("1");
  expect(percentToFraction("10")).toBe("0.1");
  expect(percentToFraction("1000")).toBe("10");
  expect(percentToFraction("abc")).toBeNull();
});

test("getAt and setAt work on draft paths and never mutate the original", () => {
  const draft = { items: [{ sku: "A" }, { sku: "B" }], rules: {} };
  expect(getAt(draft, "items[1].sku")).toBe("B");
  const next = setAt(draft, "rules.calculation.basis", "price_difference");
  expect(next.rules).toEqual({ calculation: { basis: "price_difference" } });
  expect(draft.rules).toEqual({});
  const row = setAt(draft, "items[0].rate", "0.05") as typeof draft;
  expect(row.items).toEqual([{ sku: "A", rate: "0.05" }, { sku: "B" }]);
  expect(draft.items[0]).toEqual({ sku: "A" });
});

test("what the user types becomes the stored value", () => {
  expect(parseEdit("items[2].rate", "5%")).toBe("0.05");
  expect(parseEdit("items[2].rate", "x")).toBeUndefined();
  expect(parseEdit("rules.rounding.scale", "2")).toBe(2);
  expect(parseEdit("rules.spa_reference.required", "false")).toBe(false);
  expect(parseEdit("rules.date_basis", "")).toBeUndefined(); // a choice cannot be left empty
  expect(parseEdit("rules.claim_uom", "")).toBeNull(); // a claim unit can
});

test("values show the way people read them", () => {
  expect(displayValue("rules.date_basis", "ship_date")).toBe("Ship date");
  expect(displayValue("header.valid_from", "2026-01-01")).toBe("Jan 1, 2026");
  expect(displayValue("items[0].deviated_cost", "3412.5")).toBe("$3,412.50");
  expect(displayValue("items[0].rate", "0.05")).toBe("5%");
  expect(displayValue("rules.date_basis", undefined)).toBe("—");
});

const result = {
  fields: [
    field("header.date_basis", "rules.date_basis", {
      confidence: "low",
      reasons: ["not_stated"],
      evidence: null,
    }),
    field("header.valid_to", "header.valid_to", { value: "2026-12-31" }),
    field("rules.rounding", "rules.rounding", { value: { mode: "half_up" } }),
    field("items[A].rate", "items[0].rate", { value: "0.05", confidence: "medium" }),
    field("exclusions_text[]", null, { confidence: "low", reasons: ["passes_disagree"], evidence: null }),
  ],
} as unknown as ExtractionResult;

const draft = {
  header: { valid_to: "2026-12-31" },
  rules: { rounding: { mode: "half_up" } },
  items: [
    { sku: "A", rate: "0.05" },
    { sku: "Z", rate: "0.1" },
  ],
  customers: [],
};

const validation = {
  valid: false,
  errors: [
    issue("payload.rules.date_basis", "required", "Field required"),
    issue("payload.items[1].sku", "unresolved", "Unknown SKU Z."),
    issue("payload.header.valid_from", "version_overlap", "Overlaps an agreement already in force."),
  ],
  warnings: [issue("payload.items[0].rate", "rate_high", "That rate is unusually high.", "w1")],
} as unknown as Validation;

test("the review separates what was found from what needs a decision, in plain English", () => {
  const r = buildReview(result, draft, validation);

  expect(r.found.map((i) => i.id)).toEqual(["header.valid_to", "rules.rounding.mode"]);
  expect(r.needs.map((i) => i.id)).toEqual([
    "rules.date_basis",
    "rules.rounding.scale",
    "rules.rounding.timing",
    "items[0].rate",
    "field:exclusions_text[]",
    "items[1].sku",
    "header.valid_from",
  ]);

  const byId = Object.fromEntries(r.needs.map((i) => [i.id, i]));
  expect(byId["rules.date_basis"]).toMatchObject({
    label: "Which date decides eligibility",
    why: "The document doesn't state this.",
    blocking: true,
    display: "—",
  });
  expect(byId["rules.rounding.scale"]).toMatchObject({ label: "Decimal places", blocking: true });
  expect(byId["items[0].rate"]).toMatchObject({
    label: "Item 1 (A) · Rate",
    why: "That rate is unusually high.",
    blocking: false,
    warningIds: ["w1"],
    display: "5%",
  });
  expect(byId["items[1].sku"]).toMatchObject({
    label: "Item 2 (Z) · Part number",
    why: "Unknown SKU Z.",
    blocking: true,
  });
  expect(byId["field:exclusions_text[]"]).toMatchObject({
    path: null,
    blocking: false,
    why: "The two AI readings didn't match.",
  });
  expect(r.conflicts).toBe(1);
  expect(r.skus).toEqual({ matched: 1, total: 2 });
  expect(matchSentence(r)).toBe("Matched to your data: 1/2 SKUs");
});

test("a confirmed item stops waiting, but never while it is still invalid or empty", () => {
  const r = buildReview(result, draft, validation);
  const rate = need(r, "items[0].rate");
  const basis = need(r, "rules.date_basis");
  expect(isPending(rate, new Set())).toBe(true);
  expect(isPending(rate, new Set(["items[0].rate"]))).toBe(false);
  expect(isPending(basis, new Set(["rules.date_basis"]))).toBe(true);
  expect(pendingCount(r, new Set(["items[0].rate", "field:exclusions_text[]"]))).toBe(5);
});

test("an optional claim unit may be left empty once confirmed", () => {
  const r = buildReview(
    {
      fields: [
        field("rules.claim_uom", "rules.claim_uom", {
          confidence: "low",
          reasons: ["not_stated"],
          evidence: null,
        }),
      ],
    } as unknown as ExtractionResult,
    { rules: {} },
    { valid: true, errors: [], warnings: [] } as unknown as Validation,
  );
  const uom = need(r, "rules.claim_uom");
  expect(uom.blocking).toBe(false);
  expect(isPending(uom, new Set([uom.id]))).toBe(false);
});

test("template sentences use the numbers the app already has", () => {
  const spa = {
    kind: "spa",
    sample_size: 10,
    totals: { eligible: 9, exceptions: 1, eligible_amount: "3412.50" },
    accrual: null,
  } as unknown as DraftPreview;
  expect(previewSentence(spa)).toBe("If approved: 9 imported sales lines qualify, est. $3,412.50");
  expect(previewSentence({ ...spa, totals: { eligible: 1, eligible_amount: "5" } })).toBe(
    "If approved: 1 imported sales line qualifies, est. $5.00",
  );
  expect(previewSentence({ ...spa, sample_size: 0 })).toBe(
    "If approved: no imported sales lines match this agreement yet",
  );
});

test("list labels say what to do, not a confidence decimal", () => {
  expect(readLabel({ status: "completed", needs_count: 3, draft_status: "draft" })).toBe(
    "Read: needs your review",
  );
  expect(readLabel({ status: "completed", needs_count: 0, draft_status: "draft" })).toBe("Ready to submit");
  expect(readLabel({ status: "completed", needs_count: 0, draft_status: "pending_approval" })).toBe(
    "Submitted for approval",
  );
  expect(readLabel({ status: "failed", needs_count: null, draft_status: null })).toBeNull();
  expect(countsText({ found_count: 12, needs_count: 3 })).toBe(
    "12 found with a quote · 3 need your decision",
  );
  expect(countsText({ found_count: null, needs_count: null })).toBe("—");
});
