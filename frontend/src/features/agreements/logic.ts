import type { components } from "../../api/schema";

export type AgreementOut = components["schemas"]["AgreementOut"];
export type CancelIn = components["schemas"]["CancelIn"];
export type CustomerRef = components["schemas"]["CustomerRef"];
export type DiffOut = components["schemas"]["DiffOut"];
export type DraftCreate = components["schemas"]["DraftCreate"];
export type DraftOut = components["schemas"]["DraftOut"];
export type DraftSummary = components["schemas"]["DraftSummary"];
export type DraftUpdate = components["schemas"]["DraftUpdate"];
export type Header = components["schemas"]["Header"];
export type Issue = components["schemas"]["Issue"];
export type Item = components["schemas"]["Item"];
export type ItemOut = components["schemas"]["ItemOut"];
export type ItemsImportIn = components["schemas"]["ItemsImportIn"];
export type ItemsImportOut = components["schemas"]["ItemsImportOut"];
export type Payload = components["schemas"]["Payload"];
export type RebateRules = components["schemas"]["RebateRules"];
export type RejectIn = components["schemas"]["RejectIn"];
export type ReturnIn = components["schemas"]["ReturnIn"];
export type Rounding = components["schemas"]["Rounding"];
export type SpaRules = components["schemas"]["SpaRules"];
export type SubmitIn = components["schemas"]["SubmitIn"];
export type TierIn = components["schemas"]["TierIn"];
export type TierOut = components["schemas"]["TierOut"];
export type Validation = components["schemas"]["Validation"];
export type VersionOut = components["schemas"]["VersionOut"];
export type VersionSummary = components["schemas"]["VersionSummary"];

export const ITEMS_PAGE_SIZE = 100;

export const DRAFT_STATUSES = [
  "draft",
  "pending_approval",
  "approved",
  "rejected",
  "needs_manual_entry",
  "cancelled",
] as const;

export const DRAFT_STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  pending_approval: "Pending approval",
  approved: "Approved",
  rejected: "Rejected",
  needs_manual_entry: "Needs manual entry",
  cancelled: "Cancelled",
};

export const VERSION_STATUS_LABELS: Record<string, string> = {
  active: "Active",
  superseded: "Superseded",
  expired: "Expired",
  cancelled: "Cancelled",
};

export function defaultRounding(): Rounding {
  return { mode: "half_up", scale: 2, timing: "per_line" };
}

export function defaultSpaRules(): SpaRules {
  return {
    type: "spa",
    schema_version: 1,
    adapter: null,
    claim_uom: null,
    date_basis: "ship_date",
    calculation: { basis: "price_difference", cost_source: "agreement_reference" },
    customer_scope: { mode: "listed" },
    product_scope: { mode: "listed" },
    price_check: { mode: "flag", tolerance: "0.005" },
    quantity: { cap_scope: "per_item" },
    rounding: defaultRounding(),
    spa_reference: { required: true },
  };
}

export function defaultRebateRules(start = "", end = ""): RebateRules {
  return {
    type: "rebate",
    schema_version: 1,
    measurement_period: { kind: "calendar_year", start, end },
    product_scope: { mode: "all" },
    returns: "reduce_volume",
    rounding: defaultRounding(),
    spa_lines: "include",
    tier_metric: "amount",
    tier_mode: "retroactive",
    volume_basis: "purchases",
  };
}

export function emptyPayload(agreementType: "spa" | "rebate" = "spa"): Payload {
  return {
    payload_schema_version: 1,
    header: {
      agreement_type: agreementType,
      external_number: "",
      manufacturer_code: "",
      name: "",
      priority: 0,
      valid_from: "",
      valid_to: "",
    },
    rules: agreementType === "rebate" ? defaultRebateRules() : defaultSpaRules(),
    customers: [],
    items: [],
    tiers: [],
  };
}

export function emptyItem(): Item {
  return {
    sku: "",
    reference_cost: null,
    deviated_cost: null,
    sell_price: null,
    rate: null,
    flat_amount: null,
    max_quantity: null,
    claim_uom: null,
  };
}

export function emptyCustomer(): CustomerRef {
  return { customer_code: "", scope: "include" };
}

export function emptyTier(tierNumber: number): TierIn {
  return { tier_number: tierNumber, lower_bound: "", rate: "" };
}

