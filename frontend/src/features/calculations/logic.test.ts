import { expect, test } from "vitest";

import { buildTraceTree, collectExpandableIds, flattenKeyValues, formatDecimal } from "./logic";

test("formatDecimal groups thousands and preserves exact fractional digits", () => {
  expect(formatDecimal("1234567.8900")).toBe("1,234,567.8900");
  expect(formatDecimal("-42000.5")).toBe("-42,000.5");
  expect(formatDecimal("999")).toBe("999");
  expect(formatDecimal("0.0000")).toBe("0.0000");
  expect(formatDecimal(null)).toBe("—");
  expect(formatDecimal(undefined)).toBe("—");
  expect(formatDecimal("")).toBe("—");
  expect(formatDecimal("N/A")).toBe("N/A");
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
