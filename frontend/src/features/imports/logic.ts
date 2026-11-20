import type { components } from "../../api/schema";

type ImportOut = components["schemas"]["ImportOut"];
type FieldOut = components["schemas"]["FieldOut"];
export type Mapping = Record<string, string | null>;

export type Step = "processing" | "map" | "review" | "loading" | "done" | "failed" | "discarded";

/** Which wizard step an import is at, from the server's status (the server owns the state machine). */
export function stepOf(i: Pick<ImportOut, "status" | "needs_mapping">): Step {
  switch (i.status) {
    case "awaiting_confirmation":
      return i.needs_mapping ? "map" : "review";
    case "loading":
      return "loading";
    case "completed":
    case "completed_with_errors":
      return "done";
    case "failed":
    case "rejected":
      return "failed";
    case "discarded":
      return "discarded";
    default:
      return "processing"; // uploaded, scanning, parsing
  }
}

/** Poll while the worker is busy with this import. */
export const isBusy = (i: Pick<ImportOut, "status" | "needs_mapping">) =>
  ["processing", "loading"].includes(stepOf(i));

export const STATUS_TEXT: Record<string, string> = {
  uploaded: "Uploaded",
  scanning: "Waiting for the malware scan",
  parsing: "Reading the file",
  awaiting_confirmation: "Waiting for you",
  loading: "Loading rows",
  completed: "Completed",
  completed_with_errors: "Completed, some rows rejected",
  failed: "Failed",
  rejected: "Rejected",
  discarded: "Discarded",
};

export const TYPE_TEXT: Record<string, string> = {
  sales: "Sales",
  purchases: "Purchases",
  customers: "Customers",
  products: "Products",
  uom: "UoM conversions",
};

/** The mapping the form starts with: what the import already uses, else the server's suggestion. */
export function initialMapping(fields: FieldOut[], suggested: Mapping, current: Mapping | null): Mapping {
  return Object.fromEntries(fields.map((f) => [f.name, current?.[f.name] ?? suggested[f.name] ?? null]));
}

/** Required fields with neither a column nor a default (a hint only: the server validates). */
export function unmappedRequired(fields: FieldOut[], mapping: Mapping, defaults: Record<string, string>) {
  return fields.filter((f) => f.required && !mapping[f.name] && !defaults[f.name]?.trim()).map((f) => f.name);
}

/** Only non-empty defaults, for fields without a column. */
export function cleanDefaults(mapping: Mapping, defaults: Record<string, string>): Record<string, string> {
  return Object.fromEntries(
    Object.entries(defaults)
      .map(([k, v]) => [k, v.trim()] as const)
      .filter(([k, v]) => v !== "" && !mapping[k]),
  );
}

/** "unit_price" -> "Unit price". */
export const label = (field: string) => (field.charAt(0).toUpperCase() + field.slice(1)).replaceAll("_", " ");

/** An AI suggestion limited to this import's fields and the file's real columns (the model may name anything).
 * It only pre-fills the form; the user still applies and saves it. */
export function usableSuggestion(fields: FieldOut[], headers: string[], suggested: Mapping): Mapping {
  return Object.fromEntries(
    fields.map((f) => {
      const col = suggested[f.name];
      return [f.name, col && headers.includes(col) ? col : null];
    }),
  );
}
