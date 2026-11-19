import { describe, expect, it } from "vitest";

import { formatDate, formatDateTime, formatMoney, formatQty } from "./format";

describe("formatMoney", () => {
  it("shows a dollar sign and two decimals without rounding", () => {
    expect(formatMoney("3412.5")).toBe("$3,412.50");
    expect(formatMoney("312.5000")).toBe("$312.50");
    expect(formatMoney("0")).toBe("$0.00");
    expect(formatMoney("1234567.89")).toBe("$1,234,567.89");
    expect(formatMoney("-12345.67")).toBe("-$12,345.67");
    expect(formatMoney("-0.00")).toBe("$0.00");
    expect(formatMoney("0.1234")).toBe("$0.1234");
    expect(formatMoney("007.10")).toBe("$7.10");
  });
  it("passes through empty and non-numeric values", () => {
    expect(formatMoney(null)).toBe("—");
    expect(formatMoney("")).toBe("—");
    expect(formatMoney("N/A")).toBe("N/A");
  });
});

describe("formatQty", () => {
  it("trims trailing zeros", () => {
    expect(formatQty("60.000000")).toBe("60");
    expect(formatQty("2.500")).toBe("2.5");
    expect(formatQty("1200")).toBe("1,200");
    expect(formatQty("-3.0")).toBe("-3");
    expect(formatQty(undefined)).toBe("—");
  });
});

describe("dates", () => {
  it("never shifts a business date across time zones", () => {
    expect(formatDate("2026-11-18")).toBe("Nov 18, 2026");
    expect(formatDate("2026-01-01")).toBe("Jan 1, 2026");
  });
  it("formats timestamps in a readable form, never raw ISO", () => {
    const out = formatDateTime("2026-11-18T22:50:25Z");
    expect(out).not.toContain("T22");
    expect(out).toMatch(/^Nov 1[89], 2026, \d{1,2}:\d{2}\s?[AP]M$/);
    expect(formatDate(null)).toBe("—");
    expect(formatDateTime("garbage")).toBe("garbage");
  });
});
