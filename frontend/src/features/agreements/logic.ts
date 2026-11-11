import type { components } from "../../api/schema";

// Pure helpers for the agreement screens (S4.8). The server owns validation and every business rule:
// the editor only edits the JSON payload (`payload_schema_version` 1, BUSINESS_LOGIC.md §3.3) and shows
// what the validator says.

export type Change = components["schemas"]["Change"];
export type Issue = components["schemas"]["Issue"];
export type Validation = components["schemas"]["Validation"];
export type Json = Record<string, unknown>;
export type AgreementType = "spa" | "rebate";

const isObject = (v: unknown): v is Json => typeof v === "object" && v !== null && !Array.isArray(v);

/** Read a value at a dot path (`header.name`, `items.3.sku`). */
export function getAt(root: unknown, path: string): unknown {
  let node = root;
  for (const key of path.split(".")) {
    if (Array.isArray(node)) node = node[Number(key)];
    else if (isObject(node)) node = node[key];
    else return undefined;
  }
  return node;
}

/** Copy of `root` with `value` at `path` (missing objects are created). `undefined` removes the key. */
export function setAt<T>(root: T, path: string, value: unknown): T {
  const [key, ...rest] = path.split(".");
  if (key === undefined) return root;
  const inner = rest.length ? setAt(getAt(root, key) ?? {}, rest.join("."), value) : value;
  if (Array.isArray(root)) {
    const copy = [...root];
    copy[Number(key)] = inner;
    return copy as T;
  }
  const copy: Json = { ...(isObject(root) ? root : {}) };
  if (inner === undefined) delete copy[key];
  else copy[key] = inner;
  return copy as T;
}

/** Validator field (`payload.items[3].sku`) → editor path (`items.3.sku`). */
export const editorPath = (field: string): string =>
  field.replace(/^payload\.?/, "").replace(/\[(\d+)\]/g, ".$1");

/** DOM id of the control that edits `path` (validation messages link to it). */
export const fieldId = (path: string): string => `f-${path.replace(/[^A-Za-z0-9_-]/g, "-")}`;

/** Row index of an items path (`items.3.sku` → 3), for scrolling the virtualised grid. */
export function itemRow(path: string): number | null {
  const m = /^items\.(\d+)(\.|$)/.exec(path);
  return m ? Number(m[1]) : null;
}

/**
 * Text field → payload value. Empty means "not set": optional item/tier cells become `null` (the schema
 * allows null there), header and rules keys are removed so the validator reports `required`.
 */
export function textValue(text: string, empty: "null" | "remove" = "remove"): string | null | undefined {
  if (text.trim() === "") return empty === "null" ? null : undefined;
  return text.trim();
}

/** Integer fields: digits become a JSON number; anything else is sent as typed for the server to refuse. */
export function intValue(text: string): number | string | undefined {
  const t = text.trim();
  if (t === "") return undefined;
  return /^-?\d{1,9}$/.test(t) ? Number(t) : t;
}

/** Display any payload value in a text input. */
export const asText = (v: unknown): string => (v === null || v === undefined ? "" : String(v));

/** The payload a new manual draft starts with: only the structure, no business defaults. */
export function skeleton(type: AgreementType): Json {
  return {
    payload_schema_version: 1,
    header: { agreement_type: type },
    rules: { schema_version: 1, type, ...(type === "spa" ? { product_scope: { mode: "listed" } } : {}) },
    customers: [],
    items: [],
    tiers: [],
  };
}

export const ITEM_FIELDS = [
  { key: "sku", label: "SKU" },
  { key: "reference_cost", label: "Reference cost" },
  { key: "deviated_cost", label: "Deviated cost" },
  { key: "sell_price", label: "Sell price" },
  { key: "rate", label: "Rate (fraction)" },
  { key: "flat_amount", label: "Flat amount" },
  { key: "max_quantity", label: "Max quantity" },
  { key: "claim_uom", label: "Claim UoM" },
] as const;

export const emptyItem = (): Json => Object.fromEntries(ITEM_FIELDS.map((f) => [f.key, null]));

/** Labels for payload paths, used by the validation panel and the version diff. */
const LABELS: Record<string, string> = {
  payload_schema_version: "Payload schema version",
  "header.agreement_type": "Agreement type",
  "header.manufacturer_code": "Manufacturer",
  "header.external_number": "Agreement number",
  "header.name": "Name",
  "header.valid_from": "Valid from",
  "header.valid_to": "Valid to",
  "header.priority": "Priority",
  "rules.date_basis": "Date basis",
  "rules.calculation.basis": "Calculation basis",
  "rules.calculation.cost_source": "Cost source",
  "rules.customer_scope.mode": "Customer scope",
  "rules.product_scope.mode": "Product scope",
  "rules.spa_reference.required": "SPA reference required",
  "rules.price_check.mode": "Price check",
  "rules.price_check.tolerance": "Price check tolerance",
  "rules.quantity.cap_scope": "Quantity cap",
  "rules.claim_uom": "Claim UoM",
  "rules.rounding.mode": "Rounding mode",
  "rules.rounding.scale": "Rounding scale",
  "rules.rounding.timing": "Rounding timing",
  "rules.adapter": "Adapter",
  "rules.volume_basis": "Volume basis",
  "rules.measurement_period.kind": "Measurement period kind",
  "rules.measurement_period.start": "Measurement period start",
  "rules.measurement_period.end": "Measurement period end",
  "rules.tier_mode": "Tier mode",
  "rules.tier_metric": "Tier metric",
  "rules.spa_lines": "SPA lines",
  "rules.returns": "Returns",
  customers: "Customers",
  items: "Items",
  tiers: "Tiers",
};
const ITEM_LABEL: Record<string, string> = Object.fromEntries(ITEM_FIELDS.map((f) => [f.key, f.label]));
const ROW_LABEL: Record<string, string> = {
  ...ITEM_LABEL,
  customer_code: "Customer code",
  scope: "Scope",
  tier_number: "Tier number",
  lower_bound: "Lower bound",
  rate: "Rate",
};

