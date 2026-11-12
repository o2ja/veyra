import {
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  forwardRef,
  useId,
} from "react";

import type { ApiError } from "../lib/api/errors";

export function Button({
  variant = "primary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" }) {
  const styles = {
    primary: "bg-brand text-white hover:opacity-90",
    secondary: "border border-line bg-panel hover:bg-surface",
    danger: "bg-danger text-white hover:opacity-90",
  }[variant];
  return (
    <button
      type="button"
      className={`inline-flex items-center justify-center rounded-md px-3 py-2 text-sm font-medium
        disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand
        ${styles} ${className}`}
      {...props}
    />
  );
}

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  error?: string | undefined;
  hint?: string;
}

export const Field = forwardRef<HTMLInputElement, FieldProps>(function Field(
  { label, error, hint, ...props },
  ref,
) {
  const generated = useId();
  const id = props.id ?? generated; // a caller's id must also be the label's target
  const describedBy = [error ? `${id}-error` : null, hint ? `${id}-hint` : null].filter(Boolean).join(" ");
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <input
        id={id}
        ref={ref}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        className="rounded-md border border-line bg-panel px-3 py-2 text-sm
          focus-visible:outline-2 focus-visible:outline-brand aria-[invalid=true]:border-danger"
        {...props}
      />
      {hint && (
        <p id={`${id}-hint`} className="text-xs text-muted">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  );
});

export function Alert({
  tone = "danger",
  children,
}: {
  tone?: "danger" | "ok" | "info";
  children: ReactNode;
}) {
  const styles = {
    danger: "border-danger text-danger",
    ok: "border-ok text-ok",
    info: "border-line text-ink",
  }[tone];
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      className={`rounded-md border px-3 py-2 text-sm ${styles}`}
    >
      {children}
    </div>
  );
}

export function ErrorAlert({ error }: { error: ApiError | null | undefined }) {
  if (!error) return null;
  return (
    <Alert>
      {error.message}
      {error.requestId && <span className="block text-xs opacity-70">Reference: {error.requestId}</span>}
    </Alert>
  );
}

export function Card({
  title,
  children,
  actions,
}: {
  title: string;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section className="rounded-lg border border-line bg-panel p-6" aria-label={title}>
      <div className="mb-4 flex items-center justify-between gap-4">
        <h1 className="text-lg font-semibold">{title}</h1>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p role="status" aria-live="polite" className="text-sm text-muted">
      {label}
    </p>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted">{children}</p>;
}

export function PermissionDenied() {
  return (
    <Card title="Not available">
      <p className="text-sm text-muted">
        Your role does not include this page. Ask a workspace admin if you need it.
      </p>
    </Card>
  );
}

export function AuthCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mx-auto mt-12 w-full max-w-md">
      <Card title={title}>{children}</Card>
    </div>
  );
}

/** First server-side field error for a form field (backend validation is authoritative). */
export function fieldError(error: ApiError | null | undefined, field: string): string | undefined {
  return error?.details.find((d) => d.field === field)?.message;
}
