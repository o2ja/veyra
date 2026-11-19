import { expect, test } from "vitest";

import {
  buildTraceTree,
  collectExpandableIds,
  exceptionLabel,
  explainFormula,
  flattenKeyValues,
  runSummary,
} from "./logic";

test("explainFormula restates the server figures as one sentence", () => {
  expect(
    explainFormula({
      status: "eligible",
      claim_amount: "30.0000",
      claim_unit_amount: "0.5000",
      eligible_quantity: "60.000000",
      claim_uom: "EA",
    }),
  ).toBe("Claim $30.00 = 60 EA eligible × $0.50 per unit, after the contract's rounding rule.");
  expect(explainFormula({ status: "exception", exception_code: "no_agreement" })).toBe(
    "Nothing claimed: no contract on file.",
  );
});

test("runSummary reads a run's totals as one plain line", () => {
  expect(
    runSummary({
      lines: 35,
      by_status: { eligible: 7, exception: 26, not_applicable: 2 },
      eligible_amount: "8472.5000",
    }),
  ).toBe("35 lines · 7 eligible · $8,472.50 · 26 exceptions");
  expect(runSummary({ lines: 1, by_status: {}, eligible_amount: "0" })).toBe(
    "1 line · 0 eligible · $0.00 · 0 exceptions",
  );
  expect(runSummary({})).toBe("No results yet");
});

test("exceptionLabel explains engine codes and falls back to readable text", () => {
  expect(exceptionLabel("no_agreement")).toBe("No contract on file");
  expect(exceptionLabel("brand_new_code")).toBe("brand new code");
  expect(exceptionLabel(null)).toBe("—");
});

test("flattenKeyValues flattens nested objects without computing anything", () => {
  expect(
    flattenKeyValues({
      eligible_count: 12,
      amounts: { claim_total: "1234.50", currency: "USD" },
      empty: {},
      flags: ["a", "b"],
    }),
  ).toEqual([
    { key: "eligible_count", value: "12" },
    { key: "amounts.claim_total", value: "1234.50" },
    { key: "amounts.currency", value: "USD" },
    { key: "empty", value: "{}" },
    { key: "flags", value: '["a","b"]' },
  ]);
});

test("buildTraceTree builds nested tree nodes for objects and arrays", () => {
  const tree = buildTraceTree({
    rule: "price_difference",
    inputs: { sell_price: "88.00", deviated_cost: "78.00" },
    steps: [{ step: "cap", applied: true }],
    empty_list: [],
  });
  expect(tree).toEqual([
    { id: "trace.rule", key: "rule", value: "price_difference", children: [] },
    {
      id: "trace.inputs",
      key: "inputs",
      value: null,
      children: [
        { id: "trace.inputs.sell_price", key: "sell_price", value: "88.00", children: [] },
        { id: "trace.inputs.deviated_cost", key: "deviated_cost", value: "78.00", children: [] },
      ],
    },
    {
      id: "trace.steps",
      key: "steps",
      value: null,
      children: [
        {
          id: "trace.steps[0]",
          key: "[0]",
          value: null,
          children: [
            { id: "trace.steps[0].step", key: "step", value: "cap", children: [] },
            { id: "trace.steps[0].applied", key: "applied", value: "true", children: [] },
          ],
        },
      ],
    },
    { id: "trace.empty_list", key: "empty_list", value: "[]", children: [] },
  ]);

  expect(collectExpandableIds(tree)).toEqual(["trace.inputs", "trace.steps", "trace.steps[0]"]);
});
