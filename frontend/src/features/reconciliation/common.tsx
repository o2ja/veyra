import { useMemo, useState } from "react";
import { NavLink } from "react-router-dom";

import { Empty } from "../../components/ui";
import { useSession } from "../../lib/session";
import { buildEvidenceTree, collectExpandableIds, type EvidenceNode } from "./logic";

export { ConfirmDialog, ValidationDetailsAlert, useManufacturerMap } from "../claims/common";

const SECTIONS = [
  { to: "/reconciliation", label: "Remittances", permission: "data.read" },
  { to: "/reconciliation/new", label: "New remittance", permission: "remittance.reconcile" },
  { to: "/reconciliation/reason-codes", label: "Reason codes", permission: "data.read" },
] as const;

export function ReconciliationNav() {
  const { can } = useSession();
  return (
    <nav aria-label="Reconciliation" className="mb-6 flex flex-wrap gap-4 text-sm">
      {SECTIONS.filter((s) => can(s.permission)).map((s) => (
        <NavLink
          key={s.to}
          to={s.to}
          end={s.to === "/reconciliation"}
          className={({ isActive }) =>
            isActive ? "font-medium text-ink underline underline-offset-4" : "text-muted hover:text-ink"
          }
        >
          {s.label}
        </NavLink>
      ))}
    </nav>
  );
}

function EvidenceBranch({
  nodes,
  expanded,
  onToggle,
}: {
  nodes: EvidenceNode[];
  expanded: Set<string>;
  onToggle: (id: string) => void;
}) {
  return (
    <ul className="flex flex-col gap-1 font-mono text-xs">
      {nodes.map((node) => {
        const hasChildren = node.children.length > 0;
        const isExpanded = expanded.has(node.id);
        return (
          <li key={node.id} className="flex flex-col">
            <div className="flex items-center gap-1.5">
              {hasChildren ? (
                <button
                  type="button"
                  aria-expanded={isExpanded}
                  onClick={() => onToggle(node.id)}
                  className="rounded px-1 py-0.5 text-muted hover:bg-surface hover:text-ink"
                >
                  {isExpanded ? "▼" : "▶"} <span className="font-semibold text-ink">{node.key}</span>
                </button>
              ) : (
                <div className="flex items-baseline gap-2 pl-4">
                  <span className="text-muted">{node.key}:</span>
                  <span className="font-semibold">{node.value}</span>
                </div>
              )}
            </div>
            {hasChildren && isExpanded && (
              <div className="border-l border-line pl-3">
                <EvidenceBranch nodes={node.children} expanded={expanded} onToggle={onToggle} />
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function EvidenceTree({
  evidence,
  label = "Match evidence",
}: {
  evidence: Record<string, unknown> | null | undefined;
  label?: string;
}) {
  const nodes = useMemo(() => buildEvidenceTree(evidence), [evidence]);
  const allIds = useMemo(() => collectExpandableIds(nodes), [nodes]);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(allIds.slice(0, 5)));

  if (nodes.length === 0) return <Empty>No evidence details.</Empty>;

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div
      role="region"
      aria-label={label}
      className="flex flex-col gap-2 rounded border border-line p-3 bg-surface/30"
    >
      <div className="flex items-center justify-between text-xs">
        <span className="font-semibold text-muted uppercase tracking-wider">{label}</span>
        {allIds.length > 0 && (
          <div className="flex gap-1">
            <button
              type="button"
              className="text-xs text-brand underline"
              onClick={() => setExpanded(new Set(allIds))}
            >
              Expand all
            </button>
            <span className="text-muted">·</span>
            <button
              type="button"
              className="text-xs text-brand underline"
              onClick={() => setExpanded(new Set())}
            >
              Collapse all
            </button>
          </div>
        )}
      </div>
      <EvidenceBranch nodes={nodes} expanded={expanded} onToggle={toggle} />
    </div>
  );
}
