import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { Alert, Button, Card, Empty, ErrorAlert, Loading, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { ManufacturerSelect } from "../masterdata/common";
import { BranchSelect, ReportingNav } from "./common";
import {
  formatDecimal,
  jobPollDecision,
  METRIC_DESCRIPTIONS,
  METRIC_KEYS,
  METRIC_LABELS,
  type MetricKey,
} from "./logic";

export function DashboardPage() {
  const { can } = useSession();
  const client = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const manufacturerId = searchParams.get("manufacturer_id") ?? "";
  const branchId = searchParams.get("branch_id") ?? "";
  const monthParam = searchParams.get("month") ?? ""; // e.g. "2026-06-01" or ""

  // month input uses YYYY-MM
  const monthInputValue = monthParam ? monthParam.slice(0, 7) : "";

  const [refreshNotice, setRefreshNotice] = useState<string | null>(null);
  const [refreshJobId, setRefreshJobId] = useState<string | null>(null);

  const queryParams = {
    ...(manufacturerId ? { manufacturer_id: manufacturerId } : {}),
    ...(branchId ? { branch_id: branchId } : {}),
    ...(monthParam ? { month: monthParam } : {}),
  };

  const dashboardQuery = useQuery({
    queryKey: ["dashboard", manufacturerId, branchId, monthParam],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/dashboard", {
          params: {
            query: queryParams,
          },
        }),
      ),
  });

  // Polling for refresh job if queued
  useQuery({
    queryKey: ["jobs", refreshJobId],
    enabled: Boolean(refreshJobId),
    queryFn: () => {
      if (!refreshJobId) throw new Error("Missing job id");
      return unwrap(
        api.GET("/api/v1/jobs/{job_id}", {
          params: { path: { job_id: refreshJobId } },
        }),
      );
    },
    refetchInterval: (query) => {
      if (jobPollDecision(query.state.data?.status) !== "poll") {
        setRefreshJobId(null);
        void client.invalidateQueries({ queryKey: ["dashboard"] });
        return false;
      }
      return 1500;
    },
  });

  const refreshMutation = useMutation({
    mutationFn: async () => {
      return unwrap(api.POST("/api/v1/dashboard/refresh"));
    },
    onSuccess: (data) => {
      if (data && typeof data === "object" && "job_id" in data && data.job_id) {
        setRefreshJobId(String(data.job_id));
        setRefreshNotice("Dashboard refresh queued.");
      } else {
        setRefreshNotice("A refresh is already queued");
      }
      setTimeout(() => setRefreshNotice(null), 5000);
    },
  });

  function updateFilter(key: "manufacturer_id" | "branch_id" | "month", value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    setSearchParams(next, { replace: true });
  }

  const queryErr = dashboardQuery.error as ApiError | null;
  if (queryErr?.status === 403 || queryErr?.code === "forbidden") {
    return (
      <>
        <ReportingNav />
        <PermissionDenied />
      </>
    );
  }

  if (dashboardQuery.isPending) {
    return (
      <>
        <ReportingNav />
        <Loading />
      </>
    );
  }

  if (queryErr) {
    return (
      <>
        <ReportingNav />
        <ErrorAlert error={queryErr} />
      </>
    );
  }

  const d = dashboardQuery.data;

  // Empty state when as_of is null
  if (!d || d.as_of === null) {
    return (
      <>
        <ReportingNav />
        <Card title="Executive Dashboard">
          <Empty>Figures have not been built yet</Empty>
          {refreshMutation.error && <ErrorAlert error={refreshMutation.error as ApiError} />}
          {can("report.refresh") && (
            <div className="mt-4">
              <Button
                disabled={refreshMutation.isPending || Boolean(refreshJobId)}
                onClick={() => refreshMutation.mutate()}
              >
                {refreshMutation.isPending || refreshJobId ? "Refreshing…" : "Build figures"}
              </Button>
            </div>
          )}
        </Card>
      </>
    );
  }

  const hasRecoveredAfterWriteOff =
    d.figures.recovered_after_write_off !== undefined && d.figures.recovered_after_write_off !== null;

  // Build the drill-down link preserving active query params
  function drilldownUrl(metric: MetricKey, month = monthParam) {
    const params = new URLSearchParams();
    if (manufacturerId) params.set("manufacturer_id", manufacturerId);
    if (branchId) params.set("branch_id", branchId);
    if (month) params.set("month", month);
    const qs = params.toString();
    return `/dashboard/${metric}${qs ? `?${qs}` : ""}`;
  }

  return (
    <>
      <ReportingNav />

      <div className="flex flex-col gap-6">
        {/* Header + Refresh action */}
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="font-semibold text-2xl text-ink">Dashboard</h1>
            <p className="text-muted text-xs">
              As of <span className="font-mono">{d.as_of}</span>
              {d.as_of_date && (
                <>
                  ; at-risk computed for <span className="font-mono">{d.as_of_date}</span>
                </>
              )}
            </p>
          </div>

          <div className="flex items-center gap-3">
            <Link
              to="/calculations/rebates"
              className="text-xs text-muted underline-offset-2 hover:text-ink hover:underline"
            >
              Rebate tier tracker →
            </Link>
            {can("report.refresh") && (
              <Button
                variant="secondary"
                disabled={refreshMutation.isPending || Boolean(refreshJobId)}
                onClick={() => refreshMutation.mutate()}
              >
                {refreshMutation.isPending || refreshJobId ? "Refreshing…" : "Refresh figures"}
              </Button>
            )}
          </div>
        </div>

        {refreshNotice && <Alert tone="info">{refreshNotice}</Alert>}
        {refreshMutation.error && <ErrorAlert error={refreshMutation.error as ApiError} />}

        {/* Filters */}
        <Card title="Filters">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="flex flex-col gap-1 text-sm">
              <ManufacturerSelect
                value={manufacturerId}
                onChange={(v) => updateFilter("manufacturer_id", v)}
              />
            </div>

            <BranchSelect value={branchId} onChange={(v) => updateFilter("branch_id", v)} />

            <div className="flex flex-col gap-1 text-sm">
              <label htmlFor="month-filter" className="font-medium text-xs text-muted">
                Month
              </label>
              <input
                id="month-filter"
                type="month"
                value={monthInputValue}
                onChange={(e) => {
                  const val = e.target.value;
                  // sends YYYY-MM-01
                  updateFilter("month", val ? `${val}-01` : "");
                }}
                className="rounded border border-line bg-surface p-2 text-sm font-mono"
              />
            </div>
          </div>
        </Card>

        {/* Figures Cards Grid */}
        <section aria-label="Key performance figures">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {METRIC_KEYS.map((key) => {
              const val = d.figures[key];
              const label = METRIC_LABELS[key] ?? key;
              const formatted = formatDecimal(val);
              const desc = METRIC_DESCRIPTIONS[key];
              const to = drilldownUrl(key);

              return (
                <div key={key} className="flex flex-col">
                  <Link
                    to={to}
                    aria-label={`${label}: ${formatted}, view details`}
                    className="group flex flex-col justify-between rounded-lg border border-line bg-panel p-5 transition hover:border-ink/50 hover:shadow-sm"
                  >
                    <div>
                      <div className="flex items-center justify-between">
                        <span className="font-medium text-muted text-xs uppercase tracking-wide">
                          {label}
                        </span>
                        <span aria-hidden="true" className="text-muted text-xs group-hover:text-ink">
                          ↗
                        </span>
                      </div>
                      <p className="mt-2 font-mono font-semibold text-2xl text-ink">{formatted}</p>
                    </div>
                    {desc && <p className="mt-2 text-muted text-xs leading-relaxed">{desc}</p>}
                  </Link>

                  {/* Reserved slot for Recovered after write-off rendered immediately after written_off */}
                  {key === "written_off" && hasRecoveredAfterWriteOff && (
                    <Link
                      to={drilldownUrl("recovered_after_write_off")}
                      aria-label={`${METRIC_LABELS.recovered_after_write_off}: ${formatDecimal(d.figures.recovered_after_write_off)}, view details`}
                      className="group mt-4 flex flex-col justify-between rounded-lg border border-line bg-panel p-5 transition hover:border-ink/50 hover:shadow-sm"
                    >
                      <div>
                        <div className="flex items-center justify-between">
                          <span className="font-medium text-muted text-xs uppercase tracking-wide">
                            {METRIC_LABELS.recovered_after_write_off}
                          </span>
                          <span aria-hidden="true" className="text-muted text-xs group-hover:text-ink">
                            ↗
                          </span>
                        </div>
                        <p className="mt-2 font-mono font-semibold text-2xl text-ink">
                          {formatDecimal(d.figures.recovered_after_write_off)}
                        </p>
                      </div>
                      <p className="mt-2 text-muted text-xs leading-relaxed">
                        {METRIC_DESCRIPTIONS.recovered_after_write_off}
                      </p>
                    </Link>
                  )}
                </div>
              );
            })}
          </div>
        </section>

        {/* Monthly table */}
        <Card title={`Monthly breakdown (${d.months.length} months)`}>
          {d.months.length === 0 ? (
            <Empty>No monthly figures available for selected filters.</Empty>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm" aria-label="Monthly breakdown table">
                <thead>
                  <tr className="border-b border-line text-muted text-xs uppercase">
                    <th scope="col" className="py-2 pr-4 font-medium">
                      Month
                    </th>
                    <th scope="col" className="py-2 pr-4 text-right font-medium">
                      Claimed
                    </th>
                    <th scope="col" className="py-2 pr-4 text-right font-medium">
                      Collected
                    </th>
                    <th scope="col" className="py-2 pr-4 text-right font-medium">
                      Short paid
                    </th>
                    <th scope="col" className="py-2 pr-4 text-right font-medium">
                      Recovered
                    </th>
                    <th scope="col" className="py-2 pr-4 text-right font-medium">
                      Written off
                    </th>
                    {hasRecoveredAfterWriteOff && (
                      <th scope="col" className="py-2 pr-4 text-right font-medium">
                        Recov. post-WO
                      </th>
                    )}
                    <th scope="col" className="py-2 pr-4 text-right font-medium">
                      At risk
                    </th>
                    <th scope="col" className="py-2 pr-4 text-right font-medium">
                      Expiring
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {d.months.map((m) => {
                    const cell = (metric: MetricKey, value: string | null | undefined) => (
                      <td className="py-2 pr-4 text-right font-mono">
                        <Link
                          to={drilldownUrl(metric, m.month)}
                          aria-label={`${METRIC_LABELS[metric] ?? metric} ${m.month}: ${formatDecimal(value)}, view details`}
                          className="underline-offset-2 hover:underline"
                        >
                          {formatDecimal(value)}
                        </Link>
                      </td>
                    );
                    return (
                      <tr key={m.month} className="border-b border-line hover:bg-surface/50">
                        <td className="py-2 pr-4 font-mono font-medium">{m.month}</td>
                        {cell("claimed", m.figures.claimed)}
                        {cell("collected", m.figures.collected)}
                        {cell("short_paid", m.figures.short_paid)}
                        {cell("recovered", m.figures.recovered)}
                        {cell("written_off", m.figures.written_off)}
                        {hasRecoveredAfterWriteOff &&
                          cell("recovered_after_write_off", m.figures.recovered_after_write_off)}
                        {cell("at_risk", m.figures.at_risk)}
                        {cell("expiring", m.figures.expiring)}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
