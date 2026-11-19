import { useQuery } from "@tanstack/react-query";
import {
  Children,
  type ReactElement,
  type ReactNode,
  type SelectHTMLAttributes,
  useId,
  useState,
} from "react";
import { NavLink } from "react-router-dom";

import { Button, Empty, ErrorAlert, Loading } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";

// Shared pieces for the master-data, import and line-browser screens (S3).

/** Keyset pagination (API_SPECIFICATION.md §1.2): a stack of cursors, so "Previous" needs no offsets. */
export interface Pager {
  stack: (string | null)[];
}
export const firstPage = (): Pager => ({ stack: [null] });
export const pageCursor = (p: Pager): string | null => p.stack[p.stack.length - 1] ?? null;
export const nextPage = (p: Pager, cursor: string | null | undefined): Pager =>
  cursor ? { stack: [...p.stack, cursor] } : p;
export const prevPage = (p: Pager): Pager => (p.stack.length > 1 ? { stack: p.stack.slice(0, -1) } : p);

export function usePager() {
  const [pager, setPager] = useState(firstPage);
  return {
    cursor: pageCursor(pager),
    page: pager.stack.length,
    reset: () => setPager(firstPage()),
    next: (cursor: string | null | undefined) => setPager((p) => nextPage(p, cursor)),
    prev: () => setPager(prevPage),
  };
}

export function PagerControls({
  pager,
  page,
}: {
  pager: ReturnType<typeof usePager>;
  page: { has_more: boolean; next_cursor: string | null } | undefined;
}) {
  if (pager.page === 1 && !page?.has_more) return null;
  return (
    <nav aria-label="Pages" className="mt-4 flex items-center gap-3 text-sm">
      <Button variant="secondary" disabled={pager.page === 1} onClick={pager.prev}>
        Previous
      </Button>
      <span>Page {pager.page}</span>
      <Button variant="secondary" disabled={!page?.has_more} onClick={() => pager.next(page?.next_cursor)}>
        Next
      </Button>
    </nav>
  );
}

export interface Column<T> {
  header: string;
  cell: (row: T) => ReactNode;
  srOnly?: boolean;
}

