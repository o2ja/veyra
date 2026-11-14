import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { Badge, Loading } from "../../components/ui";
import { api } from "../../lib/api/client";
import { unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { formatDecimal } from "../reporting/logic";

function Kpi({
  label,
  value,
  hint,
  to,
  tone,
}: {
  label: string;
  value: string;
  hint: string;
  to: string;
  tone?: "ok" | "warn";
}) {
  const zero = Number(value.replaceAll(",", "")) === 0;
  const color = zero ? "text-ink" : tone === "ok" ? "text-ok" : tone === "warn" ? "text-warn" : "text-ink";
  return (
    <Link
      to={to}
      className="group rounded-xl border border-line bg-panel p-5 shadow-card transition hover:border-brand/50"
    >
      <p className="text-sm font-medium text-muted">{label}</p>
      <p className={`mt-2 font-mono text-3xl font-semibold tracking-tight ${color}`}>{value}</p>
      <p className="mt-1.5 text-xs text-muted">{hint}</p>
    </Link>
  );
}

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

export function HomePage() {
  const { me, can } = useSession();
  const read = can("data.read");
  const dash = useQuery({
    queryKey: ["dashboard", "", "", ""],
    enabled: read,
    queryFn: () => unwrap(api.GET("/api/v1/dashboard", { params: { query: {} } })),
  });
  const missed = useQuery({
    queryKey: ["missed-claims", "summary", "home"],
    enabled: read,
    queryFn: () => unwrap(api.GET("/api/v1/missed-claims/summary", { params: { query: {} } })),
  });
  const cases = useQuery({
    queryKey: ["recovery", "summary"],
    enabled: read,
    queryFn: () => unwrap(api.GET("/api/v1/cases/summary")),
  });
  const notes = useQuery({
    queryKey: ["notifications", "home"],
    enabled: read,
    queryFn: () =>
      unwrap(api.GET("/api/v1/notifications", { params: { query: { unread: true, limit: 5 } } })),
  });

  const f = dash.data?.figures;
  const built = dash.data?.as_of != null;
  const openCases = (cases.data?.by_status ?? []).reduce((n, r) => n + r.count, 0);
  const unread = notes.data?.data ?? [];

  return (
    <div className="flex flex-col gap-8">
      <section className="rounded-2xl bg-rail p-6 text-rail-ink shadow-card sm:p-8">
        <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">
          {me?.tenant?.name ?? "Workspace"}
        </h1>
        <p className="mt-1 text-sm text-rail-ink/70">
          Signed in as {me?.user.email} ({me?.role})
        </p>
        <p className="mt-4 max-w-2xl text-sm leading-relaxed text-rail-ink/85">
          Veyra Recover finds manufacturer rebates you are owed but never claimed, tracks every claim to
          payment, and chases the money that was short-paid. Start with the figures below, or follow the four
          steps further down.
        </p>
        {read && (
          <div className="mt-5 flex flex-wrap gap-3">
            <Link
              to="/claims/missed"
              className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-panel hover:brightness-110"
            >
              Review unclaimed rebates
            </Link>
            <Link
              to="/dashboard"
              className="rounded-lg border border-white/25 px-4 py-2 text-sm font-medium text-white hover:bg-white/10"
            >
              View key figures
            </Link>
          </div>
        )}
      </section>

      {read && (
        <section aria-label="Where you stand" className="flex flex-col gap-3">
          <h2 className="text-lg font-semibold tracking-tight">Where you stand</h2>
          {(dash.isPending || missed.isPending) && <Loading label="Loading your figures…" />}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {missed.data && (
              <Kpi
                label="Unclaimed rebates"
                value={formatDecimal(missed.data.totals.amount)}
                hint={`${missed.data.totals.lines} sales lines never claimed`}
                to="/claims/missed"
                tone="warn"
              />
            )}
            {f && built && (
              <>
                <Kpi
                  label="Claimed"
                  value={formatDecimal(f.claimed)}
                  hint="Submitted to manufacturers"
                  to="/dashboard/claimed"
                />
                <Kpi
                  label="Collected"
                  value={formatDecimal(f.collected)}
                  hint="Matched to remittances"
                  to="/dashboard/collected"
                  tone="ok"
                />
                <Kpi
                  label="Short paid"
                  value={formatDecimal(f.short_paid)}
                  hint="Paid less than claimed"
                  to="/dashboard/short_paid"
                  tone="warn"
                />
              </>
            )}
            {cases.data && (
              <Kpi
                label="Cases being chased"
                value={String(openCases)}
                hint={`${cases.data.at_risk.count} near a deadline`}
                to="/recovery"
              />
            )}
          </div>
          {dash.data && !built && (
            <p className="text-sm text-muted">
              Claim and collection figures appear once the dashboard has been built.{" "}
              <Link to="/dashboard" className="text-brand underline">
                Open the figures page
              </Link>
            </p>
          )}
        </section>
      )}

      <div className="grid gap-6 lg:grid-cols-5">
        <section
          aria-label="How it works"
          className="rounded-xl border border-line bg-panel p-5 shadow-card sm:p-6 lg:col-span-3"
        >
          <h2 className="text-lg font-semibold tracking-tight">How Veyra Recover works</h2>
          <p className="mt-1 text-sm text-muted">
            Four steps, in this order. Each one opens the place where it happens.
          </p>
          <ol className="mt-4 grid gap-3 sm:grid-cols-2">
            {STEPS.map((s) => (
              <li key={s.to}>
                <Link
                  to={s.to}
                  className="block h-full rounded-lg border border-line p-4 transition hover:border-brand/50 hover:bg-brand-soft"
                >
                  <p className="font-medium">{s.title}</p>
                  <p className="mt-1 text-sm text-muted">{s.text}</p>
                </Link>
              </li>
            ))}
          </ol>
        </section>

        {read && (
          <section
            aria-label="Needs your attention"
            className="rounded-xl border border-line bg-panel p-5 shadow-card sm:p-6 lg:col-span-2"
          >
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-lg font-semibold tracking-tight">Needs your attention</h2>
              {unread.length > 0 && <Badge tone="warn">{unread.length} unread</Badge>}
            </div>
            {notes.isPending ? (
              <Loading />
            ) : unread.length === 0 ? (
              <p className="mt-4 rounded-lg border border-dashed border-line bg-surface px-4 py-6 text-center text-sm text-muted">
                Nothing needs you right now. Reminders and deadlines will show up here.
              </p>
            ) : (
              <ul className="mt-3 divide-y divide-line">
                {unread.map((n) => (
                  <li key={n.id} className="py-2.5 text-sm">
                    <p className="font-medium">{n.title}</p>
                    {n.due_date && <p className="text-xs text-muted">Due {n.due_date}</p>}
                  </li>
                ))}
              </ul>
            )}
            <Link to="/notifications" className="mt-3 inline-block text-sm text-brand underline">
              See all alerts
            </Link>
          </section>
        )}
      </div>
    </div>
  );
}
