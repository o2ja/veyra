import * as Dialog from "@radix-ui/react-dialog";
import { useMutation } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";

import { Alert, Button, ErrorAlert } from "../../components/ui";
import { api } from "../../lib/api/client";
import type { ApiError } from "../../lib/api/errors";
import { unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { useManufacturers } from "../masterdata/common";

const SECTIONS = [
  { to: "/claims/missed", label: "Missed claims", permission: "data.read" },
  { to: "/claims/batches", label: "Claim batches", permission: "data.read" },
  { to: "/claims/adjustments", label: "Adjustments", permission: "data.read" },
  { to: "/claims/templates", label: "Export templates", permission: "claim_batch.create" },
] as const;

export function ClaimsNav() {
  const { can } = useSession();
  return (
    <nav
      aria-label="Claims"
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

export function ValidationDetailsAlert({ error }: { error: ApiError | null | undefined }) {
  if (!error) return null;
  return (
    <div className="flex flex-col gap-2">
      <ErrorAlert error={error} />
      {error.details.length > 0 && (
        <Alert>
          <ul className="list-disc pl-5 text-xs">
            {error.details.map((d, idx) => {
              const extra = d as unknown as { batch_id?: string; entry_id?: string; adjustment_id?: string };
              const subject = extra.adjustment_id ?? extra.entry_id;
              return (
                <li key={`${d.field}-${d.code}-${idx}`}>
                  {d.field ? `${d.field}: ` : ""}
                  {subject && <span className="font-mono">{subject}: </span>}
                  {d.message}
                  {extra.batch_id && (
                    <>
                      {" "}
                      —{" "}
                      <Link
                        to={`/claims/batches/${extra.batch_id}`}
                        className="font-medium text-brand underline"
                      >
                        View batch
                      </Link>
                    </>
                  )}
                </li>
              );
            })}
          </ul>
        </Alert>
      )}
    </div>
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
        <Dialog.Content className="fixed left-1/2 top-1/3 z-50 w-[min(32rem,92vw)] -translate-x-1/2 rounded-xl border border-line bg-panel shadow-card p-6 shadow-lg">
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

export function FileDownloadButton({
  fileId,
  label = "Download file",
  className = "",
}: {
  fileId: string;
  label?: string;
  className?: string;
}) {
  const download = useMutation({
    mutationFn: async () => {
      const res = await unwrap(
        api.GET("/api/v1/files/{file_id}/download-url", {
          params: { path: { file_id: fileId } },
        }),
      );
      window.open(res.url, "_blank", "noopener");
      return res;
    },
  });

  return (
    <div className="inline-flex flex-col gap-1">
      <Button
        variant="secondary"
        className={className}
        disabled={download.isPending}
        onClick={() => download.mutate()}
      >
        {download.isPending ? "Generating link…" : label}
      </Button>
      <ErrorAlert error={download.error as ApiError | null} />
    </div>
  );
}
