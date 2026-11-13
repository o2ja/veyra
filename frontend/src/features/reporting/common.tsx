import { useQuery } from "@tanstack/react-query";
import { NavLink } from "react-router-dom";

import { Alert, ErrorAlert } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useManufacturers } from "../masterdata/common";
import { formatFields, type TraceNode } from "./logic";

const NAV_ITEMS = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/reports/audit-pack", label: "Audit pack" },
  { to: "/calculations/rebates", label: "Rebate tier tracker" },
] as const;

export function ReportingNav() {
  return (
    <nav aria-label="Reporting navigation" className="mb-6 flex gap-4 border-b border-line pb-3 text-sm">
      {NAV_ITEMS.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === "/dashboard"}
          className={({ isActive }) => (isActive ? "font-semibold text-ink" : "text-muted hover:text-ink")}
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}

export function useManufacturerMap(): Map<string, string> {
  const query = useManufacturers();
  const map = new Map<string, string>();
  for (const m of query.data?.data ?? []) {
    map.set(m.id, `${m.name} (${m.code})`);
  }
  return map;
}

export function useBranchMap(): Map<string, string> {
  const query = useQuery({
    queryKey: ["branches"],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/branches", {
          params: { query: { limit: 100 } },
        }),
      ),
  });

  const map = new Map<string, string>();
  for (const b of query.data?.data ?? []) {
    map.set(b.id, `${b.name} (${b.code})`);
  }
  return map;
}

export function BranchSelect({
  value,
  onChange,
  disabled = false,
}: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const query = useQuery({
    queryKey: ["branches"],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/branches", {
          params: { query: { limit: 100 } },
        }),
      ),
  });

  return (
    <div className="flex flex-col gap-1 text-sm">
      <label htmlFor="branch-filter" className="font-medium text-xs text-muted">
        Branch
      </label>
      <select
        id="branch-filter"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className="rounded border border-line bg-surface p-2 text-sm"
      >
        <option value="">All branches</option>
        {(query.data?.data ?? []).map((b) => (
          <option key={b.id} value={b.id}>
            {b.name} ({b.code})
          </option>
        ))}
      </select>
    </div>
  );
}

export function TraceNodeCard({ node }: { node: TraceNode }) {
  const fields = formatFields(node.fields);

  return (
    <div className="rounded-lg border border-line bg-panel p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line pb-2">
        <h3 className="font-semibold text-sm text-ink">{node.label}</h3>
        <span className="rounded bg-surface px-2 py-0.5 font-mono text-xs text-muted">{node.kind}</span>
      </div>

      {fields.length > 0 ? (
        <dl className="mt-3 grid grid-cols-1 gap-2 text-xs sm:grid-cols-2 lg:grid-cols-3">
          {fields.map((f, i) => (
            <div key={i} className="flex flex-col">
              <dt className="capitalize text-muted">{f.key}</dt>
              <dd className="font-mono text-ink break-all">{f.value}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="mt-2 text-xs text-muted">No additional fields recorded.</p>
      )}
    </div>
  );
}

export function ValidationDetailsAlert({ error }: { error: ApiError | null | undefined }) {
  if (!error) return null;
  return (
    <div className="flex flex-col gap-2">
      <ErrorAlert error={error} />
      {error.details.length > 0 && (
        <Alert tone="danger">
          <ul className="list-disc pl-5 text-xs">
            {error.details.map((d, idx) => (
              <li key={idx}>
                {d.field ? `${d.field}: ` : ""}
                {d.message}
              </li>
            ))}
          </ul>
        </Alert>
      )}
    </div>
  );
}
