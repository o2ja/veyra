import { expect, test } from "vitest";

import {
  confidenceText,
  formatEvidenceLocation,
  formatExtractionValue,
  groupAndSortFields,
  isFieldFlagged,
  parseExtractionResult,
  type ExtractionFieldView,
} from "./logic";

function field(overrides: Partial<ExtractionFieldView>): ExtractionFieldView {
  return {
    key: "header.name",
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

test("isFieldFlagged detects reasons and pass status mismatches", () => {
  expect(isFieldFlagged(field({}))).toBe(false);
  expect(isFieldFlagged(field({ reasons: ["pass_conflict"] }))).toBe(true);
  expect(
    isFieldFlagged(
      field({
        pass_a: { status: "matched", value: "A", reasons: [], evidence: null },
        pass_b: { status: "conflict", value: "B", reasons: ["diff"], evidence: null },
      }),
    ),
  ).toBe(true);
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

test("parseExtractionResult validates shape", () => {
  expect(parseExtractionResult(null)).toBeNull();
  expect(parseExtractionResult({ foo: "bar" })).toBeNull();
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
