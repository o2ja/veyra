import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { Badge, Loading } from "../../components/ui";
import { api } from "../../lib/api/client";
import { unwrap } from "../../lib/api/errors";
import { formatDate, formatMoney } from "../../lib/format";
import { useSession } from "../../lib/session";

const STEPS = [
  {
    to: "/imports",
    title: "1. Bring in your data",
    text: "Upload your sales and purchase data from your ERP.",
  },
  {
    to: "/agreements",
    title: "2. Add your rebate contracts",
    text: "Contracts are read by AI, then confirmed by you.",
  },
  {
    to: "/claims",
    title: "3. Claim what you are owed",
    text: "Veyra finds rebates you never claimed and builds the claim.",
  },
  {
    to: "/reconciliation",
    title: "4. Match payments, recover shortfalls",
    text: "Remittances are checked line by line; short payments become cases.",
  },
] as const;

interface AttentionItem {
  count: number;
  label: string;
  to: string;
  tone?: "danger" | "warn";
}

function Kpi({ label, value, hint, to }: { label: string; value: string; hint: string; to: string }) {
  return (
    <Link
      to={to}
      className="rounded-xl border border-line bg-panel p-5 shadow-card transition-colors duration-150 hover:border-ink/30"
    >
      <p className="text-sm text-muted">{label}</p>
      <p className="mt-1.5 text-2xl font-semibold tracking-tight tabular-nums">{value}</p>
      <p className="mt-1 text-xs text-muted">{hint}</p>
    </Link>
  );
}