/** A plain data table with loading, error and empty states. */
export function DataTable<T extends { id: string }>({
  caption,
  columns,
  rows,
  loading,
  error,
  empty,
}: {
  caption: string;
  columns: Column<T>[];
  rows: T[] | undefined;
  loading: boolean;
  error: unknown;
  empty: string;
}) {
  if (error) return <ErrorAlert error={error as ApiError} />;
  if (loading) return <Loading />;
  if (!rows?.length) return <Empty>{empty}</Empty>;
  return (
    <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={caption}>
      <table className="w-full text-left text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="text-muted">
          <tr>
            {columns.map((c) => (
              <th key={c.header} scope="col" className="py-2 pr-4 font-medium">
                {c.srOnly ? <span className="sr-only">{c.header}</span> : c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-line align-top">
              {columns.map((c) => (
                <td key={c.header} className="py-2 pr-4">
                  {c.cell(r)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SelectField({
  label,
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & { label: string; children: ReactNode }) {
  const generated = useId();
  const id = props.id ?? generated; // a caller's id must also be the label's target
  const known = Children.toArray(children).map((c) =>
    String((c as ReactElement<{ value?: unknown }>).props?.value),
  );
  const v = props.value;
  const odd = v !== undefined && v !== null && String(v) !== "" && !known.includes(String(v));
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <select className="rounded-md border border-line bg-panel px-3 py-2 text-sm" {...props} id={id}>
        {odd && <option value={String(v)}>{String(v)}</option>}
        {children}
      </select>
      {odd && <p className="text-xs text-danger">Unsupported value &quot;{String(v)}&quot; — fix or keep.</p>}
    </div>
  );
}

export function Checkbox({
  label,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      {label}
    </label>
  );
}

/** Empty optional text fields are sent as null (the server treats null as "not set"). */
export const orNull = (v: string): string | null => (v.trim() === "" ? null : v.trim());

/** Manufacturers for selects; tenants have a handful, the first 200 cover them. */
export function useManufacturers() {
  return useQuery({
    queryKey: ["manufacturers", "all"],
    queryFn: () => unwrap(api.GET("/api/v1/manufacturers", { params: { query: { limit: 200 } } })),
  });
}

export function ManufacturerSelect({
  label = "Manufacturer",
  value,
  onChange,
  allowAny,
  required,
}: {
  label?: string;
  value: string;
  onChange: (id: string) => void;
  allowAny?: string;
  required?: boolean;
}) {
  const list = useManufacturers();
  return (
    <SelectField label={label} value={value} required={required} onChange={(e) => onChange(e.target.value)}>
      <option value="">{allowAny ?? "Choose…"}</option>
      {list.data?.data.map((m) => (
        <option key={m.id} value={m.id}>
          {m.name} ({m.code})
        </option>
      ))}
    </SelectField>
  );
}

/** Pick an agreement by number and name instead of pasting its id. */
export function AgreementSelect({
  label = "Agreement",
  value,
  onChange,
  allowAny = "All agreements",
}: {
  label?: string;
  value: string;
  onChange: (id: string) => void;
  allowAny?: string;
}) {
  // shortcut: first 200 agreements only; switch to a search picker if a tenant outgrows that.
  const list = useQuery({
    queryKey: ["agreements", "picker"],
    queryFn: () => unwrap(api.GET("/api/v1/agreements", { params: { query: { limit: 200 } } })),
  });
  return (
    <SelectField label={label} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{allowAny}</option>
      {list.data?.data.map((a) => (
        <option key={a.id} value={a.id}>
          {a.external_number} · {a.name}
        </option>
      ))}
    </SelectField>
  );
}

/** Search a customer or product by name/code/SKU and pick one (value is the id). */
export function EntityPicker({
  kind,
  label,
  value,
  onChange,
}: {
  kind: "customer" | "product";
  label: string;
  value: string;
  onChange: (id: string) => void;
}) {
  const [q, setQ] = useState("");
  const id = useId();
  const found = useQuery({
    queryKey: [kind, "search", q],
    queryFn: async () => {
      const query = { q: q || null, limit: 25 };
      if (kind === "customer") {
        const r = await unwrap(api.GET("/api/v1/customers", { params: { query } }));
        return r.data.map((c) => ({ id: c.id, text: `${c.name}${c.code ? ` (${c.code})` : ""}` }));
      }
      const r = await unwrap(api.GET("/api/v1/products", { params: { query } }));
      return r.data.map((p) => ({ id: p.id, text: `${p.sku}${p.description ? ` · ${p.description}` : ""}` }));
    },
  });
  return (
    <fieldset className="flex flex-col gap-1">
      <legend className="text-sm font-medium">{label}</legend>
      <div className="flex flex-col gap-2 sm:flex-row">
        <label htmlFor={`${id}-q`} className="sr-only">
          Search {kind === "customer" ? "customers" : "products"}
        </label>
        <input
          id={`${id}-q`}
          type="search"
          placeholder={kind === "customer" ? "Search name or code" : "Search SKU or description"}
          className="rounded-md border border-line bg-panel px-3 py-2 text-sm"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <label htmlFor={`${id}-pick`} className="sr-only">
          {label}
        </label>
        <select
          id={`${id}-pick`}
          className="min-w-56 rounded-md border border-line bg-panel px-3 py-2 text-sm"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">{found.isPending ? "Loading…" : "Choose…"}</option>
          {found.data?.map((o) => (
            <option key={o.id} value={o.id}>
              {o.text}
            </option>
          ))}
        </select>
      </div>
    </fieldset>
  );
}

const SECTIONS = [
  { to: "/master-data/manufacturers", label: "Manufacturers", permission: "data.read" },
  { to: "/master-data/customers", label: "Customers", permission: "data.read" },
  { to: "/master-data/products", label: "Products", permission: "data.read" },
  { to: "/master-data/uom", label: "UoM conversions", permission: "data.read" },
  { to: "/master-data/branches", label: "Branches", permission: "data.read" },
  { to: "/master-data/unmapped", label: "Unmapped queue", permission: "masterdata.write" },
] as const;

export function MasterDataNav() {
  const { can } = useSession();
  return (
    <nav
      aria-label="Master data"
      className="mb-6 flex w-fit max-w-full flex-wrap gap-1 rounded-xl border border-line bg-panel p-1 text-sm shadow-card"
    >
      {SECTIONS.filter((s) => can(s.permission)).map((s) => (
        <NavLink
          key={s.to}
          to={s.to}
          className={({ isActive }) =>
            isActive
              ? "rounded-lg bg-brand px-3 py-1.5 font-medium text-panel"
              : "rounded-lg px-3 py-1.5 text-muted hover:bg-brand-soft hover:text-ink"
          }
        >
          {s.label}
        </NavLink>
      ))}
    </nav>
  );
}

export function FormGrid({ children }: { children: ReactNode }) {
  return <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{children}</div>;
}
