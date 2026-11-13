import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { Card, Empty, ErrorAlert, Loading, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { ReportingNav } from "./common";
import { formatFields, isTraceKind, type TraceNode, type TraceOut } from "./logic";

export function TracePage() {
  const { kind = "", id = "" } = useParams();

  const isKnownKind = isTraceKind(kind);

  const traceQuery = useQuery({
    queryKey: ["trace", kind, id],
    enabled: Boolean(isKnownKind && id),
    queryFn: async (): Promise<TraceOut> => {
      switch (kind) {
        case "claim_line":
          return unwrap(
            api.GET("/api/v1/trace/claim_line/{claim_line_id}", {
              params: { path: { claim_line_id: id } },
            }),
          );
        case "claim_adjustment":
          return unwrap(
            api.GET("/api/v1/trace/claim_adjustment/{adjustment_id}", {
              params: { path: { adjustment_id: id } },
            }),
          );
        case "remittance_line":
          return unwrap(
            api.GET("/api/v1/trace/remittance_line/{remittance_line_id}", {
              params: { path: { remittance_line_id: id } },
            }),
          );
        case "variance_case":
          return unwrap(
            api.GET("/api/v1/trace/variance_case/{case_id}", {
              params: { path: { case_id: id } },
            }),
          );
        case "calculation_result":
          return unwrap(
            api.GET("/api/v1/trace/calculation_result/{result_id}", {
              params: { path: { result_id: id } },
            }),
          );
        default:
          throw new Error(`Unknown trace kind: ${kind}`);
      }
    },
  });

  if (!isKnownKind) {
    return (
      <>
        <ReportingNav />
        <Card title="Trace not found">
          <Empty>
            {`Unknown trace kind: "${kind}". Expected one of: claim_line, claim_adjustment, remittance_line, variance_case, calculation_result.`}
          </Empty>
          <div className="mt-4">
            <Link to="/dashboard" className="text-sm underline hover:text-ink">
              ← Return to dashboard
            </Link>
          </div>
        </Card>
      </>
    );
  }

  const queryErr = traceQuery.error as ApiError | null;
  if (queryErr?.status === 403 || queryErr?.code === "forbidden") {
    return (
      <>
        <ReportingNav />
        <PermissionDenied />
      </>
    );
  }

  if (queryErr?.status === 404) {
    return (
      <>
        <ReportingNav />
        <Card title="Record not found">
          <Empty>The requested record could not be found or belongs to another workspace.</Empty>
          <div className="mt-4">
            <Link to="/dashboard" className="text-sm underline hover:text-ink">
              ← Return to dashboard
            </Link>
          </div>
        </Card>
      </>
    );
  }

  if (traceQuery.isPending) {
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

  const trace = traceQuery.data;
  const chain = trace?.chain ?? [];

  return (
    <>
      <ReportingNav />

      <div className="flex flex-col gap-6">
        <div>
          <Link
            to="/dashboard"
            className="text-muted text-xs underline-offset-2 hover:text-ink hover:underline"
          >
            ← Back to dashboard
          </Link>
          <h1 className="mt-1 font-semibold text-2xl text-ink">Trace resolver</h1>
          <p className="font-mono text-muted text-xs">
            {kind}: {id}
          </p>
        </div>

        <Card title={`Trace chain (${chain.length} nodes from record to source)`}>
          {chain.length === 0 ? (
            <Empty>No trace chain available for this record.</Empty>
          ) : (
            <ol className="relative space-y-6 border-l border-line pl-6">
              {chain.map((node: TraceNode, index: number) => {
                const canNavigate = Boolean(
                  node.id && isTraceKind(node.kind) && (node.kind !== kind || node.id !== id),
                );
                const isRebateAccrual = node.kind === "rebate_accrual";
                const fields = formatFields(node.fields);

                return (
                  <li key={`${node.kind}-${node.id ?? index}`} className="relative">
                    {/* Step marker indicator */}
                    <span
                      aria-hidden="true"
                      className="absolute -left-[31px] flex h-5 w-5 items-center justify-center rounded-full border border-line bg-surface text-[10px] font-mono font-medium text-muted"
                    >
                      {index + 1}
                    </span>

                    <div className="rounded-lg border border-line bg-panel p-4 shadow-sm">
                      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line pb-2">
                        <div className="flex items-center gap-2">
                          <h2 className="font-semibold text-sm text-ink">{node.label}</h2>
                          {canNavigate && (
                            <Link
                              to={`/trace/${node.kind}/${node.id}`}
                              className="text-xs text-muted underline hover:text-ink"
                            >
                              (view trace →)
                            </Link>
                          )}
                        </div>
                        <span className="rounded bg-surface px-2 py-0.5 font-mono text-xs text-muted">
                          {node.kind}
                        </span>
                      </div>

                      {/* Rebate accrual note */}
                      {isRebateAccrual && (
                        <div className="mt-2 rounded bg-surface/50 p-2 text-xs italic text-muted">
                          Aggregates many purchases: no single source row
                        </div>
                      )}

                      {/* Node fields */}
                      {fields.length > 0 ? (
                        <dl className="mt-3 grid grid-cols-1 gap-2 text-xs sm:grid-cols-2 lg:grid-cols-3">
                          {fields.map((f, i) => (
                            <div key={i} className="flex flex-col">
                              <dt className="capitalize text-muted">{f.key}</dt>
                              <dd className="font-mono text-ink break-all">{f.value}</dd>
                            </div>
                          ))}
                        </dl>
                      ) : (
                        <p className="mt-2 text-xs text-muted">No additional fields recorded.</p>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </Card>
      </div>
    </>
  );
}
