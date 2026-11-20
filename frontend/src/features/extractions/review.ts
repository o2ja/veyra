import { formatDate, formatMoney } from "../../lib/format";
import { friendlyIssueMessage, issueFieldLabel, type Validation } from "../agreements/logic";
import type { components } from "../../api/schema";
import type { ExtractionFieldView, ExtractionOut, ExtractionResult } from "./logic";

type Json = Record<string, unknown>;
export type DraftPreview = components["schemas"]["DraftPreviewOut"];
export type Input =
  | { kind: "select"; options: [value: string, label: string][] }
  | { kind: "date" | "text" | "number" | "percent" };

export type ReviewItem = {
  id: string;
  label: string;
  path: string | null;
  quote: string | null;
  page: number | null;
  value: unknown;
  display: string;
  input: Input | null;
  why: string;
  needs: boolean; // below high confidence, missing, or flagged by the checks
  blocking: boolean; // the draft cannot be submitted while this stays as it is
  optional: boolean; // an empty value is a legitimate answer once confirmed
  warningIds: string[];
};

export type Review = {
  needs: ReviewItem[];
  found: ReviewItem[];
  conflicts: number;
  customers: { matched: number; total: number };
  skus: { matched: number; total: number };
};

// ---- draft payload paths ("items[2].sku") --------------------------------------------------------------

const tokens = (path: string) => path.split(/\.|\[(\d+)\]/).filter(Boolean);
const isObj = (v: unknown): v is Json => v !== null && typeof v === "object" && !Array.isArray(v);

export function getAt(root: unknown, path: string): unknown {
  let node = root;
  for (const t of tokens(path)) {
    if (Array.isArray(node)) node = node[Number(t)];
    else if (isObj(node)) node = node[t];
    else return undefined;
  }
  return node;
}

/** A copy of ``root`` with ``value`` at ``path``; the original is untouched. */
export function setAt(root: unknown, path: string, value: unknown): Json {
  const t = tokens(path);
  const walk = (node: unknown, i: number): unknown => {
    const key = t[i] as string;
    const base: unknown = Array.isArray(node)
      ? [...node]
      : isObj(node)
        ? { ...node }
        : /^\d+$/.test(key)
          ? []
          : {};
    const child = i === t.length - 1 ? value : walk(getAt(node, key), i + 1);
    if (Array.isArray(base)) base[Number(key)] = child;
    else (base as Json)[key] = child;
    return base;
  };
  return walk(root, 0) as Json;
}

const normPath = (p: string) =>
  p
    .trim()
    .replace(/^payload\.?/, "")
    .replace(/\.(\d+)(?=\.|$)/g, "[$1]");

const related = (a: string, b: string) => a === b || a.startsWith(`${b}.`) || b.startsWith(`${a}.`);

// ---- plain English ---------------------------------------------------------------------------------------

const OPT = {
  date_basis: [
    ["ship_date", "Ship date"],
    ["invoice_date", "Invoice date"],
  ],
  calculation: [
    ["price_difference", "Price difference"],
    ["percent_of_sell", "Percent of sell price"],
    ["percent_of_cost", "Percent of cost"],
    ["flat_per_unit", "Flat amount per unit"],
    ["guaranteed_margin", "Guaranteed margin"],
  ],
  cost_source: [
    ["agreement_reference", "Agreement reference cost"],
    ["line_cost", "Cost on the sales line"],
    ["replacement_cost", "Replacement cost"],
  ],
} satisfies Record<string, [string, string][]>;

