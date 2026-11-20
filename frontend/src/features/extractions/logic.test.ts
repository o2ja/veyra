import { expect, test } from "vitest";

import {
  confidenceText,
  formatEvidenceLocation,
  formatExtractionValue,
  groupAndSortFields,
  parseExtractionResult,
  readConfirmed,
  writeConfirmed,
  type ExtractionFieldView,
} from "./logic";

function field(overrides: Partial<ExtractionFieldView>): ExtractionFieldView {
  return {
    key: "header.name",
    payload_path: null,
    critical: false,
    value: "Test",
    confidence: "0.95",
    reasons: [],
    evidence: null,
    pass_a: null,
    pass_b: null,
    ...overrides,
  };
}

test("groupAndSortFields groups critical fields before other fields", () => {
  const fields = [
    field({ key: "notes", critical: false }),
    field({ key: "header.valid_from", critical: true }),
    field({ key: "header.priority", critical: false }),
    field({ key: "header.valid_to", critical: true }),
  ];
  const grouped = groupAndSortFields(fields);
  expect(grouped.critical.map((f) => f.key)).toEqual(["header.valid_from", "header.valid_to"]);
  expect(grouped.other.map((f) => f.key)).toEqual(["notes", "header.priority"]);
});

test("formatEvidenceLocation formats page, sheet, and cell", () => {
  expect(formatEvidenceLocation(null)).toBe("");
  expect(formatEvidenceLocation({ page: 3, sheet: null, cell: null })).toBe("Page 3");
  expect(formatEvidenceLocation({ page: null, sheet: "Rates", cell: "B12" })).toBe(
    'Sheet "Rates" · Cell B12',
  );
  expect(formatEvidenceLocation({ page: 1, sheet: "S1", cell: "A1" })).toBe('Page 1 · Sheet "S1" · Cell A1');
});

test("confidenceText and formatExtractionValue render plain text", () => {
  expect(confidenceText("high")).toBe("Confidence: high");
  expect(confidenceText(null)).toBe("Confidence: —");
  expect(formatExtractionValue(null)).toBe("—");
  expect(formatExtractionValue("")).toBe("—");
  expect(formatExtractionValue("SPA-1")).toBe("SPA-1");
  expect(formatExtractionValue({ a: 1 })).toBe('{"a":1}');
});

test("parseExtractionResult accepts valid result and rejects malformed shapes", () => {
  expect(parseExtractionResult(null)).toBeNull();
  expect(parseExtractionResult({ foo: "bar" })).toBeNull();
  expect(
    parseExtractionResult({
      fields: [],
      blocking: [],
      needs_acknowledgement: [],
      indicators: [],
      notes: { a: "not-an-array", b: [] },
      unresolved: {},
    }),
  ).toBeNull();
  expect(
    parseExtractionResult({
      fields: [],
      blocking: "nope",
      needs_acknowledgement: [],
      indicators: [],
      notes: { a: [], b: [] },
      unresolved: {},
    }),
  ).toBeNull();
  expect(
    parseExtractionResult({
      fields: [],
      blocking: [],
      needs_acknowledgement: [],
      indicators: [],
      notes: { a: [], b: [] },
      unresolved: [],
    }),
  ).toBeNull();

  const valid = parseExtractionResult({
    agreement_type: "spa",
    blocking: [],
    draft_id: "d1",
    fields: [],
    indicators: [],
    needs_acknowledgement: [],
    notes: { a: [], b: [] },
    payload: {},
    schema_version: 1,
    unresolved: {},
  });
  expect(valid?.draft_id).toBe("d1");
});

test("confirmations are remembered per extraction and dropped once the draft changed", () => {
  writeConfirmed("ex-1", "2026-11-20T10:00:00Z", new Set(["a", "b"]));
  expect(readConfirmed("ex-1", "2026-11-20T10:00:00Z")).toEqual(["a", "b"]);
  expect(readConfirmed("ex-1", "2026-11-20T10:05:00Z")).toEqual([]);
  expect(readConfirmed("ex-2", "2026-11-20T10:00:00Z")).toEqual([]);
});