export function HomePage() {
  const { me, can } = useSession();
  const read = can("data.read");
  const attention = useQuery({
    queryKey: ["home", "attention"],
    enabled: read,
    queryFn: () => unwrap(api.GET("/api/v1/home/attention")),
  });
  const dash = useQuery({
    queryKey: ["dashboard", "", "", ""],
    enabled: read,
    queryFn: () => unwrap(api.GET("/api/v1/dashboard", { params: { query: {} } })),
  });
  const notes = useQuery({
    queryKey: ["notifications", "home"],
    enabled: read,
    queryFn: () =>
      unwrap(api.GET("/api/v1/notifications", { params: { query: { unread: true, limit: 5 } } })),
  });

  const a = attention.data;
  const money = a?.money_found;
  const f = dash.data?.as_of != null ? dash.data.figures : null;
  const unread = notes.data?.data ?? [];

  const items: AttentionItem[] = a
    ? [
        {
          count: a.cases_overdue,
          label: "Recovery cases past their deadline",
          to: "/recovery",
          tone: "danger" as const,
        },
        {
          count: a.cases_due_soon,
          label: `Recovery cases due within ${a.deadline_within_days} days`,
          to: "/recovery",
          tone: "warn" as const,
        },
        {
          count: a.drafts_to_review,
          label:
            a.drafts_to_review_ai > 0
              ? `Agreement drafts to review (${a.drafts_to_review_ai} read by AI)`
              : "Agreement drafts to review",
          to: "/agreement-drafts",
        },
        {
          count: a.drafts_to_approve,
          label: "Agreement drafts waiting for approval",
          to: "/agreement-drafts?status=pending_approval",
        },
        { count: a.imports_waiting, label: "Imports waiting for you", to: "/imports" },
        {
          count: a.unmapped_open,
          label: "Unknown customers or products to map",
          to: "/master-data/unmapped",
        },
        { count: a.cases_unassigned, label: "Open recovery cases with no owner", to: "/recovery" },
        {
          count: a.failed_jobs ?? 0,
          label: "Background jobs that failed",
          to: "/admin/jobs",
          tone: "danger" as const,
        },
      ].filter((i) => i.count > 0)
    : [];

  return (
    <div className="flex flex-col gap-8">
      <section className="rounded-2xl bg-rail p-6 text-rail-ink shadow-card sm:p-8">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div>
            <p className="text-sm text-rail-ink/70">{me?.tenant?.name ?? "Workspace"}</p>
            <p className="text-xs text-rail-ink/60">
              Signed in as {me?.user.email} ({me?.role})
            </p>
            <h1 className="mt-4 text-xs font-semibold tracking-[0.18em] text-rail-ink/60 uppercase">
              Money found
            </h1>
            {read && attention.isPending ? (
              <p className="mt-2 h-12 w-56 animate-pulse rounded-lg bg-white/10" aria-label="Loading" />
            ) : (
              <p className="mt-1 text-5xl font-semibold tracking-tight text-white tabular-nums">
                {money ? formatMoney(money.total) : "—"}
              </p>
            )}
            <p className="mt-2 max-w-md text-sm text-rail-ink/70">
              Rebates you are owed but never claimed, plus short payments still open.
            </p>
          </div>
          {money && (
            <dl className="grid min-w-64 gap-2 text-sm">
              <MoneyLink label="Unclaimed rebates" value={money.unclaimed} to="/claims/missed" />
              <MoneyLink label="Short-paid, still open" value={money.short_paid_open} to="/recovery" />
              <MoneyLink
                label={`of which due within ${a?.deadline_within_days ?? 14} days`}
                value={money.expiring_soon}
                to="/recovery"
                indent
              />
            </dl>
          )}
        </div>
      </section>

      {read && (
        <div className="grid gap-6 lg:grid-cols-5">
          <section
            aria-label="Needs your attention"
            className="rounded-xl border border-line bg-panel p-5 shadow-card sm:p-6 lg:col-span-3"
          >
            <div className="flex items-baseline justify-between gap-3">
              <h2 className="text-lg font-semibold tracking-tight">Needs your attention</h2>
              {a && <span className="text-xs text-muted">As of {formatDate(a.as_of_date)}</span>}
            </div>
            {attention.isPending ? (
              <Loading />
            ) : items.length === 0 ? (
              <p className="mt-4 rounded-lg border border-dashed border-line bg-surface px-4 py-6 text-center text-sm text-muted">
                Nothing needs you right now.
              </p>
            ) : (
              <ul className="mt-3 divide-y divide-line">
                {items.map((i) => (
                  <li key={i.label}>
                    <Link
                      to={i.to}
                      className="group -mx-2 flex items-center gap-4 rounded-lg px-2 py-2.5 text-sm transition-colors hover:bg-brand-soft"
                    >
                      <span
                        className={`w-10 text-right text-lg font-semibold tabular-nums ${
                          i.tone === "danger" ? "text-danger" : i.tone === "warn" ? "text-warn" : "text-ink"
                        }`}
                      >
                        {i.count}
                      </span>
                      <span className="flex-1">{i.label}</span>
                      <span
                        aria-hidden="true"
                        className="text-muted transition-transform group-hover:translate-x-0.5"
                      >
                        →
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            {unread.length > 0 && (
              <div className="mt-5 border-t border-line pt-4">
                <div className="flex items-center justify-between gap-3">
                  <h3 className="text-sm font-semibold">Reminders</h3>
                  <Badge tone="warn">{unread.length} unread</Badge>
                </div>
                <ul className="mt-2 flex flex-col gap-1.5 text-sm">
                  {unread.map((n) => (
                    <li key={n.id} className="flex justify-between gap-3">
                      <span>{n.title}</span>
                      {n.due_date && <span className="text-xs text-muted">Due {formatDate(n.due_date)}</span>}
                    </li>
                  ))}
                </ul>
                <Link to="/notifications" className="mt-3 inline-block text-sm text-brand underline">
                  See all reminders
                </Link>
              </div>
            )}
          </section>

          <section aria-label="Where you stand" className="flex flex-col gap-3 lg:col-span-2">
            <h2 className="text-lg font-semibold tracking-tight">Where you stand</h2>
            {f ? (
              <div className="grid gap-3">
                <Kpi
                  label="Claimed"
                  value={formatMoney(f.claimed)}
                  hint="Submitted to manufacturers"
                  to="/dashboard/claimed"
                />
                <Kpi
                  label="Collected"
                  value={formatMoney(f.collected)}
                  hint="Matched to remittances"
                  to="/dashboard/collected"
                />
                <Kpi
                  label="Short paid"
                  value={formatMoney(f.short_paid)}
                  hint="Paid less than claimed"
                  to="/dashboard/short_paid"
                />
              </div>
            ) : dash.isPending ? (
              <Loading />
            ) : (
              <p className="text-sm text-muted">
                Claim and collection figures appear once the dashboard has been built.{" "}
                <Link to="/dashboard" className="text-brand underline">
                  Open the figures page
                </Link>
              </p>
            )}
          </section>
        </div>
      )}

      <section
        aria-label="How it works"
        className="rounded-xl border border-line bg-panel p-5 shadow-card sm:p-6"
      >
        <h2 className="text-lg font-semibold tracking-tight">How Veyra Recover works</h2>
        <ol className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((s) => (
            <li key={s.to}>
              <Link
                to={s.to}
                className="block h-full rounded-lg border border-line p-4 transition-colors duration-150 hover:border-ink/30 hover:bg-brand-soft"
              >
                <p className="font-medium">{s.title}</p>
                <p className="mt-1 text-sm text-muted">{s.text}</p>
              </Link>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}

function MoneyLink({
  label,
  value,
  to,
  indent = false,
}: {
  label: string;
  value: string;
  to: string;
  indent?: boolean;
}) {
  return (
    <div
      className={`relative flex items-baseline justify-between gap-6 rounded-lg px-3 py-1.5 transition-colors hover:bg-white/10 ${
        indent ? "pl-6 text-rail-ink/70" : "text-rail-ink"
      }`}
    >
      <dt>
        <Link to={to} className="after:absolute after:inset-0">
          {label}
        </Link>
      </dt>
      <dd className="font-semibold text-white tabular-nums">{formatMoney(value)}</dd>
    </div>
  );
}
