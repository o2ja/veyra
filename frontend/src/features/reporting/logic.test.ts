import { describe, expect, it } from "vitest";

import { jobPollDecision, formatFields, isTraceKind, METRIC_KEYS, METRIC_LABELS, TRACE_KINDS } from "./logic";

describe("reporting logic", () => {
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

describe("jobPollDecision", () => {
  it("polls until a terminal status", () => {
    expect(jobPollDecision(undefined)).toBe("poll");
    expect(jobPollDecision("queued")).toBe("poll");
    expect(jobPollDecision("running")).toBe("poll");
    expect(jobPollDecision("succeeded")).toBe("succeeded");
    expect(jobPollDecision("failed")).toBe("failed");
    // legacy names are not statuses of this API: keep polling rather than offering a download
    expect(jobPollDecision("completed")).toBe("poll");
    expect(jobPollDecision("finished")).toBe("poll");
  });
});