/** Human label for a validator field or editor path (`payload.items[3].sku` → "Items row 4: SKU"). */
export function pathLabel(field: string): string {
  const path = editorPath(field);
  if (path === "") return "Whole agreement";
  if (LABELS[path]) return LABELS[path];
  const row = /^(customers|items|tiers)\.(\d+)(?:\.(\w+))?$/.exec(path);
  if (row) {
    const [, list = "", index = "0", key] = row;
    const base = `${LABELS[list] ?? list} row ${Number(index) + 1}`;
    return key ? `${base}: ${ROW_LABEL[key] ?? key}` : base;
  }
  return path;
}

export type Section = "header" | "rules" | "customers" | "items" | "tiers" | "other";
export const SECTION_TITLE: Record<Section, string> = {
  header: "Header",
  rules: "Rules",
  customers: "Customers",
  items: "Items",
  tiers: "Tiers",
  other: "Other",
};

export function sectionOf(path: string): Section {
  const head = /^[a-z_]+/.exec(editorPath(path))?.[0] ?? "";
  return (["header", "rules", "customers", "items", "tiers"] as const).find((s) => s === head) ?? "other";
}

/** Validation issues grouped by editor section, in section order. */
export function issuesBySection(issues: Issue[]): [Section, Issue[]][] {
  const order: Section[] = ["other", "header", "rules", "customers", "items", "tiers"];
  return order
    .map((s) => [s, issues.filter((i) => sectionOf(i.field) === s)] as [Section, Issue[]])
    .filter(([, list]) => list.length > 0);
}

// ---- version diff (`GET /agreement-versions/{a}/diff/{b}`) -----------------------------------------------

export interface DiffRow {
  label: string;
  before: string;
  after: string;
  kind: "added" | "removed" | "changed";
}

/** A diff value as text: absent/null is "—", objects as compact JSON. */
export function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

/** Diff path → label: `items[PNL-200A].deviated_cost` → "PNL-200A: Deviated cost". */
export function diffLabel(path: string): string {
  const keyed = /^(customers|items|tiers)\[([^\]]+)\](?:\.(\w+))?$/.exec(path);
  if (keyed) {
    const [, list, key = "", field] = keyed;
    const who = list === "tiers" ? `Tier ${key}` : key;
    return field ? `${who}: ${ROW_LABEL[field] ?? field}` : who;
  }
  return LABELS[path] ?? path;
}

/** Server changes grouped by section, each with display text; order inside a section is the server's. */
export function groupChanges(changes: Change[]): [Section, DiffRow[]][] {
  const order: Section[] = ["header", "rules", "customers", "items", "tiers", "other"];
  const sectionOfDiff = (p: string): Section => {
    const head = /^[a-z_]+/.exec(p)?.[0] ?? "";
    return order.find((s) => s === head && s !== "other") ?? "other";
  };
  return order
    .map((s) => {
      const rows = changes
        .filter((c) => sectionOfDiff(c.path) === s)
        .map(
          (c): DiffRow => ({
            label: diffLabel(c.path),
            before: formatValue(c.before),
            after: formatValue(c.after),
            kind:
              c.before === null || c.before === undefined
                ? "added"
                : c.after === null || c.after === undefined
                  ? "removed"
                  : "changed",
          }),
        );
      return [s, rows] as [Section, DiffRow[]];
    })
    .filter(([, rows]) => rows.length > 0);
}

// ---- virtualised items grid -----------------------------------------------------------------------------

/** Rows [start, end) to render for a scroll position (fixed row height, `overscan` rows each side). */
export function visibleRange(
  scrollTop: number,
  viewport: number,
  rowHeight: number,
  count: number,
  overscan = 5,
): [number, number] {
  const first = Math.floor(Math.max(0, scrollTop) / rowHeight);
  const start = Math.max(0, first - overscan);
  const end = Math.min(count, first + Math.ceil(viewport / rowHeight) + overscan);
  return [Math.min(start, end), end];
}

// ---- display texts --------------------------------------------------------------------------------------

export const DRAFT_STATUS: Record<string, string> = {
  draft: "Draft",
  pending_approval: "Waiting for approval",
  approved: "Approved",
  rejected: "Rejected",
  needs_manual_entry: "Needs manual entry",
  cancelled: "Cancelled",
};

export const VERSION_STATUS: Record<string, string> = {
  active: "Active",
  expired: "Expired",
  superseded: "Superseded",
  cancelled: "Cancelled",
};

export const SOURCE_TEXT: Record<string, string> = {
  manual: "Entered manually",
  spreadsheet_template: "Spreadsheet template",
  ai_extraction: "AI extraction",
};

export const TYPE_TEXT: Record<string, string> = { spa: "SPA", rebate: "Volume rebate" };
