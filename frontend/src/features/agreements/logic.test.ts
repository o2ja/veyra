import { expect, test } from "vitest";

import {
  intOrRaw,
  buildAmendmentPayload,
  emptyPayload,
  flattenRulesEntries,
  friendlyIssueMessage,
  issueFieldId,
  issueFieldLabel,
  itemIndexFromIssuePath,
  pageForItemIndex,
  paginateItems,
  applyEdit,
  payloadFromDraft,
  viewPayload,
  switchPayloadType,
  setAgreementType,
  type ItemOut,
  type VersionOut,
} from "./logic";

test("payloadFromDraft fills defaults for empty draft and preserves server fields", () => {
  const blank = payloadFromDraft({});
  expect(blank.payload_schema_version).toBe(1);
  expect(blank.header.agreement_type).toBe("spa");
  expect(blank.rules.type).toBe("spa");
  expect(blank.items).toEqual([]);

  const rebate = payloadFromDraft({
    header: { agreement_type: "rebate", external_number: "REB-1", manufacturer_code: "VOLT", name: "R" },
    rules: { type: "rebate", tier_metric: "quantity", tier_mode: "incremental" },
    tiers: [{ tier_number: 1, lower_bound: "100", rate: "0.02" }],
  });
  expect(rebate.header.external_number).toBe("REB-1");
  expect(rebate.rules.type).toBe("rebate");
  if (rebate.rules.type === "rebate") {
    expect(rebate.rules.tier_metric).toBe("quantity");
    expect(rebate.rules.tier_mode).toBe("incremental");
  }
  expect(rebate.tiers).toEqual([{ tier_number: 1, lower_bound: "100", rate: "0.02" }]);
});

test("switchPayloadType switches rules and clears tiers when switching to spa", () => {
  const spa = emptyPayload("spa");
  spa.header.valid_from = "2026-01-01";
  spa.header.valid_to = "2026-12-31";
  const reb = switchPayloadType(spa, "rebate");
  expect(reb.header.agreement_type).toBe("rebate");
  expect(reb.rules.type).toBe("rebate");
  if (reb.rules.type === "rebate") {
    expect(reb.rules.measurement_period.start).toBe("2026-01-01");
    expect(reb.rules.measurement_period.end).toBe("2026-12-31");
  }
  reb.tiers = [{ tier_number: 1, lower_bound: "0", rate: "0.01" }];
  const backToSpa = switchPayloadType(reb, "spa");
  expect(backToSpa.rules.type).toBe("spa");
  expect(backToSpa.tiers).toEqual([]);
});

test("buildAmendmentPayload copies version fields and items without computation", () => {
  const version: VersionOut = {
    id: "v1",
    agreement_id: "a1",
    agreement_type: "spa",
    external_number: "SPA-1",
    name: "Test SPA",
    priority: 2,
    valid_from: "2026-01-01",
    valid_to: "2026-12-31",
    manufacturer_id: "m1",
    version_number: 1,
    status: "active",
    activated_at: "2026-01-01T00:00:00Z",
    approved_by: "u1",
    created_by: "u2",
    content_hash: "abc",
    derived_from_version_id: null,
    draft_id: "d1",
    item_count: 1,
    rules: { type: "spa", date_basis: "invoice_date" },
    rules_schema_version: 1,
    source: "manual",
    source_file_id: null,
    superseded_at: null,
    superseded_by_version_id: null,
    supersedes_version_id: null,
    customers: [{ customer_id: "c1", customer_code: "ALDER", scope: "include" }],
    tiers: [],
  };
  const items: ItemOut[] = [
    {
      id: "i1",
      line_number: 1,
      product_id: "p1",
      customer_id: null,
      sku: "00418",
      reference_cost: "100.00",
      deviated_cost: "80.00",
      sell_price: "90.00",
      rate: null,
      flat_amount: null,
      max_quantity: "50",
      claim_uom: "EA",
    },
  ];
  const payload = buildAmendmentPayload(version, "VOLT", items);
  expect(payload.header.manufacturer_code).toBe("VOLT");
  expect(payload.header.priority).toBe(2);
  expect(payload.customers).toEqual([{ customer_code: "ALDER", scope: "include" }]);
  expect(payload.items).toEqual([
    {
      sku: "00418",
      reference_cost: "100.00",
      deviated_cost: "80.00",
      sell_price: "90.00",
      rate: null,
      flat_amount: null,
      max_quantity: "50",
      claim_uom: "EA",
    },
  ]);
});

