import * as Dialog from "@radix-ui/react-dialog";
import { useMutation } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

import { Button, ErrorAlert } from "../../components/ui";
import { api } from "../../lib/api/client";
import { type ApiError, unwrap } from "../../lib/api/errors";

const SECTIONS = [
  { to: "/recovery", label: "Recovery queue", end: true },
  { to: "/notifications", label: "Notifications", end: false },
] as const;

export function RecoveryNav() {
  return (
    <nav aria-label="Recovery" className="mb-6 flex flex-wrap gap-4 text-sm">
      {SECTIONS.map((section) => (
        <NavLink
          key={section.to}
          to={section.to}
          end={section.end}
          className={({ isActive }) =>
            isActive ? "font-medium text-ink underline underline-offset-4" : "text-muted hover:text-ink"
          }
        >
          {section.label}
        </NavLink>
      ))}
    </nav>
  );
}

/** A modal action: focus is trapped by Radix, Escape closes it, every action is labelled. */
export function ActionDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Confirm",
  confirmVariant = "primary",
  confirmDisabled = false,
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
  confirmDisabled?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  children?: ReactNode;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <Dialog.Content className="fixed left-1/2 top-1/3 z-50 w-[min(34rem,92vw)] -translate-x-1/2 rounded-lg border border-line bg-panel p-6 shadow-lg">
          <Dialog.Title className="text-lg font-semibold">{title}</Dialog.Title>
          {description && (
            <Dialog.Description className="mt-1 text-sm text-muted">{description}</Dialog.Description>
          )}
          {children && <div className="mt-4">{children}</div>}
          <div className="mt-6 flex justify-end gap-2">
            <Button variant="secondary" disabled={busy} onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button variant={confirmVariant} disabled={busy || confirmDisabled} onClick={onConfirm}>
              {busy ? "Working…" : confirmLabel}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function TreeBranch({ name, value, depth }: { name: string; value: unknown; depth: number }) {
  const label = <span className="font-medium text-muted">{name}</span>;

  if (Array.isArray(value)) {
    return (
      <li>
        <details open={depth < 1}>
          <summary className="cursor-pointer text-sm">
            {label} <span className="text-xs text-muted">({value.length})</span>
          </summary>
          <ul className="mt-1 space-y-1 border-l border-line pl-4">
            {value.map((item, index) => (
              <TreeBranch
                key={`${name}-${index}`}
                name={`${name}[${index}]`}
                value={item}
                depth={depth + 1}
              />
            ))}
          </ul>
        </details>
      </li>
    );
  }

  if (isRecord(value)) {
    return (
      <li>
        <details open={depth < 1}>
          <summary className="cursor-pointer text-sm">{label}</summary>
          <ul className="mt-1 space-y-1 border-l border-line pl-4">
            {Object.entries(value).map(([key, child]) => (
              <TreeBranch key={`${name}-${key}`} name={key} value={child} depth={depth + 1} />
            ))}
          </ul>
        </details>
      </li>
    );
  }

  return (
    <li className="flex flex-wrap items-baseline gap-2 text-sm">
      {label}
      <span className="font-mono text-xs break-all">
        {value === null || value === undefined ? "—" : String(value)}
      </span>
    </li>
  );
}

/** Untyped server objects (`dispute_assessment`, event payloads) as a labelled, keyboard-usable tree. */
export function GenericTree({
  data,
  label,
  empty = "None.",
}: {
  data: Record<string, unknown> | null | undefined;
  label: string;
  empty?: string;
}) {
  const entries = data ? Object.entries(data) : [];
  if (entries.length === 0) return <p className="text-sm text-muted">{empty}</p>;
  return (
    <div role="region" aria-label={label} className="rounded-md border border-line bg-surface/50 p-3">
      <ul className="space-y-1">
        {entries.map(([key, value]) => (
          <TreeBranch key={key} name={key} value={value} depth={0} />
        ))}
      </ul>
    </div>
  );
}

/** Attachments are stored by file id; the download URL is issued by the server per click. */
export function AttachmentDownloadButton({ fileId, label = "Download" }: { fileId: string; label?: string }) {
  const download = useMutation({
    mutationFn: async () => {
      const result = await unwrap(
        api.GET("/api/v1/files/{file_id}/download-url", {
          params: { path: { file_id: fileId } },
        }),
      );
      window.open(result.url, "_blank", "noopener");
      return result;
    },
  });

  return (
    <div className="flex flex-col gap-1">
      <Button
        variant="secondary"
        disabled={download.isPending}
        onClick={() => download.mutate()}
        aria-label={`${label} attachment ${fileId}`}
      >
        {download.isPending ? "Preparing…" : label}
      </Button>
      <ErrorAlert error={download.error as ApiError | null} />
    </div>
  );
}
