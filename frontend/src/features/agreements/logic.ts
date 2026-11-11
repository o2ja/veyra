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

function asStr(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}

function asNullableStr(v: unknown): string | null {
  if (v === null || v === undefined) return null;
  return typeof v === "string" ? v : String(v);
}

function asNum(v: unknown, fallback = 0): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

function normalizeRounding(raw: unknown): Rounding {
  const r = asRecord(raw);
  const mode = r.mode === "half_even" || r.mode === "down" ? r.mode : "half_up";
  const timing = r.timing === "per_document" ? "per_document" : "per_line";
  return { mode, scale: asNum(r.scale, 2), timing };
}

export function normalizeRules(raw: unknown, agreementType: "spa" | "rebate"): SpaRules | RebateRules {
  const r = asRecord(raw);
  const type = r.type === "rebate" || r.type === "spa" ? r.type : agreementType;
  if (type === "rebate") {
    const def = defaultRebateRules();
    const mp = asRecord(r.measurement_period);
    const ps = asRecord(r.product_scope);
    return {
      ...def,
      measurement_period: {
        kind: asStr(mp.kind, def.measurement_period.kind),
        start: asStr(mp.start, def.measurement_period.start),
        end: asStr(mp.end, def.measurement_period.end),
      },
      product_scope: { mode: ps.mode === "listed" ? "listed" : "all" },
      returns: "reduce_volume",
      rounding: normalizeRounding(r.rounding),
      spa_lines: r.spa_lines === "qualify_only" || r.spa_lines === "exclude" ? r.spa_lines : "include",
      tier_metric: r.tier_metric === "quantity" ? "quantity" : "amount",
      tier_mode: r.tier_mode === "incremental" ? "incremental" : "retroactive",
      volume_basis: r.volume_basis === "sales" ? "sales" : "purchases",
    };
  }
  const def = defaultSpaRules();
  const calc = asRecord(r.calculation);
  const cs = asRecord(r.customer_scope);
  const pc = asRecord(r.price_check);
  const qty = asRecord(r.quantity);
  const sr = asRecord(r.spa_reference);
  const basis =
    calc.basis === "percent_of_sell" ||
    calc.basis === "percent_of_cost" ||
    calc.basis === "flat_per_unit" ||
    calc.basis === "guaranteed_margin"
      ? calc.basis
      : "price_difference";
  const costSource =
    calc.cost_source === "line_cost" || calc.cost_source === "replacement_cost"
      ? calc.cost_source
      : "agreement_reference";
  return {
    ...def,
    adapter: asNullableStr(r.adapter),
    claim_uom: asNullableStr(r.claim_uom),
    date_basis: r.date_basis === "invoice_date" ? "invoice_date" : "ship_date",
    calculation: { basis, cost_source: costSource },
    customer_scope: { mode: cs.mode === "all" ? "all" : "listed" },
    product_scope: { mode: "listed" },
    price_check: {
      mode: pc.mode === "ignore" ? "ignore" : "flag",
      tolerance: asStr(pc.tolerance, def.price_check.tolerance),
    },
    quantity: {
      cap_scope: qty.cap_scope === "none" || qty.cap_scope === "per_agreement" ? qty.cap_scope : "per_item",
    },
    rounding: normalizeRounding(r.rounding),
    spa_reference: { required: typeof sr.required === "boolean" ? sr.required : true },
  };
}

export function payloadFromDraft(raw: Record<string, unknown> | null | undefined): Payload {
  const root = asRecord(raw);
  const h = asRecord(root.header);
  const rulesRaw = asRecord(root.rules);
  const agreementType: "spa" | "rebate" =
    h.agreement_type === "rebate" || rulesRaw.type === "rebate" ? "rebate" : "spa";

  const customers = Array.isArray(root.customers)
    ? root.customers.map((c) => {
        const cr = asRecord(c);
        return {
          customer_code: asStr(cr.customer_code),
          scope: cr.scope === "exclude" ? ("exclude" as const) : ("include" as const),
        };
      })
    : [];

  const items = Array.isArray(root.items)
    ? root.items.map((it) => {
        const ir = asRecord(it);
        return {
          sku: asStr(ir.sku),
          reference_cost: asNullableStr(ir.reference_cost),
          deviated_cost: asNullableStr(ir.deviated_cost),
          sell_price: asNullableStr(ir.sell_price),
          rate: asNullableStr(ir.rate),
          flat_amount: asNullableStr(ir.flat_amount),
          max_quantity: asNullableStr(ir.max_quantity),
          claim_uom: asNullableStr(ir.claim_uom),
        };
      })
    : [];

  const tiers = Array.isArray(root.tiers)
    ? root.tiers.map((t, idx) => {
        const tr = asRecord(t);
        return {
          tier_number: asNum(tr.tier_number, idx + 1),
          lower_bound: asStr(tr.lower_bound),
          rate: asStr(tr.rate),
        };
      })
    : [];

  return {
    payload_schema_version: 1,
    header: {
      agreement_type: agreementType,
      external_number: asStr(h.external_number),
      manufacturer_code: asStr(h.manufacturer_code),
      name: asStr(h.name),
      priority: asNum(h.priority, 0),
      valid_from: asStr(h.valid_from),
      valid_to: asStr(h.valid_to),
    },
    rules: normalizeRules(rulesRaw, agreementType),
    customers,
    items,
    tiers,
  };
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
    rules: normalizeRules(version.rules, agreementType),
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