const FIELDS: Record<string, { label: string; input: Input | null; optional?: boolean }> = {
  "header.agreement_type": {
    label: "Agreement type",
    input: {
      kind: "select",
      options: [
        ["spa", "SPA (ship and debit)"],
        ["rebate", "Volume rebate"],
      ],
    },
  },
  "header.external_number": { label: "Agreement number", input: { kind: "text" } },
  "header.manufacturer_code": { label: "Manufacturer", input: { kind: "text" } },
  "header.valid_from": { label: "Start date", input: { kind: "date" } },
  "header.valid_to": { label: "End date", input: { kind: "date" } },
  "rules.date_basis": {
    label: "Which date decides eligibility",
    input: { kind: "select", options: OPT.date_basis },
  },
  "rules.calculation.basis": {
    label: "How the claim is calculated",
    input: { kind: "select", options: OPT.calculation },
  },
  "rules.calculation.cost_source": {
    label: "Where the cost comes from",
    input: { kind: "select", options: OPT.cost_source },
  },
  "rules.claim_uom": { label: "Claim unit", input: { kind: "text" }, optional: true },
  "rules.customer_scope.mode": {
    label: "Which customers it covers",
    input: {
      kind: "select",
      options: [
        ["listed", "Only the listed customers"],
        ["all", "All customers"],
      ],
    },
  },
  "rules.spa_reference.required": {
    label: "SPA reference on claims",
    input: {
      kind: "select",
      options: [
        ["true", "Required"],
        ["false", "Not required"],
      ],
    },
  },
  "rules.rounding.mode": {
    label: "Rounding mode",
    input: {
      kind: "select",
      options: [
        ["half_up", "Half up"],
        ["half_even", "Half even"],
        ["down", "Round down"],
      ],
    },
  },
  "rules.rounding.scale": { label: "Decimal places", input: { kind: "number" } },
  "rules.rounding.timing": {
    label: "Rounding applied",
    input: {
      kind: "select",
      options: [
        ["per_line", "On each line"],
        ["per_document", "On the document total"],
      ],
    },
  },
  "rules.volume_basis": {
    label: "Volume counted from",
    input: {
      kind: "select",
      options: [
        ["purchases", "Purchases"],
        ["sales", "Sales"],
      ],
    },
  },
  "rules.tier_mode": {
    label: "How tiers apply",
    input: {
      kind: "select",
      options: [
        ["retroactive", "Retroactive to the first dollar"],
        ["incremental", "Only above each threshold"],
      ],
    },
  },
  "rules.spa_lines": {
    label: "SPA sales in rebate volume",
    input: {
      kind: "select",
      options: [
        ["include", "Include"],
        ["qualify_only", "Count toward the tier only"],
        ["exclude", "Exclude"],
      ],
    },
  },
  "rules.measurement_period.kind": { label: "Measurement period", input: { kind: "text" } },
  "rules.measurement_period.start": { label: "Measurement period start", input: { kind: "date" } },
  "rules.measurement_period.end": { label: "Measurement period end", input: { kind: "date" } },
  "customers[].customer_code": { label: "Customer", input: { kind: "text" } },
  "customers[].scope": {
    label: "Included or excluded",
    input: {
      kind: "select",
      options: [
        ["include", "Included"],
        ["exclude", "Excluded"],
      ],
    },
  },
  "items[].sku": { label: "Part number", input: { kind: "text" } },
  "items[].reference_cost": { label: "Reference cost", input: { kind: "text" } },
  "items[].deviated_cost": { label: "Deviated cost", input: { kind: "text" } },
  "items[].sell_price": { label: "Sell price", input: { kind: "text" } },
  "items[].rate": { label: "Rate", input: { kind: "percent" } },
  "items[].flat_amount": { label: "Flat amount", input: { kind: "text" } },
  "items[].max_quantity": { label: "Maximum quantity", input: { kind: "text" } },
  "items[].claim_uom": { label: "Claim unit", input: { kind: "text" }, optional: true },
  "tiers[].lower_bound": { label: "Starts at", input: { kind: "text" } },
  "tiers[].rate": { label: "Rate", input: { kind: "percent" } },
};

const MONEY = new Set([
  "items[].reference_cost",
  "items[].deviated_cost",
  "items[].sell_price",
  "items[].flat_amount",
  "tiers[].lower_bound",
]);
const ROW = { items: "Item", customers: "Customer", tiers: "Tier" } as const;
const ROW_ID = { items: "sku", customers: "customer_code" } as const;

const generic = (path: string) => path.replace(/\[\d+\]/g, "[]");

export function labelFor(path: string, payload: unknown): string {
  const known = FIELDS[generic(path)];
  const row = /^(items|customers|tiers)\[(\d+)\]\.(.+)$/.exec(path);
  if (!row) return known?.label ?? issueFieldLabel(path);
  const kind = row[1] as keyof typeof ROW;
  const id = kind === "tiers" ? undefined : getAt(payload, `${kind}[${row[2]}].${ROW_ID[kind]}`);
  const who = typeof id === "string" && id ? ` (${id})` : "";
  return `${ROW[kind]} ${Number(row[2]) + 1}${who} · ${known?.label ?? issueFieldLabel(row[3] as string)}`;
}

const REASONS: Record<string, string> = {
  not_stated: "The document doesn't state this.",
  passes_disagree: "The two AI readings didn't match.",
  ocr_text: "Read from a scanned page. Check it against the quote.",
  ambiguous_date: "This date could be read more than one way.",
  derived_from_context: "Inferred from nearby text, not stated directly.",
  validator_warning: "The agreement checks flagged this value.",
};
const CONFIDENCE: Record<string, string> = {
  unsupported: "The AI's value couldn't be confirmed in the document.",
  conflict: "The two AI readings conflict.",
  low: "The AI wasn't sure about this.",
  medium: "Please check this against the quote.",
};
const humanise = (code: string) => {
  const s = code.replaceAll("_", " ");
  return s.charAt(0).toUpperCase() + s.slice(1) + ".";
};

