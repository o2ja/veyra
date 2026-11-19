/**
 * Display formatting shared by every page. Works on the server's decimal strings directly:
 * never parses money to a float and never rounds, so no displayed figure can drift from the server.
 */

const DASH = "—";

type Parts = { raw: string } | { sign: string; grouped: string; frac: string };

function split(value: string | number | null | undefined): Parts | null {
  if (value === null || value === undefined || value === "") return null;
  const str = String(value).trim();
  // eslint-disable-next-line security/detect-unsafe-regex -- linear: no overlapping repetition; bounded server decimal
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(str);
  if (!m) return { raw: str };
  const [, sign = "", int = "0", frac = ""] = m;
  // eslint-disable-next-line security/detect-unsafe-regex -- thousands grouping on a bounded digit string
  const grouped = int.replace(/^0+(?=\d)/, "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return { sign, grouped, frac };
}

/**
 * `$3,412.50`. At least 2 decimals; extra digits are kept only when non-zero (unit rates like `$0.1234`).
 * A non-USD currency is shown as a suffix instead of `$` (`1,200.00 EUR`).
 */
export function formatMoney(value: string | number | null | undefined, currency = "USD"): string {
  const p = split(value);
  if (!p) return DASH;
  if ("raw" in p) return p.raw;
  const frac = p.frac.replace(/0+$/, "").padEnd(2, "0");
  const negative = p.sign === "-" && /[1-9]/.test(p.grouped + frac);
  const sign = negative ? "-" : "";
  return currency === "USD" ? `${sign}$${p.grouped}.${frac}` : `${sign}${p.grouped}.${frac} ${currency}`;
}

/** `60`, `2.5`, `1,200`: trailing zeros trimmed. */
export function formatQty(value: string | number | null | undefined): string {
  const p = split(value);
  if (!p) return DASH;
  if ("raw" in p) return p.raw;
  const frac = p.frac.replace(/0+$/, "");
  const negative = p.sign === "-" && /[1-9]/.test(p.grouped + frac);
  return `${negative ? "-" : ""}${p.grouped}${frac ? `.${frac}` : ""}`;
}

const DATE = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" });
const DATE_UTC = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  timeZone: "UTC",
});
const DATE_TIME = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

/** `Nov 18, 2026`. Business dates (`2026-11-18`) are shown as-is, never shifted by the viewer's time zone. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return DASH;
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const d = new Date(`${value}T00:00:00Z`);
    return Number.isNaN(d.getTime()) ? value : DATE_UTC.format(d);
  }
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : DATE.format(d);
}

/** `Nov 18, 2026, 10:50 PM` in the viewer's local time. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return DASH;
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return formatDate(value);
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : DATE_TIME.format(d);
}