test("issueFieldId maps payload paths to field DOM ids", () => {
  expect(issueFieldId("payload.items[3].sku")).toBe("field-items-3-sku");
  expect(issueFieldId("items.0.deviated_cost")).toBe("field-items-0-deviated_cost");
  expect(issueFieldId("payload.header.valid_to")).toBe("field-header-valid_to");
  expect(issueFieldId("payload.rules.calculation.basis")).toBe("field-rules-calculation-basis");
  expect(issueFieldId("payload.tiers[1].rate")).toBe("field-tiers-1-rate");
  expect(issueFieldId("unknown.field")).toBeNull();
  expect(issueFieldId("")).toBeNull();
});

test("itemIndexFromIssuePath and pagination helpers resolve item pages", () => {
  expect(itemIndexFromIssuePath("payload.items[150].sku")).toBe(150);
  expect(itemIndexFromIssuePath("items.0.rate")).toBe(0);
  expect(itemIndexFromIssuePath("payload.header.name")).toBeNull();

  expect(pageForItemIndex(0)).toBe(1);
  expect(pageForItemIndex(99)).toBe(1);
  expect(pageForItemIndex(100)).toBe(2);
  expect(pageForItemIndex(250)).toBe(3);

  const items = Array.from({ length: 205 }, (_, i) => i);
  const p3 = paginateItems(items, 3, 100);
  expect(p3.totalPages).toBe(3);
  expect(p3.startIndex).toBe(200);
  expect(p3.slice).toEqual([200, 201, 202, 203, 204]);
});

test("flattenRulesEntries flattens nested rules for read-only display", () => {
  expect(
    flattenRulesEntries({ type: "spa", rounding: { mode: "half_up", scale: 2 }, adapter: null }),
  ).toEqual([
    { key: "type", value: "spa" },
    { key: "rounding.mode", value: "half_up" },
    { key: "rounding.scale", value: "2" },
    { key: "adapter", value: "—" },
  ]);
});

test("payloadFromDraft keeps server values verbatim; edits apply only the user's change", () => {
  const raw = {
    header: { agreement_type: "spa", priority: "7", extra: 1 },
    rules: {
      type: "spa",
      price_check: { mode: "flag", tolerance: 0.005 },
      rounding: { scale: "x" },
      unknown: { a: 1 },
    },
    tiers: [{ tier_number: "a", lower_bound: "1", rate: "2" }],
  };
  const p = payloadFromDraft(raw);
  expect(p).toEqual(raw);
  const v = viewPayload(p);
  expect(v.header.name).toBe("");
  const next = { ...v, header: { ...v.header, name: "N" } };
  const out = applyEdit(p, v, next) as typeof raw & { header: { name: string } };
  expect(out.header).toEqual({ agreement_type: "spa", priority: "7", extra: 1, name: "N" });
  expect(out.rules).toEqual(raw.rules);
  expect(out.tiers).toEqual(raw.tiers);
});

test("a decision the document did not state stays empty: not shown as a default and never saved as one", () => {
  const raw = {
    header: { agreement_type: "spa", external_number: "X-1" },
    rules: { type: "spa", price_check: { mode: "flag", tolerance: "0.005" } },
  };
  const p = payloadFromDraft(raw);
  const v = viewPayload(p);
  const rules = v.rules as unknown as Record<string, Record<string, unknown> | undefined>;
  expect(rules.date_basis).toBeUndefined();
  expect(rules.calculation?.basis).toBeUndefined();
  expect(rules.rounding?.mode).toBeUndefined();
  expect(rules.rounding?.scale).toBeUndefined();
  expect(rules.spa_reference?.required).toBeUndefined();
  expect(rules.price_check?.mode).toBe("flag"); // a stated value still shows

  const edited = applyEdit(p, v, { ...v, header: { ...v.header, name: "N" } });
  expect(JSON.stringify(edited)).not.toMatch(/ship_date|price_difference|half_up|per_line/);

  const chosen = applyEdit(p, v, { ...v, rules: { ...v.rules, date_basis: "invoice_date" } }) as typeof raw;
  expect(JSON.stringify(chosen.rules)).toBe(
    JSON.stringify({
      type: "spa",
      price_check: { mode: "flag", tolerance: "0.005" },
      date_basis: "invoice_date",
    }),
  );
});

