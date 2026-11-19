import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { Alert, Card, ErrorAlert, Loading, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError } from "../../lib/api/errors";
import { unwrap } from "../../lib/api/errors";
import { CalculationsNav, KeyValueGrid, TraceTree } from "./common";
import { RESULT_STATUS_LABELS } from "./logic";
import { formatDate, formatMoney, formatQty } from "../../lib/format";

export function ResultDetailPage() {
  const { resultId = "" } = useParams();

  const detail = useQuery({
    queryKey: ["calculation-results", resultId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/calculation-results/{result_id}", {
          params: { path: { result_id: resultId } },
        }),
      ),
  });

  const err = detail.error as ApiError | null;

  if (err?.status === 403 || err?.code === "forbidden") {
    return (
      <>
        <CalculationsNav />
        <PermissionDenied />
      </>
    );
  }

  if (err?.status === 404 || err?.code === "not_found") {
    return (
      <>
        <CalculationsNav />
        <Card title="Calculation result not found">
          <Alert>This calculation result could not be found.</Alert>
          <div className="mt-4">
            <Link to="/calculations" className="text-brand underline">
              Back to runs
            </Link>
          </div>
        </Card>
      </>
    );
  }

  return (
    <>
      <CalculationsNav />
      <div className="flex flex-col gap-6">
        <Card
          title={
            detail.data
              ? `Result · ${detail.data.document_number} (line ${detail.data.line_number})`
              : "Calculation result"
          }
        >
          <ErrorAlert error={err} />
          {detail.isPending ? (
            <Loading />
          ) : (
            detail.data && (
              <div className="flex flex-col gap-6">
                <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
                  <div>
                    <dt className="text-muted">Status</dt>
                    <dd className="font-medium">
                      {RESULT_STATUS_LABELS[detail.data.status] ?? detail.data.status}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted">Invoice date</dt>
                    <dd className="font-medium">{formatDate(detail.data.invoice_date)}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Exception code</dt>
                    <dd className="font-mono text-xs">{detail.data.exception_code ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Claim UoM</dt>
                    <dd className="font-medium">{detail.data.claim_uom ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Claim amount</dt>
                    <dd className="font-mono font-medium">{formatMoney(detail.data.claim_amount)}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Claim unit amount</dt>
                    <dd className="font-mono">{formatMoney(detail.data.claim_unit_amount)}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Eligible quantity</dt>
                    <dd className="font-mono">{formatQty(detail.data.eligible_quantity)}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Excess quantity</dt>
                    <dd className="font-mono">{formatQty(detail.data.excess_quantity)}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Flags</dt>
                    <dd>{detail.data.flags.length ? detail.data.flags.join(", ") : "—"}</dd>
                  </div>
                  <div className="sm:col-span-2 lg:col-span-3">
                    <dt className="text-muted">Inputs hash</dt>
                    <dd className="font-mono text-xs break-all">{detail.data.inputs_hash}</dd>
                  </div>
                </dl>

                <div className="flex flex-wrap gap-4 text-sm">
                  <Link
                    to={`/lines/sales/${detail.data.sales_document_line_id}`}
                    className="text-brand underline"
                  >
                    Open source sales line
                  </Link>
                  {detail.data.agreement_version_id && (
                    <Link
                      to={`/agreements/versions/${detail.data.agreement_version_id}`}
                      className="text-brand underline"
                    >
                      Open agreement version
                    </Link>
                  )}
                  <Link to={`/calculations/${detail.data.run_id}`} className="text-brand underline">
                    Back to run
                  </Link>
                </div>
              </div>
            )
          )}
        </Card>

        {detail.data && (
          <Card title="Trace references">
            <KeyValueGrid data={detail.data.trace_ref} empty="No trace references." />
          </Card>
        )}

        {detail.data && (
          <Card title="Explain this number">
            <TraceTree trace={detail.data.trace} />
          </Card>
        )}
      </div>
    </>
  );
}
