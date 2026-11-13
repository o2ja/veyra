import { describe, expect, it } from "vitest";

import { formatDecimal, formatFields, isTraceKind, METRIC_KEYS, METRIC_LABELS, TRACE_KINDS } from "./logic";

describe("reporting logic", () => {
  it("formats decimal strings with digit grouping without numeric arithmetic", () => {
    expect(formatDecimal(null)).toBe("—");
    expect(formatDecimal(undefined)).toBe("—");
    expect(formatDecimal("")).toBe("—");
    expect(formatDecimal("0.00")).toBe("0.00");
    expect(formatDecimal("150.00")).toBe("150.00");
    expect(formatDecimal("10722.50")).toBe("10,722.50");
    expect(formatDecimal("1234567.89")).toBe("1,234,567.89");
    expect(formatDecimal("-12345.67")).toBe("-12,345.67");
    expect(formatDecimal("invalid-text")).toBe("invalid-text");
  });

  it("provides metric labels for all metric keys", () => {
    for (const key of METRIC_KEYS) {
      expect(METRIC_LABELS[key]).toBeDefined();
      expect(METRIC_LABELS[key]?.length).toBeGreaterThan(0);
    }
    expect(METRIC_LABELS["recovered_after_write_off"]).toBe("Recovered after write-off");
  });

  it("identifies valid trace kinds", () => {
    for (const kind of TRACE_KINDS) {
      expect(isTraceKind(kind)).toBe(true);
    }
    expect(isTraceKind("unknown")).toBe(false);
    expect(isTraceKind("rebate_accrual")).toBe(false);
  });

  it("formats fields dictionary into key/value items", () => {
    const formatted = formatFields({
      source_file: "sales_data.csv",
      row_number: "42",
      notes: null,
    });
    expect(formatted).toEqual([
      { key: "source file", value: "sales_data.csv" },
      { key: "row number", value: "42" },
      { key: "notes", value: "—" },
    ]);
  });
});