const isDecimal = (s: string) => {
  const [whole = "", frac, extra] = s.split(".");
  return extra === undefined && /^\d+$/.test(whole) && (frac === undefined || /^\d+$/.test(frac));
};

/** "0.025" -> "2.5%". Decimal-string arithmetic only; nothing is parsed to a float. */
export function fractionToPercent(s: string): string {
  if (!isDecimal(s)) return s;
  const [i = "0", f = ""] = s.split(".");
  const frac = f.padEnd(2, "0");
  const whole = (i + frac.slice(0, 2)).replace(/^0+(?=\d)/, "");
  const rest = frac.slice(2).replace(/0+$/, "");
  return `${whole}${rest ? `.${rest}` : ""}%`;
}

/** "2.5" or "2.5%" -> "0.025"; ``null`` when it is not a plain non-negative number. */
export function percentToFraction(s: string): string | null {
  const t = s.trim().replace(/%$/, "").trim();
  if (!isDecimal(t)) return null;
  const [i = "0", f = ""] = t.split(".");
  const digits = i.padStart(3, "0") + f;
  const point = digits.length - f.length - 2;
  const out = `${digits.slice(0, point).replace(/^0+(?=\d)/, "")}.${digits.slice(point)}`.replace(
    /\.?0+$/,
    "",
  );
  return out === "" ? "0" : out.endsWith(".") ? out.slice(0, -1) : out;
}

export function displayValue(path: string, value: unknown): string {
  if (value === undefined || value === null || value === "") return "—";
  const g = generic(path);
  const input = FIELDS[g]?.input;
  if (input?.kind === "select") {
    return input.options.find(([v]) => v === String(value))?.[1] ?? String(value);
  }
  if (input?.kind === "date") return formatDate(String(value));
  if (input?.kind === "percent") return fractionToPercent(String(value));
  if (MONEY.has(g)) return formatMoney(String(value));
  return String(value);
}

/** What the input shows for the stored value (percent as typed, booleans as their option value). */
export function editText(path: string, value: unknown): string {
  if (value === undefined || value === null) return "";
  return FIELDS[generic(path)]?.input?.kind === "percent"
    ? fractionToPercent(String(value)).replace(/%$/, "")
    : String(value);
}

/** The value to store for what the user entered; ``undefined`` when it cannot be stored as it is. */
export function parseEdit(path: string, text: string): unknown {
  const t = text.trim();
  const g = generic(path);
  if (t === "") return FIELDS[g]?.optional ? null : undefined;
  const kind = FIELDS[g]?.input?.kind;
  if (kind === "percent") return percentToFraction(t) ?? undefined;
  if (kind === "number") return /^\d+$/.test(t) ? Number(t) : undefined;
  if (g === "rules.spa_reference.required") return t === "true";
  return t;
}

// ---- classification --------------------------------------------------------------------------------------

const SUB: Record<string, string[]> = {
  "rules.rounding": ["mode", "scale", "timing"],
  "rules.measurement_period": ["kind", "start", "end"],
};
const CONFLICT_CODES = new Set(["version_overlap", "agreement_exists"]);

const present = (v: unknown) => v !== undefined && v !== null && v !== "";