test("buildAmendmentPayload copies rules as returned", () => {
  const rules = { type: "spa", weird: [1], price_check: { tolerance: 3 } };
  const p = buildAmendmentPayload(
    { agreement_type: "spa", rules, customers: [], tiers: [] } as unknown as VersionOut,
    "M",
    [],
  );
  expect(p.rules).toBe(rules);
});

test("intOrRaw converts whole numbers and passes anything else through unchanged", () => {
  expect(intOrRaw("7")).toBe(7);
  expect(intOrRaw(" -3 ")).toBe(-3);
  expect(intOrRaw("")).toBe("");
  expect(intOrRaw("2.5")).toBe("2.5");
  expect(intOrRaw("abc")).toBe("abc");
});

test("applyEdit adds or removes one row without filling defaults into the other rows", () => {
  const raw = [{ sku: "A" }, { sku: "B", rate: 5 }]; // server rows with missing and odd values
  const view = [
    { sku: "A", rate: null },
    { sku: "B", rate: 5 },
  ];
  const added = applyEdit(raw, view, [...view, { sku: "", rate: null }]);
  expect(added).toEqual([{ sku: "A" }, { sku: "B", rate: 5 }, { sku: "", rate: null }]);
  const removed = applyEdit(raw, view, [view[1]]);
  expect(removed).toEqual([{ sku: "B", rate: 5 }]);
  const removedLast = applyEdit(raw, view, [view[0]]);
  expect(removedLast).toEqual([{ sku: "A" }]);
});

test("validation issues read as plain English", () => {
  expect(issueFieldLabel("payload.items[3].sell_price")).toBe("Item 4 · Sell price");
  expect(issueFieldLabel("header.valid_to")).toBe("Valid to");
  expect(issueFieldLabel("payload.tiers.0.rate")).toBe("Tier 1 · Rate");
  expect(friendlyIssueMessage("Field required")).toBe("This is required.");
  expect(friendlyIssueMessage("Input should be a valid decimal")).toBe("Enter a number, for example 12.50.");
  expect(friendlyIssueMessage("Input should be greater than 0")).toBe("Must be more than 0.");
  expect(friendlyIssueMessage("Input should be 'spa' or 'rebate'")).toBe("Choose one of: spa, rebate.");
  expect(friendlyIssueMessage("Value error, valid_to must be after valid_from")).toBe(
    "Valid_to must be after valid_from",
  );
  expect(friendlyIssueMessage("SKU is listed twice.")).toBe("SKU is listed twice.");
});

test("setAgreementType sets header and rules.type together, keeps read values, leaves decisions empty", () => {
  const read = {
    payload_schema_version: 1,
    header: { external_number: "X-1" },
    rules: { date_basis: "invoice_date" },
    items: [],
    customers: [],
    tiers: [{ tier_number: 1, lower_bound: "0", rate: "0.01" }],
  } as unknown as Parameters<typeof setAgreementType>[0];
  const spa = setAgreementType(read, "spa");
  expect(spa.header.agreement_type).toBe("spa");
  expect(spa.rules.type).toBe("spa");
  expect(spa.header.external_number).toBe("X-1");
  expect((spa.rules as { date_basis?: string }).date_basis).toBe("invoice_date");
  expect(JSON.parse(JSON.stringify(spa.rules)).rounding).toEqual({});
  expect(spa.tiers).toEqual([]);
  const rebate = setAgreementType(read, "rebate");
  expect(rebate.rules.type).toBe("rebate");
  expect(JSON.parse(JSON.stringify(rebate.rules)).tier_mode).toBeUndefined();
  expect(rebate.tiers).toHaveLength(1);
  expect(setAgreementType(spa, "spa")).toBe(spa);
});
