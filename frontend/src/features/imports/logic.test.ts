import { expect, test } from "vitest";

import { firstPage, nextPage, pageCursor, prevPage } from "../masterdata/common";
import { cleanDefaults, initialMapping, label, stepOf, unmappedRequired, usableSuggestion } from "./logic";

const fields = [
  { name: "document_number", kind: "text", required: true },
  { name: "currency_code", kind: "text", required: true },
  { name: "spa_reference", kind: "text", required: false },
];

test("each server status maps to one wizard step", () => {
  const at = (status: string, needs_mapping = false) => stepOf({ status, needs_mapping });
  expect(["uploaded", "scanning", "parsing"].map((s) => at(s))).toEqual([
    "processing",
    "processing",
    "processing",
  ]);
  expect(at("awaiting_confirmation", true)).toBe("map");
  expect(at("awaiting_confirmation", false)).toBe("review");
  expect(at("loading")).toBe("loading");
  expect([at("completed"), at("completed_with_errors")]).toEqual(["done", "done"]);
  expect([at("failed"), at("rejected")]).toEqual(["failed", "failed"]);
  expect(at("discarded")).toBe("discarded"); // terminal: must not fall into "processing" and poll forever
});

test("the mapping form starts from the current mapping, then the server's suggestion", () => {
  const suggested = { document_number: "invoice_no", currency_code: null, spa_reference: "spa_ref" };
  expect(initialMapping(fields, suggested, null)).toEqual(suggested);
  expect(initialMapping(fields, suggested, { document_number: "Invoice #", spa_reference: null })).toEqual({
    document_number: "Invoice #",
    currency_code: null,
    spa_reference: "spa_ref", // a field the saved mapping left open falls back to the suggestion
  });
});

test("required fields need a column or a default; defaults for mapped fields are dropped", () => {
  const mapping = { document_number: "invoice_no", currency_code: null, spa_reference: null };
  expect(unmappedRequired(fields, mapping, {})).toEqual(["currency_code"]);
  expect(unmappedRequired(fields, mapping, { currency_code: "  " })).toEqual(["currency_code"]);
  expect(unmappedRequired(fields, mapping, { currency_code: "USD" })).toEqual([]);
  expect(cleanDefaults(mapping, { currency_code: " USD ", document_number: "X", spa_reference: "" })).toEqual(
    {
      currency_code: "USD",
    },
  );
});

test("keyset pager keeps a cursor stack for previous/next", () => {
  let p = firstPage();
  expect(pageCursor(p)).toBeNull();
  p = nextPage(p, "c2");
  p = nextPage(p, "c3");
  expect(pageCursor(p)).toBe("c3");
  expect(pageCursor(nextPage(p, null))).toBe("c3"); // no next cursor: stay
  p = prevPage(p);
  expect(pageCursor(p)).toBe("c2");
  expect(pageCursor(prevPage(prevPage(p)))).toBeNull(); // never below page 1
});

test("field labels are readable", () => {
  expect(label("unit_price")).toBe("Unit price");
});

test("an AI suggestion keeps only known fields and real columns", () => {
  const out = usableSuggestion(fields, ["Invoice #", "Cur"], {
    document_number: "Invoice #",
    currency_code: "Made up column",
    not_a_field: "Cur",
  });
  expect(out).toEqual({ document_number: "Invoice #", currency_code: null, spa_reference: null });
});
