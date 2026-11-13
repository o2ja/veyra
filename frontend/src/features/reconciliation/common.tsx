import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";
import { useMemo, useState } from "react";
import { NavLink } from "react-router-dom";

import { Alert, Button, Empty, ErrorAlert } from "../../components/ui";
import type { ApiError } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { useManufacturers } from "../masterdata/common";
import { buildEvidenceTree, collectExpandableIds, type EvidenceNode } from "./logic";

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

export function useManufacturerMap() {
  const list = useManufacturers();
  const map = new Map<string, { id: string; name: string; code: string }>();
  for (const m of list.data?.data ?? []) {
    map.set(m.id, m);
  }
  return map;
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

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Confirm",
  confirmVariant = "primary",
  busy = false,
  onConfirm,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  confirmLabel?: string;
  confirmVariant?: "primary" | "secondary" | "danger";
  busy?: boolean;
  onConfirm: () => void;
  children?: ReactNode;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <Dialog.Content className="fixed left-1/2 top-1/3 z-50 w-[min(32rem,92vw)] -translate-x-1/2 rounded-lg border border-line bg-panel p-6 shadow-lg">
          <Dialog.Title className="text-lg font-semibold">{title}</Dialog.Title>
          {description && (
            <Dialog.Description className="mt-1 text-sm text-muted">{description}</Dialog.Description>
          )}
          {children && <div className="mt-4">{children}</div>}
          <div className="mt-6 flex justify-end gap-2">
            <Button variant="secondary" disabled={busy} onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button variant={confirmVariant} disabled={busy} onClick={onConfirm}>
              {busy ? "Working…" : confirmLabel}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
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