export function buildReview(
  result: ExtractionResult,
  payload: Json,
  validation: Validation | null | undefined,
): Review {
  const errors = (validation?.errors ?? []).map((e) => ({ ...e, path: normPath(e.field), error: true }));
  const warnings = (validation?.warnings ?? []).map((e) => ({ ...e, path: normPath(e.field), error: false }));
  const issues = [...errors, ...warnings];
  const items: ReviewItem[] = [];
  const covered = new Set<string>();

  const make = (
    path: string | null,
    field: ExtractionFieldView | null,
    fallbackLabel: string,
  ): ReviewItem => {
    const value = path ? getAt(payload, path) : field?.value;
    const mine = path ? issues.filter((i) => related(i.path, path)) : [];
    const meta = path ? FIELDS[generic(path)] : undefined;
    const optional = meta?.optional === true;
    const missing = !present(value) && !optional;
    const confidence = field?.confidence;
    const hasError = mine.some((i) => i.error);
    const needs =
      missing ||
      hasError ||
      mine.length > 0 ||
      (field !== null && (confidence !== "high" || !field.evidence));
    const reasons = (field?.reasons ?? []).map((r) => REASONS[r] ?? humanise(r));
    const checks = mine
      .map((i) => friendlyIssueMessage(i.message))
      .filter((m) => m !== "This is required." || !reasons.length);
    const why = needs
      ? [...reasons, ...checks].join(" ") ||
        (missing
          ? "Not found in the document. Choose a value."
          : (CONFIDENCE[confidence ?? ""] ?? "Please confirm."))
      : "";
    return {
      id: path ?? `field:${field?.key ?? fallbackLabel}`,
      label: path ? labelFor(path, payload) : fallbackLabel,
      path,
      quote: field?.evidence?.quote ?? null,
      page: field?.evidence?.page ?? null,
      value,
      display: path ? displayValue(path, value) : String(field?.value ?? "—"),
      input: meta?.input ?? null,
      why,
      needs,
      blocking: path !== null && (hasError || missing), // no draft path: nothing to enter, confirming is all there is
      optional,
      warningIds: mine.filter((i) => !i.error && i.id).map((i) => i.id as string),
    };
  };

  for (const f of result.fields) {
    const base = f.payload_path ?? null;
    const paths = base ? (SUB[base]?.map((s) => `${base}.${s}`) ?? [base]) : [null];
    for (const p of paths) {
      if (p) covered.add(p);
      items.push(make(p, f, f.key));
    }
  }
  const seen = new Set<string>();
  for (const i of issues) {
    if (seen.has(i.path) || [...covered].some((c) => related(i.path, c))) continue;
    seen.add(i.path);
    items.push(make(i.path || null, null, i.path || "Agreement"));
  }

  const rows = (section: "customers" | "items", field: string) => {
    const list = getAt(payload, section);
    const bad = new Set(
      errors
        .filter(
          (e) => e.code === "unresolved" && e.path.startsWith(`${section}[`) && e.path.endsWith(`.${field}`),
        )
        .map((e) => e.path),
    );
    const total = Array.isArray(list) ? list.length : 0;
    return { matched: Math.max(0, total - bad.size), total };
  };

  return {
    needs: items.filter((i) => i.needs),
    found: items.filter((i) => !i.needs),
    conflicts: errors.filter((e) => CONFLICT_CODES.has(e.code)).length,
    customers: rows("customers", "customer_code"),
    skus: rows("items", "sku"),
  };
}

/** Still waiting on the user: needs a decision and has not been confirmed (or is still invalid). */
export const isPending = (i: ReviewItem, confirmed: ReadonlySet<string>) =>
  i.needs && (i.blocking || !confirmed.has(i.id));

export const pendingCount = (r: Review, confirmed: ReadonlySet<string>) =>
  r.needs.filter((i) => isPending(i, confirmed)).length;

// ---- sentences (templates over data the app already has) -----------------------------------------------------

export function matchSentence(r: Review): string | null {
  const parts = [
    r.customers.total > 0 ? `${r.customers.matched}/${r.customers.total} customers` : null,
    r.skus.total > 0 ? `${r.skus.matched}/${r.skus.total} SKUs` : null,
  ].filter(Boolean);
  return parts.length ? `Matched to your data: ${parts.join(", ")}` : null;
}

export function previewSentence(p: DraftPreview): string {
  if (p.kind === "rebate") {
    return p.accrual
      ? `If approved: est. ${formatMoney(p.accrual.cumulative_earned)} earned so far from your imported purchases`
      : "If approved: no imported purchases fall in this agreement yet";
  }
  const eligible = Number(p.totals.eligible ?? 0);
  if (p.sample_size === 0) return "If approved: no imported sales lines match this agreement yet";
  const amount = formatMoney(String(p.totals.eligible_amount ?? "0"));
  const lines = `${eligible} imported sales ${eligible === 1 ? "line qualifies" : "lines qualify"}`;
  return `If approved: ${lines}, est. ${amount}`;
}

export function readLabel(ex: Pick<ExtractionOut, "status" | "needs_count" | "draft_status">): string | null {
  if (ex.status !== "completed") return null;
  if (ex.draft_status === "pending_approval") return "Submitted for approval";
  if (ex.draft_status === "approved") return "Approved";
  if (ex.needs_count === null || ex.needs_count === undefined) return "Read";
  return ex.needs_count > 0 ? "Read: needs your review" : "Ready to submit";
}

export function countsText(ex: Pick<ExtractionOut, "found_count" | "needs_count">): string {
  if (ex.found_count === null || ex.found_count === undefined) return "—";
  return `${ex.found_count} found with a quote · ${ex.needs_count ?? 0} need your decision`;
}