function asRecord(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function isObj(v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

/** Server payload exactly as received (cloned); defaults only for a brand-new empty draft. */
export function payloadFromDraft(raw: Record<string, unknown> | null | undefined): Payload {
  if (!isObj(raw) || Object.keys(raw).length === 0) return emptyPayload();
  return structuredClone(raw) as unknown as Payload;
}

function fill(def: unknown, raw: unknown): unknown {
  if (raw === undefined || (raw === null && isObj(def))) return def;
  if (isObj(def) && isObj(raw)) {
    const out: Record<string, unknown> = { ...raw };
    for (const k of Object.keys(def)) out[k] = fill(def[k], raw[k]);
    return out;
  }
  return raw;
}

/** Read-only view for form binding: raw values win, missing keys fall back to defaults. */
export function viewPayload(raw: Payload): Payload {
  const r = asRecord(raw);
  const h = asRecord(r.header);
  const rules = asRecord(r.rules);
  const type: "spa" | "rebate" = h.agreement_type === "rebate" || rules.type === "rebate" ? "rebate" : "spa";
  const def = emptyPayload(type);
  const defRules = type === "rebate" ? defaultRebateRules() : defaultSpaRules();
  const list = (v: unknown, d: unknown) =>
    Array.isArray(v) ? v.map((e) => (isObj(e) ? fill(d, e) : e)) : [];
  return {
    ...r,
    payload_schema_version: r.payload_schema_version ?? 1,
    header: fill(def.header, h),
    rules: fill(defRules, rules),
    customers: list(r.customers, emptyCustomer()),
    items: list(r.items, emptyItem()),
    tiers: list(r.tiers, emptyTier(1)),
  } as unknown as Payload;
}

/** Apply only the user's edits (prev view -> next view) onto the raw payload; untouched values and unknown keys survive. */
export function applyEdit(raw: unknown, prev: unknown, next: unknown): unknown {
  if (JSON.stringify(prev) === JSON.stringify(next)) return raw;
  if (isObj(next) && isObj(prev)) {
    const out: Record<string, unknown> = isObj(raw) ? { ...raw } : {};
    for (const k of Object.keys(next)) out[k] = applyEdit(out[k], prev[k], next[k]);
    return Object.fromEntries(Object.entries(out).filter(([k]) => k in next || !(k in prev)));
  }
  if (Array.isArray(next) && Array.isArray(prev) && Array.isArray(raw) && raw.length === prev.length) {
    if (next.length === prev.length) return next.map((n, i) => applyEdit(raw[i], prev[i], n));
    // one row added or removed: only that row changes; every other row stays exactly as the server sent it
    const same = (x: unknown, y: unknown) => JSON.stringify(x) === JSON.stringify(y);
    if (next.length === prev.length + 1) {
      let k = 0;
      while (k < prev.length && same(prev[k], next[k])) k++;
      if (prev.slice(k).every((p, i) => same(p, next[k + 1 + i])))
        return [...raw.slice(0, k), next[k], ...raw.slice(k)];
    }
    if (next.length === prev.length - 1) {
      let k = 0;
      while (k < next.length && same(prev[k], next[k])) k++;
      if (next.slice(k).every((n, i) => same(n, prev[k + 1 + i])))
        return [...raw.slice(0, k), ...raw.slice(k + 1)];
    }
  }
  return next;
}

export function switchPayloadType(current: Payload, nextType: "spa" | "rebate"): Payload {
  if (current.header.agreement_type === nextType && current.rules.type === nextType) return current;
  const rules =
    nextType === "rebate"
      ? {
          ...defaultRebateRules(current.header.valid_from, current.header.valid_to),
          rounding: current.rules.rounding,
        }
      : { ...defaultSpaRules(), rounding: current.rules.rounding };
  return {
    ...current,
    header: { ...current.header, agreement_type: nextType },
    rules,
    tiers: nextType === "spa" ? [] : current.tiers,
  };
}

export function buildAmendmentPayload(
  version: VersionOut,
  manufacturerCode: string,
  items: ItemOut[],
): Payload {
  const agreementType: "spa" | "rebate" = version.agreement_type === "rebate" ? "rebate" : "spa";
  return {
    payload_schema_version: 1,
    header: {
      agreement_type: agreementType,
      external_number: version.external_number,
      manufacturer_code: manufacturerCode,
      name: version.name,
      priority: version.priority,
      valid_from: version.valid_from,
      valid_to: version.valid_to,
    },
    rules: version.rules as unknown as Payload["rules"],
    customers: version.customers.map((c) => ({
      customer_code: c.customer_code ?? "",
      scope: c.scope === "exclude" ? "exclude" : "include",
    })),
    items: items.map((it) => ({
      sku: it.sku,
      reference_cost: it.reference_cost,
      deviated_cost: it.deviated_cost,
      sell_price: it.sell_price,
      rate: it.rate,
      flat_amount: it.flat_amount,
      max_quantity: it.max_quantity,
      claim_uom: it.claim_uom,
    })),
    tiers: version.tiers.map((t) => ({
      tier_number: t.tier_number,
      lower_bound: t.lower_bound,
      rate: t.rate ?? "",
    })),
  };
}

/**
 * Convert a server validation issue path (e.g. `payload.items[3].sku` or `header.valid_to`)
 * into the DOM input `id` used in the draft editor, or `null` if it does not map to a field.
 */
export function issueFieldId(path: string): string | null {
  const trimmed = path.trim();
  if (!trimmed) return null;
  const withoutPayload = trimmed.startsWith("payload.") ? trimmed.slice("payload.".length) : trimmed;
  if (!withoutPayload || withoutPayload === "payload") return null;
  const normalized = withoutPayload.replace(/\[(\d+)\]/g, ".$1");
  const parts = normalized.split(".").filter(Boolean);
  if (parts.length === 0) return null;
  const section = parts[0];
  if (
    section !== "header" &&
    section !== "rules" &&
    section !== "customers" &&
    section !== "items" &&
    section !== "tiers"
  ) {
    return null;
  }
  return `field-${parts.join("-")}`;
}

const SECTION_LABELS: Record<string, string> = {
  header: "",
  rules: "Rules",
  customers: "Customer",
  items: "Item",
  tiers: "Tier",
};

/** `payload.items[3].sell_price` → "Item 4 · Sell price"; `header.valid_to` → "Valid to". */
export function issueFieldLabel(path: string): string {
  const parts = path
    .trim()
    .replace(/^payload\.?/, "")
    .replace(/\[(\d+)\]/g, ".$1")
    .split(".")
    .filter(Boolean);
  const words = parts.map((p, i) => {
    if (/^\d+$/.test(p)) return `${Number(p) + 1}`;
    if (i === 0 && p in SECTION_LABELS) return SECTION_LABELS[p] ?? p;
    const text = p.replaceAll("_", " ");
    return text.charAt(0).toUpperCase() + text.slice(1);
  });
  // "Item", "4", "Sell price" → "Item 4 · Sell price"
  const out: string[] = [];
  for (const w of words) {
    if (!w) continue;
    if (/^\d+$/.test(w) && out.length) out[out.length - 1] += ` ${w}`;
    else out.push(w);
  }
  return out.join(" · ") || "Agreement";
}

const ISSUE_PATTERNS: [RegExp, (m: RegExpExecArray) => string][] = [
  [/^Field required$/i, () => "This is required."],
  [/^Input should be a valid (?:decimal|number|integer)/i, () => "Enter a number, for example 12.50."],
  [/^Input should be a valid date/i, () => "Enter a date as YYYY-MM-DD."],
  [/^String should have at least 1 character/i, () => "This can't be empty."],
  [/^String should have at most (\d+) characters?/i, (m) => `Keep this to ${m[1]} characters or fewer.`],
  [/^Input should be greater than or equal to (\S+)/i, (m) => `Must be ${m[1]} or more.`],
  [/^Input should be greater than (\S+)/i, (m) => `Must be more than ${m[1]}.`],
  [/^Input should be less than or equal to (\S+)/i, (m) => `Must be ${m[1]} or less.`],
  [/^Input should be less than (\S+)/i, (m) => `Must be less than ${m[1]}.`],
  [
    /^Decimal input should have no more than (\d+) decimal places?/i,
    (m) => `Use at most ${m[1]} decimal places.`,
  ],
  [/^Input should be (.+)$/i, (m) => `Choose one of: ${m[1]?.replaceAll("'", "").replace(/ or /, ", ")}.`],
  [/^Extra inputs are not permitted/i, () => "This field is not used for this kind of agreement."],
  [/^Value error, (.+)$/i, (m) => (m[1] ?? "").charAt(0).toUpperCase() + (m[1] ?? "").slice(1)],
];

/** Server validation messages (often raw Pydantic text) rewritten for an analyst; unknown text passes through. */
export function friendlyIssueMessage(message: string): string {
  for (const [re, fn] of ISSUE_PATTERNS) {
    const m = re.exec(message.trim());
    if (m) return fn(m);
  }
  return message;
}

export function itemIndexFromIssuePath(path: string): number | null {
  const m = /(?:^|\.)items(?:\[(\d+)\]|\.(\d+))(?:\.|$)/.exec(path.trim());
  if (!m) return null;
  const raw = m[1] ?? m[2];
  if (raw === undefined) return null;
  const n = Number(raw);
  return Number.isInteger(n) && n >= 0 ? n : null;
}

export function pageForItemIndex(index: number, pageSize = ITEMS_PAGE_SIZE): number {
  return Math.floor(Math.max(0, index) / pageSize) + 1;
}

export function paginateItems<T>(
  items: T[],
  page: number,
  pageSize = ITEMS_PAGE_SIZE,
): { slice: T[]; startIndex: number; page: number; totalPages: number } {
  const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
  const clamped = Math.min(Math.max(1, page), totalPages);
  const startIndex = (clamped - 1) * pageSize;
  return {
    slice: items.slice(startIndex, startIndex + pageSize),
    startIndex,
    page: clamped,
    totalPages,
  };
}

export function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

export function flattenRulesEntries(
  rules: Record<string, unknown>,
  prefix = "",
): { key: string; value: string }[] {
  const out: { key: string; value: string }[] = [];
  for (const [k, v] of Object.entries(rules)) {
    const label = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object" && !Array.isArray(v)) {
      out.push(...flattenRulesEntries(v as Record<string, unknown>, label));
    } else {
      out.push({ key: label, value: formatValue(v) });
    }
  }
  return out;
}

/** Integer inputs: a whole-number string becomes that integer; anything else is sent as typed, so the
 * server's validation (not the UI) rejects it. Never substitutes a default. */
export function intOrRaw(value: string): number | string {
  return /^-?\d+$/.test(value.trim()) ? Number(value.trim()) : value;
}
