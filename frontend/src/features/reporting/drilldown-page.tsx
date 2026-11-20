import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { Alert, Button, Card, ErrorAlert, Loading, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { DataTable, PagerControls, usePager } from "../masterdata/common";
import { ReportingNav, useBranchMap, useManufacturerMap } from "./common";
import { METRIC_LABELS, type DrillRow, type MetricKey } from "./logic";
import { formatDateTime, formatMoney } from "../../lib/format";

export function DrilldownPage() {
  const { metric = "" } = useParams();
  const { can } = useSession();
  const client = useQueryClient();
  const pager = usePager();
  const [searchParams] = useSearchParams();

  const manufacturerId = searchParams.get("manufacturer_id") ?? "";
  const branchId = searchParams.get("branch_id") ?? "";
  const monthParam = searchParams.get("month") ?? "";

  const manufacturers = useManufacturerMap();
  const branches = useBranchMap();

  const metricLabel = METRIC_LABELS[metric] ?? metric;

  const drilldownQuery = useQuery({
    queryKey: ["dashboard-drilldown", metric, manufacturerId, branchId, monthParam, pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/dashboard/drilldown", {
          params: {
            query: {
              metric: metric as MetricKey,
              ...(manufacturerId ? { manufacturer_id: manufacturerId } : {}),
              ...(branchId ? { branch_id: branchId } : {}),
              ...(monthParam ? { month: monthParam } : {}),
              limit: 50,
              cursor: pager.cursor,
            },
          },
        }),
      ),
  });

  const refreshMutation = useMutation({
    mutationFn: async () => {
      return unwrap(api.POST("/api/v1/dashboard/refresh"));
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["dashboard-drilldown"] });
      void client.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  const queryErr = drilldownQuery.error as ApiError | null;
  if (queryErr?.status === 403 || queryErr?.code === "forbidden") {
    return (
      <>
        <ReportingNav />
        <PermissionDenied />
      </>
    );
  }

  if (drilldownQuery.isPending) {
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

  const d = drilldownQuery.data;

  // Back link to dashboard with current filters
  const dashboardBackUrl = `/dashboard${searchParams.toString() ? `?${searchParams.toString()}` : ""}`;

  return (
    <>
      <ReportingNav />

      <div className="flex flex-col gap-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <Link
                to={dashboardBackUrl}
                className="text-muted text-xs underline-offset-2 hover:text-ink hover:underline"
              >
                ← Back to dashboard
              </Link>
            </div>
            <h1 className="mt-1 font-semibold text-2xl text-ink">Drill-down: {metricLabel}</h1>
            {d?.as_of && (
              <p className="text-muted text-xs">
                As of <span className="font-mono">{formatDateTime(d.as_of)}</span>
              </p>
            )}
          </div>

          {can("report.refresh") && (
            <Button
              variant="secondary"
              disabled={refreshMutation.isPending}
              onClick={() => refreshMutation.mutate()}
            >
              {refreshMutation.isPending ? "Refreshing…" : "Refresh figures"}
            </Button>
          )}
        </div>

        {refreshMutation.error && <ErrorAlert error={refreshMutation.error as ApiError} />}

        {/* Consistency warning if consistent === false */}
        {d && d.consistent === false && (
          <Alert tone="info">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span>Data changed since the last refresh</span>
              {can("report.refresh") && (
                <Button
                  variant="secondary"
                  disabled={refreshMutation.isPending}
                  onClick={() => refreshMutation.mutate()}
                >
                  Refresh now
                </Button>
              )}
            </div>
          </Alert>
        )}

        {/* Server figure & total summary */}
        {d && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Card title="Metric figure">
              <p className="font-mono font-semibold text-3xl text-ink">{formatMoney(d.figure)}</p>
              <p className="mt-1 text-muted text-xs">Calculated figure for this filter selection</p>
            </Card>

            <Card title="Drill-down total">
              <p className="font-mono font-semibold text-3xl text-ink">{formatMoney(d.total)}</p>
              <p className="mt-1 text-muted text-xs">Total of drill-down line items</p>
            </Card>
          </div>
        )}

        {/* Drill-down rows table */}
        <Card title={`Line items (${d?.data.length ?? 0} displayed)`}>
          <DataTable<DrillRow>
            caption={`${metricLabel} drill-down items`}
            loading={drilldownQuery.isPending}
            error={drilldownQuery.error}
            rows={d?.data}
            empty="No drill-down items found for this metric and filter set."
            columns={[
              {
                header: "Label",
                cell: (row) => <span className="font-medium text-ink">{row.label}</span>,
              },
              {
                header: "Month",
                cell: (row) => <span className="font-mono text-xs">{row.month}</span>,
              },
              {
                header: "Manufacturer",
                cell: (row) => (
                  <span className="text-xs">
                    {manufacturers.get(row.manufacturer_id) ?? row.manufacturer_id}
                  </span>
                ),
              },
              {
                header: "Branch",
                cell: (row) => (
                  <span className="text-xs">
                    {row.branch_id ? (branches.get(row.branch_id) ?? row.branch_id) : "—"}
                  </span>
                ),
              },
              {
                header: "Amount",
                cell: (row) => (
                  <span className="block text-right font-mono font-medium">{formatMoney(row.amount)}</span>
                ),
              },
              {
                header: "Action",
                cell: (row) => (
                  <Link
                    to={`/trace/${row.trace_kind}/${row.trace_id}`}
                    className="inline-flex items-center text-xs font-medium text-ink underline-offset-2 hover:underline"
                  >
                    Trace →
                  </Link>
                ),
              },
            ]}
          />
          <PagerControls pager={pager} page={d?.page} />
        </Card>
      </div>
    </>
  );
}
