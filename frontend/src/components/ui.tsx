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
    primary: "bg-brand text-panel shadow-card hover:bg-brand/85",
    secondary: "border border-line bg-panel shadow-card hover:border-ink/30 hover:bg-brand-soft",
    danger: "bg-danger text-panel shadow-card hover:bg-danger/85",
  }[variant];
  return (
    <button
      type="button"
      className={`inline-flex cursor-pointer items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium
        transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand
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
      <label htmlFor={id} className="text-sm font-medium text-ink">
        {label}
      </label>
      <input
        id={id}
        ref={ref}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        className="aria-[invalid=true]:border-danger"
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
    danger: "border-danger/30 bg-danger-soft text-danger",
    ok: "border-ok/30 bg-ok-soft text-ok",
    info: "border-brand/25 bg-brand-soft text-ink",
  }[tone];
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      className={`rounded-lg border px-3.5 py-2.5 text-sm ${styles}`}
    >
      {children}
    </div>
  );
}

export function rateLimitMessage(
  error: { code?: string; status?: number; retryAfter?: number | string | null } | null | undefined,
): string | null {
  if (!error) return null;
  const isRateLimited =
    error.status === 429 || error.code === "rate_limited" || error.code === "too_many_extractions";
  if (!isRateLimited) return null;

  const seconds =
    typeof error.retryAfter === "number"
      ? error.retryAfter
      : typeof error.retryAfter === "string"
        ? parseInt(error.retryAfter, 10)
        : null;

  if (typeof seconds === "number" && Number.isFinite(seconds) && seconds > 0) {
    return `Too many requests. Try again in ${seconds} seconds.`;
  }
  return "Too many requests. Try again in a minute.";
}

export function RateLimitAlert({
  error,
}: {
  error: { code?: string; status?: number; retryAfter?: number | string | null } | null | undefined;
}) {
  const message = rateLimitMessage(error);
  if (!message) return null;
  return <Alert tone="danger">{message}</Alert>;
}

export function ErrorAlert({ error }: { error: ApiError | null | undefined }) {
  if (!error) return null;
  const rateLimitMsg = rateLimitMessage(error);
  return (
    <Alert>
      {rateLimitMsg ?? error.message}
      {error.requestId && <span className="block text-xs">Reference: {error.requestId}</span>}
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
    <section className="rounded-xl border border-line bg-panel p-5 shadow-card sm:p-6" aria-label={title}>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="flex items-center gap-2.5 py-6 text-sm text-muted">
      <span
        aria-hidden="true"
        className="size-4 animate-spin rounded-full border-2 border-line border-t-brand"
      />
      {label}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-line bg-surface px-4 py-6 text-center text-sm text-muted">
      {children}
    </p>
  );
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
    <div className="mx-auto mt-8 w-full max-w-md sm:mt-16">
      <Logo className="mx-auto mb-5 h-8" />
      <p className="mb-6 text-center text-sm text-muted">
        Veyra Recover finds and recovers the rebates manufacturers owe you.
      </p>
      <Card title={title}>{children}</Card>
    </div>
  );
}

/** The Veyra wordmark (black on transparent; inverted for dark surfaces). */
export function Logo({ className = "h-6", inverted = false }: { className?: string; inverted?: boolean }) {
  return (
    <img
      src="/veyra-logo.png"
      alt="Veyra"
      width={640}
      height={115}
      className={`w-auto select-none ${inverted ? "invert" : "dark:invert"} ${className}`}
    />
  );
}

/** First server-side field error for a form field (backend validation is authoritative). */
export function fieldError(error: ApiError | null | undefined, field: string): string | undefined {
  return error?.details.find((d) => d.field === field)?.message;
}

const TONES = {
  ok: "bg-ok-soft text-ok",
  warn: "bg-warn-soft text-warn",
  danger: "bg-danger-soft text-danger",
  info: "bg-brand-soft text-brand",
  neutral: "bg-surface text-muted",
} as const;

/** Small status pill; always pairs colour with text so meaning never rests on colour alone. */
export function Badge({ tone = "neutral", children }: { tone?: keyof typeof TONES; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${TONES[tone]}`}
    >
      {children}
    </span>
  );
}

/** Page title block: heading, one plain-language line saying what the page is for, and page actions. */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="max-w-2xl">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-3">{actions}</div>}
    </div>
  );
}
