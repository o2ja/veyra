import { useMemo, useState } from "react";
import { NavLink } from "react-router-dom";

import { Alert, Button, Empty, ErrorAlert } from "../../components/ui";
import type { ApiError } from "../../lib/api/errors";
import { buildTraceTree, collectExpandableIds, flattenKeyValues, type TraceNode } from "./logic";

export function ValidationDetailsAlert({ error }: { error: ApiError | null | undefined }) {
  if (!error) return null;
  return (
    <div className="flex flex-col gap-2">
      <ErrorAlert error={error} />
      {error.details.length > 0 && (
        <Alert>
          <ul className="list-disc pl-5 text-xs">
            {error.details.map((d, idx) => (
              <li key={`${d.field}-${d.code}-${idx}`}>
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

const SECTIONS = [
  { to: "/calculations", label: "Runs", end: true },
  { to: "/calculations/rebates", label: "Rebate tracker", end: false },
] as const;

export function CalculationsNav() {
  return (
    <nav
      aria-label="Calculations"
      className="mb-6 flex w-fit max-w-full flex-wrap gap-1 rounded-xl border border-line bg-panel p-1 text-sm shadow-card"
    >
      {SECTIONS.map((s) => (
        <NavLink
          key={s.to}
          to={s.to}
          end={s.end}
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

export function KeyValueGrid({
  data,
  empty = "None.",
}: {
  data: Record<string, unknown> | null | undefined;
  empty?: string;
}) {
  const entries = useMemo(() => flattenKeyValues(data), [data]);
  if (entries.length === 0) return <Empty>{empty}</Empty>;
  return (
    <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
      {entries.map((entry) => (
        <div key={entry.key} className="rounded-lg border border-line bg-surface/50 px-3 py-2">
          <dt className="text-xs text-muted break-all">{entry.key}</dt>
          <dd className="mt-0.5 font-mono text-xs break-all">{entry.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function TraceBranch({
  nodes,
  expanded,
  onToggle,
}: {
  nodes: TraceNode[];
  expanded: Set<string>;
  onToggle: (id: string, force?: boolean) => void;
}) {
  return (
    <ul className="space-y-1 pl-4 border-l border-line">
      {nodes.map((node) => {
        const hasChildren = node.children.length > 0;
        const isOpen = expanded.has(node.id);
        return (
          <li key={node.id} className="text-sm">
            {hasChildren ? (
              <div>
                <button
                  type="button"
                  aria-expanded={isOpen}
                  className="inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-left font-medium hover:bg-surface focus-visible:outline-2 focus-visible:outline-brand"
                  onClick={() => onToggle(node.id)}
                  onKeyDown={(e) => {
                    if (e.key === "ArrowRight" && !isOpen) {
                      e.preventDefault();
                      onToggle(node.id, true);
                    } else if (e.key === "ArrowLeft" && isOpen) {
                      e.preventDefault();
                      onToggle(node.id, false);
                    }
                  }}
                >
                  <span aria-hidden="true" className="font-mono text-xs text-muted">
                    {isOpen ? "▾" : "▸"}
                  </span>
                  <span>{node.key}</span>
                </button>
                {isOpen && <TraceBranch nodes={node.children} expanded={expanded} onToggle={onToggle} />}
              </div>
            ) : (
              <div className="flex flex-wrap items-baseline gap-2 px-1.5 py-0.5">
                <span className="font-medium text-muted">{node.key}:</span>
                <span className="font-mono text-xs break-all">{node.value ?? "—"}</span>
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function TraceTree({
  trace,
  label = "Calculation trace",
}: {
  trace: Record<string, unknown> | null | undefined;
  label?: string;
}) {
  const nodes = useMemo(() => buildTraceTree(trace), [trace]);
  const allIds = useMemo(() => collectExpandableIds(nodes), [nodes]);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(allIds.slice(0, 5)));

  if (nodes.length === 0) return <Empty>No trace details.</Empty>;

  function toggle(id: string, force?: boolean) {
    setExpanded((prev) => {
      const next = new Set(prev);
      const open = force ?? !next.has(id);
      if (open) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  return (
    <div role="region" aria-label={label} className="flex flex-col gap-3">
      {allIds.length > 0 && (
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => setExpanded(new Set(allIds))}>
            Expand all
          </Button>
          <Button variant="secondary" onClick={() => setExpanded(new Set())}>
            Collapse all
          </Button>
        </div>
      )}
      <TraceBranch nodes={nodes} expanded={expanded} onToggle={toggle} />
    </div>
  );
}
