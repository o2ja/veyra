import { expect, test } from "vitest";

import type { Validation } from "../agreements/logic";
import type { ExtractionFieldView, ExtractionResult } from "./logic";
import {
  buildReview,
  countsText,
  displayValue,
  editText,
  fractionToPercent,
  getAt,
  isPending,
  matchSentence,
  parseEdit,
  pendingCount,
  percentToFraction,
  previewSentence,
  readLabel,
  safeText,
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
  header: { valid_to: "2026-12-31", agreement_type: "spa" },
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
    label: "Item A · Rate",
    why: "That rate is unusually high.",
    blocking: false,
    warningIds: ["w1"],
    display: "5%",
  });
  expect(byId["items[1].sku"]).toMatchObject({
    label: "Item Z · Part number",
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
    { header: { agreement_type: "spa" }, rules: {} },
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

const ok = { valid: true, errors: [], warnings: [] } as unknown as Validation;
const asResult = (...fields: ExtractionFieldView[]) => ({ fields }) as unknown as ExtractionResult;
const unstated = (key: string, over: Partial<ExtractionFieldView> = {}) =>
  field(key, null, { confidence: "low", reasons: ["not_stated"], evidence: null, ...over });
const RAW = /header\.|rules\.|items\[|customers\[|exclusions_text|\[object Object\]|discriminator/;
const bad = (...errors: ReturnType<typeof issue>[]) =>
  ({ valid: false, errors, warnings: [] }) as unknown as Validation;

test("raw field keys become plain-English labels, never dot or bracket notation", () => {
  const r = buildReview(
    asResult(
      unstated("header.date_basis"),
      unstated("rules.spa_lines_policy"),
      unstated("rules.claim_uom"),
      field("customers[Summit Industrial Builders LLC].name", null, { value: "Summit", evidence: null }),
      field("items[BRK-3P-100A].uom", null, { value: "EA", confidence: "low", evidence: null }),
      field("exclusions_text[#0]", null, { value: "Excludes tax", confidence: "low", evidence: null }),
    ),
    { header: { agreement_type: "spa" }, rules: {}, items: [], customers: [] },
    bad(issue("payload.rules.date_basis", "required", "Field required")),
  );
  const labels = [...r.needs, ...r.found].map((i) => i.label);
  expect(labels).toContain("Which date decides eligibility");
  expect(labels).toContain("Unit of measure");
  expect(labels).toContain("Customer · Summit Industrial Builders LLC");
  expect(labels).toContain("Item BRK-3P-100A · Unit of measure");
  expect(labels).toContain("Exclusion #1");
  for (const l of labels) expect(l).not.toMatch(RAW);
});

test("a field the extraction and the checks both report is one item, with its editor", () => {
  const r = buildReview(
    asResult(unstated("header.agreement_type"), unstated("items[BRK].part_number")),
    { header: {}, rules: {}, items: [{ rate: null }] },
    bad(
      issue("payload.header.agreement_type", "required", "Field required"),
      issue("payload.rules", "invalid", "Unable to extract tag using discriminator 'type'"),
    ),
  );
  expect(r.needs.filter((i) => i.path === "header.agreement_type")).toHaveLength(1);
  const type = need(r, "header.agreement_type");
  expect(type).toMatchObject({ label: "Agreement type", blocking: true, display: "—" });
  expect(type.input?.kind).toBe("select");
  expect(r.needs[0]).toBe(type);
  expect(r.needs).toHaveLength(1); // the cascading rules error stays quiet, the empty row field is dropped
});

test("a part number that is both extracted and flagged shows once", () => {
  const r = buildReview(
    asResult(field("items[BRK].part_number", null, { value: "BRK", confidence: "low" })),
    { header: { agreement_type: "spa" }, items: [{ sku: "BRK" }] },
    bad(issue("payload.items[0].sku", "unresolved", "Unknown SKU BRK.")),
  );
  expect([...r.needs, ...r.found].filter((i) => i.path === "items[0].sku")).toHaveLength(1);
});

test("fields for the other agreement type are not asked about when nothing was stated", () => {
  const spa = buildReview(
    asResult(
      unstated("rules.tier_mode"),
      unstated("rules.volume_basis"),
      unstated("rules.measurement_period"),
      unstated("header.date_basis"),
    ),
    { header: { agreement_type: "spa" }, rules: {} },
    ok,
  );
  expect(spa.needs.map((i) => i.id)).toEqual(["rules.date_basis"]);

  const rebate = buildReview(
    asResult(unstated("rules.spa_reference_required"), unstated("rules.tier_mode")),
    { header: { agreement_type: "rebate" }, rules: {} },
    bad(issue("payload.rules.tier_mode", "required", "Field required")),
  );
  expect(rebate.needs.map((i) => i.id)).toEqual(["rules.tier_mode"]);
});

test("until the agreement type is chosen, type-specific fields stay hidden", () => {
  const r = buildReview(
    asResult(unstated("header.agreement_type"), unstated("header.date_basis"), unstated("rules.tier_mode")),
    { header: {}, rules: {} },
    ok,
  );
  expect(r.needs.map((i) => i.id)).toEqual(["header.agreement_type"]);
});

test("unflagged, unstated optional fields do not clutter the list", () => {
  const r = buildReview(
    asResult(
      unstated("header.external_number", { critical: false }),
      unstated("rules.cost_source", { critical: false }),
    ),
    { header: { agreement_type: "spa" }, rules: {} },
    ok,
  );
  expect(r.needs).toEqual([]);
});

test("objects are never printed as [object Object]", () => {
  expect(displayValue("rules", { type: "spa" })).toBe("—");
  expect(editText("rules.rounding", { mode: "half_up" })).toBe("");
  expect(safeText(["a", "b"])).toBe("a, b");
  expect(safeText([{ a: 1 }])).toBe("—");
  const r = buildReview(
    asResult(field("exclusions_text[#0]", null, { value: { a: 1 }, confidence: "low", evidence: null })),
    { header: { agreement_type: "spa" } },
    bad(issue("payload.rules", "invalid", "Something about rules")),
  );
  for (const i of [...r.needs, ...r.found]) {
    expect(i.display).not.toContain("[object Object]");
    expect(i.why).not.toMatch(RAW);
  }
});
