import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { Alert, Button, Card, Empty, ErrorAlert, Loading, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import {
  AgreementSelect,
  DataTable,
  ManufacturerSelect,
  PagerControls,
  SelectField,
  usePager,
} from "../masterdata/common";
import { CalculationsNav, ExceptionCell, KeyValueGrid, ValidationDetailsAlert } from "./common";
import {
  EXCEPTION_INFO,
  RESULT_STATUSES,
  exceptionLabel,
  isPlainObject,
  runSummary,
  RESULT_STATUS_LABELS,
  RUN_STATUS_LABELS,
  type AdjustmentOut,
  type ResultOut,
} from "./logic";
import { formatDate, formatDateTime, formatMoney, formatQty } from "../../lib/format";

export function RunDetailPage() {
  const { runId = "" } = useParams();
  const { can } = useSession();
  const pager = usePager();
  const [searchParams, setSearchParams] = useSearchParams();

  const statusParam = searchParams.get("status") ?? "";
  const statusFilter = RESULT_STATUSES.includes(statusParam as (typeof RESULT_STATUSES)[number])
    ? (statusParam as (typeof RESULT_STATUSES)[number])
    : "";
  const exceptionCode = searchParams.get("exception_code") ?? "";
  const manufacturerId = searchParams.get("manufacturer_id") ?? "";
  const agreementId = searchParams.get("agreement_id") ?? "";

  const run = useQuery({
    queryKey: ["calculation-runs", runId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/calculation-runs/{run_id}", { params: { path: { run_id: runId } } })),
    refetchInterval: (query) => {
      const st = query.state.data?.status;
      return st === "queued" || st === "running" ? 2000 : false;
    },
  });

  const runStatus = run.data?.status;
  const results = useQuery({
    queryKey: [
      "calculation-runs",
      runId,
      "results",
      runStatus,
      statusFilter,
      exceptionCode,
      manufacturerId,
      agreementId,
      pager.cursor,
    ],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/calculation-runs/{run_id}/results", {
          params: {
            path: { run_id: runId },
            query: {
              status: statusFilter || null,
              exception_code: exceptionCode || null,
              manufacturer_id: manufacturerId || null,
              agreement_id: agreementId || null,
              limit: 50,
              cursor: pager.cursor,
            },
          },
        }),
      ),
  });

  const propose = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/calculation-runs/{run_id}/propose-adjustments", {
          params: { path: { run_id: runId } },
        }),
      ),
  });

  const runErr = run.error as ApiError | null;
  const resultsErr = results.error as ApiError | null;
  const proposeErr = propose.error as ApiError | null;

  if (runErr?.status === 403 || runErr?.code === "forbidden") {
    return (
      <>
        <CalculationsNav />
        <PermissionDenied />
      </>
    );
  }

  if (runErr?.status === 404 || runErr?.code === "not_found") {
    return (
      <>
        <CalculationsNav />
        <Card title="Calculation run not found">
          <Alert>This calculation run could not be found.</Alert>
          <div className="mt-4">
            <Link to="/calculations" className="text-brand underline">
              Back to runs
            </Link>
          </div>
        </Card>
      </>
    );
  }

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    setSearchParams(next, { replace: true });
    pager.reset();
  }

  return (
    <>
      <CalculationsNav />
      <div className="flex flex-col gap-6">
        <Card
          title={run.data ? `Calculation run ${run.data.id.slice(0, 8)}` : "Calculation run"}
          actions={
            can("calculation.run") && run.data?.status === "completed" ? (
              <Button disabled={propose.isPending} onClick={() => propose.mutate()}>
                {propose.isPending ? "Proposing…" : "Propose adjustments"}
              </Button>
            ) : undefined
          }
        >
          <ErrorAlert error={runErr} />
          {run.isPending ? (
            <Loading />
          ) : (
            run.data && (
              <div className="flex flex-col gap-6">
                <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
                  <div>
                    <dt className="text-muted">Status</dt>
                    <dd className="font-medium">{RUN_STATUS_LABELS[run.data.status] ?? run.data.status}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Run date</dt>
                    <dd className="font-medium">{formatDate(run.data.run_date)}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">As of</dt>
                    <dd className="font-medium">{formatDateTime(run.data.as_of)}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Data cutoff</dt>
                    <dd className="font-medium">{formatDateTime(run.data.data_cutoff)}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Engine version</dt>
                    <dd className="font-mono text-xs">{run.data.engine_version}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Created</dt>
                    <dd>{formatDateTime(run.data.created_at)}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Started</dt>
                    <dd>{formatDateTime(run.data.started_at)}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Completed</dt>
                    <dd>{formatDateTime(run.data.completed_at)}</dd>
                  </div>
                </dl>

                <div>
                  <h2 className="mb-2 text-sm font-semibold">Scope</h2>
                  <KeyValueGrid data={run.data.scope} empty="No scope filters." />
                </div>

                <div>
                  <h2 className="mb-2 text-sm font-semibold">Result</h2>
                  <p className="text-sm">{runSummary(run.data.totals)}</p>
                  {isPlainObject(run.data.totals?.by_exception) &&
                    Object.keys(run.data.totals.by_exception).length > 0 && (
                      <ul className="mt-3 flex flex-col gap-1.5 text-sm">
                        {Object.entries(run.data.totals.by_exception).map(([code, count]) => (
                          <li key={code} className="flex gap-3">
                            <span className="w-10 text-right font-mono text-muted">{String(count)}</span>
                            <ExceptionCell code={code} />
                          </li>
                        ))}
                      </ul>
                    )}
                </div>

                {run.data.error && (
                  <div>
                    <h2 className="mb-2 text-sm font-semibold text-danger">Error</h2>
                    <KeyValueGrid data={run.data.error} />
                  </div>
                )}
              </div>
            )
          )}
        </Card>

        {(proposeErr || propose.data) && (
          <Card title="Proposed adjustments">
            <ErrorAlert error={proposeErr} />
            {propose.data && (
              <div className="flex flex-col gap-4">
                <p className="text-sm text-muted">
                  Compared {propose.data.compared} item(s); {propose.data.proposed.length} adjustment(s)
                  proposed.
                </p>
                {propose.data.proposed.length === 0 ? (
                  <Empty>No adjustments proposed.</Empty>
                ) : (
                  <DataTable
                    caption="Proposed adjustments"
                    loading={false}
                    error={null}
                    rows={propose.data.proposed}
                    empty="No adjustments proposed."
                    columns={[
                      { header: "Type", cell: (a: AdjustmentOut) => a.adjustment_type },
                      {
                        header: "Amount",
                        cell: (a) => (
                          <span className="block text-right font-mono">{formatMoney(a.amount)}</span>
                        ),
                      },
                      { header: "Status", cell: (a) => a.status },
                      { header: "Reason", cell: (a) => a.reason },
                      {
                        header: "Result",
                        cell: (a) =>
                          a.calculation_result_id ? (
                            <Link
                              to={`/calculations/results/${a.calculation_result_id}`}
                              className="font-mono text-xs text-brand underline"
                            >
                              {a.calculation_result_id.slice(0, 8)}
                            </Link>
                          ) : (
                            "—"
                          ),
                      },
                    ]}
                  />
                )}
              </div>
            )}
          </Card>
        )}

        <Card title="Results">
          <div className="mb-4 flex flex-col gap-3">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <SelectField
                label="Status"
                value={statusFilter}
                onChange={(e) => setFilter("status", e.target.value)}
              >
                <option value="">All statuses</option>
                {RESULT_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {RESULT_STATUS_LABELS[s] ?? s}
                  </option>
                ))}
              </SelectField>
              <SelectField
                label="Exception"
                value={exceptionCode}
                onChange={(e) => setFilter("exception_code", e.target.value)}
              >
                <option value="">All exceptions</option>
                {Object.keys(EXCEPTION_INFO).map((code) => (
                  <option key={code} value={code}>
                    {exceptionLabel(code)}
                  </option>
                ))}
              </SelectField>
              <ManufacturerSelect
                allowAny="All manufacturers"
                value={manufacturerId}
                onChange={(id) => setFilter("manufacturer_id", id)}
              />
              <AgreementSelect value={agreementId} onChange={(id) => setFilter("agreement_id", id)} />
            </div>
          </div>

          {resultsErr ? (
            <ValidationDetailsAlert error={resultsErr} />
          ) : (
            <DataTable
              caption="Calculation results"
              loading={results.isPending}
              error={null}
              rows={results.data?.data}
              empty={
                statusFilter || exceptionCode || manufacturerId || agreementId
                  ? "No matching results."
                  : "No results in this run."
              }
              columns={[
                {
                  header: "Document",
                  cell: (r: ResultOut) => (
                    <Link to={`/calculations/results/${r.id}`} className="text-brand underline">
                      {r.document_number}
                    </Link>
                  ),
                },
                { header: "Line", cell: (r) => r.line_number },
                { header: "Invoice date", cell: (r) => formatDate(r.invoice_date) },
                { header: "Status", cell: (r) => RESULT_STATUS_LABELS[r.status] ?? r.status },
                { header: "Exception", cell: (r) => <ExceptionCell code={r.exception_code} /> },
                {
                  header: "Claim amount",
                  cell: (r) => (
                    <span className="block text-right font-mono">{formatMoney(r.claim_amount)}</span>
                  ),
                },
                {
                  header: "Unit amount",
                  cell: (r) => (
                    <span className="block text-right font-mono">{formatMoney(r.claim_unit_amount)}</span>
                  ),
                },
                {
                  header: "Eligible qty",
                  cell: (r) => (
                    <span className="block text-right font-mono">{formatQty(r.eligible_quantity)}</span>
                  ),
                },
                {
                  header: "Excess qty",
                  cell: (r) => (
                    <span className="block text-right font-mono">{formatQty(r.excess_quantity)}</span>
                  ),
                },
                {
                  header: "Flags",
                  cell: (r) => (r.flags.length ? r.flags.join(", ") : "—"),
                },
              ]}
            />
          )}
          <PagerControls pager={pager} page={results.data?.page} />
        </Card>

        <div>
          <Link to="/calculations" className="text-brand underline">
            Back to runs
          </Link>
        </div>
      </div>
    </>
  );
}
