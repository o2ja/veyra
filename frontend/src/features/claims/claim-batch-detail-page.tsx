import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Alert, Button, Card, ErrorAlert, Field, Loading, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { DataTable, PagerControls, SelectField, usePager, utc } from "../masterdata/common";
import {
  ClaimsNav,
  ConfirmDialog,
  FileDownloadButton,
  ValidationDetailsAlert,
  useManufacturerMap,
} from "./common";
import {
  ADJUSTMENT_TYPE_LABELS,
  BATCH_STATUS_LABELS,
  canApproveBatch,
  formatDecimal,
  getJobRefetchInterval,
  type AdjustmentOut,
  type ExportAccepted,
  type ExportTemplateOut,
  type JobOut,
  type LineOut,
} from "./logic";

export function ClaimBatchDetailPage() {
  const { batchId = "" } = useParams();
  const { can, me } = useSession();
  const client = useQueryClient();
  const pager = usePager();
  const adjPager = usePager();
  const manufacturers = useManufacturerMap();

  const [approveOpen, setApproveOpen] = useState(false);
  const [voidOpen, setVoidOpen] = useState(false);
  const [voidReason, setVoidReason] = useState("");
  const [submitOpen, setSubmitOpen] = useState(false);
  // The server rejects a submission date later than today in the tenant business timezone, so default to that
  // day (not the UTC or browser day, which can already be tomorrow).
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => unwrap(api.GET("/api/v1/settings")) });
  const [submittedAtEdit, setSubmittedAt] = useState("");
  const submittedAt =
    submittedAtEdit ||
    (settings.data
      ? new Intl.DateTimeFormat("en-CA", { timeZone: settings.data.business_timezone }).format(new Date())
      : "");
  const [externalReference, setExternalReference] = useState("");
  const [exportOpen, setExportOpen] = useState(false);
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [exportAccepted, setExportAccepted] = useState<ExportAccepted | null>(null);
  const [pollingJobId, setPollingJobId] = useState<string | null>(null);
  const [jobResult, setJobResult] = useState<JobOut | null>(null);

  const [submitIdemKey, setSubmitIdemKey] = useState<string>(() => crypto.randomUUID());

  const batch = useQuery({
    queryKey: ["claim-batches", batchId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/claim-batches/{batch_id}", {
          params: { path: { batch_id: batchId } },
        }),
      ),
  });

  const lines = useQuery({
    queryKey: ["claim-batches", batchId, "lines", pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/claim-batches/{batch_id}/lines", {
          params: {
            path: { batch_id: batchId },
            query: { limit: 50, cursor: pager.cursor },
          },
        }),
      ),
  });

  const adjustments = useQuery({
    queryKey: ["claim-adjustments", "batch", batchId, adjPager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/claim-adjustments", {
          params: {
            query: {
              included_in_batch_id: batchId,
              limit: 50,
              cursor: adjPager.cursor,
            },
          },
        }),
      ),
  });

  const templates = useQuery({
    queryKey: ["claim-export-templates", batch.data?.manufacturer_id],
    enabled: Boolean(batch.data?.manufacturer_id) && exportOpen,
    queryFn: () => {
      const mid = batch.data?.manufacturer_id;
      if (!mid) throw new Error("Missing manufacturer id");
      return unwrap(
        api.GET("/api/v1/claim-export-templates", {
          params: {
            query: {
              manufacturer_id: mid,
              active: true,
              limit: 100,
            },
          },
        }),
      );
    },
  });

  const jobQuery = useQuery({
    queryKey: ["jobs", pollingJobId],
    enabled: Boolean(pollingJobId),
    queryFn: () => {
      if (!pollingJobId) throw new Error("Missing job id");
      return unwrap(
        api.GET("/api/v1/jobs/{job_id}", {
          params: { path: { job_id: pollingJobId } },
        }),
      );
    },
    refetchInterval: (query) => {
      const st = query.state.data?.status;
      return getJobRefetchInterval(st);
    },
  });

  useEffect(() => {
    const job = jobQuery.data;
    if (!job || !pollingJobId) return;

    if (job.status === "succeeded") {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setJobResult(job);
      setPollingJobId(null);
      void client.invalidateQueries({ queryKey: ["claim-batches", batchId] });
      void client.invalidateQueries({ queryKey: ["claim-batches"] });
    } else if (job.status === "failed") {
      setJobResult(job);
      setPollingJobId(null);
    }
  }, [jobQuery.data, pollingJobId, client, batchId]);

  const approve = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/claim-batches/{batch_id}/approve", {
          params: { path: { batch_id: batchId } },
        }),
      ),
    onSuccess: () => {
      setApproveOpen(false);
      void client.invalidateQueries({ queryKey: ["claim-batches", batchId] });
      void client.invalidateQueries({ queryKey: ["claim-batches"] });
    },
  });

  const voidBatch = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/claim-batches/{batch_id}/void", {
          params: { path: { batch_id: batchId } },
          body: { reason: voidReason.trim() },
        }),
      ),
    onSuccess: () => {
      setVoidOpen(false);
      setVoidReason("");
      void client.invalidateQueries({ queryKey: ["claim-batches", batchId] });
      void client.invalidateQueries({ queryKey: ["claim-batches"] });
      void client.invalidateQueries({ queryKey: ["claim-adjustments"] });
      void client.invalidateQueries({ queryKey: ["claim-adjustments", "batch", batchId] });
    },
  });

  const submitBatch = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/claim-batches/{batch_id}/submit", {
          params: {
            path: { batch_id: batchId },
            header: { "idempotency-key": submitIdemKey },
          },
          body: {
            submitted_at: submittedAt,
            external_reference: externalReference.trim(),
          },
        }),
      ),
    onSuccess: () => {
      setSubmitOpen(false);
      setSubmitIdemKey(crypto.randomUUID());
      void client.invalidateQueries({ queryKey: ["claim-batches", batchId] });
      void client.invalidateQueries({ queryKey: ["claim-batches"] });
      void client.invalidateQueries({ queryKey: ["claim-batches", batchId, "lines"] });
    },
  });

  const exportBatch = useMutation({
    mutationFn: async () => {
      const res = await unwrap(
        api.POST("/api/v1/claim-batches/{batch_id}/export", {
          params: { path: { batch_id: batchId } },
          body: { template_id: selectedTemplateId || null },
        }),
      );
      return res as unknown as ExportAccepted;
    },
    onSuccess: (data) => {
      setExportAccepted(data);
      setPollingJobId(data.job.id);
      setJobResult(null);
      setExportOpen(false);
    },
  });

  const batchErr = batch.error as ApiError | null;
  const linesErr = lines.error as ApiError | null;
  const adjustmentsErr = adjustments.error as ApiError | null;
  const approveErr = approve.error as ApiError | null;
  const voidErr = voidBatch.error as ApiError | null;
  const submitErr = submitBatch.error as ApiError | null;
  const exportErr = exportBatch.error as ApiError | null;

  if (batchErr?.status === 403 || batchErr?.code === "forbidden") {
    return (
      <>
        <ClaimsNav />
        <PermissionDenied />
      </>
    );
  }

  if (batchErr?.status === 404 || batchErr?.code === "not_found") {
    return (
      <>
        <ClaimsNav />
        <Card title="Claim batch not found">
          <Alert>This claim batch could not be found.</Alert>
          <div className="mt-4">
            <Link to="/claims/batches" className="text-brand underline">
              Back to batches
            </Link>
          </div>
        </Card>
      </>
    );
  }

  const b = batch.data;
  const m = b ? manufacturers.get(b.manufacturer_id) : undefined;
  const approveCheck = b ? canApproveBatch(b, me?.user.id, can("claim_batch.approve")) : { allowed: false };
  const canVoid = b && (b.status === "draft" || b.status === "approved") && can("claim_batch.void");
  const canExport = b && b.status === "approved" && can("claim_batch.create");
  const canSubmit = b && b.status === "approved" && can("claim_batch.submit");

  const currentJob = jobResult ?? jobQuery.data;
  const jobStatus = currentJob?.status ?? (pollingJobId ? "queued" : undefined);
  const isJobSucceeded = jobStatus === "succeeded";
  const isJobFailed = jobStatus === "failed";
  const jobError = currentJob?.last_error ?? (currentJob as { error?: string } | undefined)?.error;

  return (
    <>
      <ClaimsNav />
      <div className="flex flex-col gap-6">
        <Card
          title={b ? `Claim batch ${b.batch_number}` : "Claim batch"}
          actions={
            b && (
              <div className="flex flex-wrap gap-2">
                {b.status === "draft" && (
                  <Button
                    disabled={!approveCheck.allowed || approve.isPending}
                    title={approveCheck.reason}
                    onClick={() => setApproveOpen(true)}
                  >
                    Approve batch
                  </Button>
                )}
                {canExport && (
                  <Button
                    variant="secondary"
                    disabled={exportBatch.isPending}
                    onClick={() => setExportOpen(true)}
                  >
                    Export batch
                  </Button>
                )}
                {canSubmit && <Button onClick={() => setSubmitOpen(true)}>Submit batch</Button>}
                {canVoid && (
                  <Button variant="danger" onClick={() => setVoidOpen(true)}>
                    Void batch
                  </Button>
                )}
              </div>
            )
          }
        >
          <ErrorAlert error={batchErr} />
          {b && approveCheck.reason && b.status === "draft" && (
            <div className="mb-4">
              <Alert tone="info">{approveCheck.reason}</Alert>
            </div>
          )}

          {b && (
            <div className="flex flex-col gap-6">
              <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
                <div>
                  <dt className="text-muted">Batch number</dt>
                  <dd className="font-medium">{b.batch_number}</dd>
                </div>
                <div>
                  <dt className="text-muted">Status</dt>
                  <dd className="font-medium">
                    <span
                      className={
                        b.status === "void" ? "text-danger" : b.status === "submitted" ? "text-ok" : ""
                      }
                    >
                      {BATCH_STATUS_LABELS[b.status] ?? b.status}
                    </span>
                  </dd>
                </div>
                <div>
                  <dt className="text-muted">Manufacturer</dt>
                  <dd className="font-medium">{m ? `${m.name} (${m.code})` : b.manufacturer_id}</dd>
                </div>
                <div>
                  <dt className="text-muted">Batch total (lines + carried adjustments, from server)</dt>
                  <dd className="font-mono text-lg font-semibold text-ok">{formatDecimal(b.total_amount)}</dd>
                </div>
                <div>
                  <dt className="text-muted">Line count</dt>
                  <dd className="font-mono">{b.line_count}</dd>
                </div>
                <div>
                  <dt className="text-muted">Period</dt>
                  <dd>{b.period_from && b.period_to ? `${b.period_from} – ${b.period_to}` : "—"}</dd>
                </div>
                <div>
                  <dt className="text-muted">Created</dt>
                  <dd>{utc(b.created_at)}</dd>
                </div>
                <div>
                  <dt className="text-muted">Approved</dt>
                  <dd>{b.approved_at ? utc(b.approved_at) : "—"}</dd>
                </div>
                <div>
                  <dt className="text-muted">Submitted</dt>
                  <dd>{b.submitted_at ? utc(b.submitted_at) : "—"}</dd>
                </div>
                <div>
                  <dt className="text-muted">External reference</dt>
                  <dd className="font-mono">{b.external_reference ?? "—"}</dd>
                </div>
                {b.exported_file_id && (
                  <div>
                    <dt className="text-muted">Exported file</dt>
                    <dd className="mt-1">
                      <FileDownloadButton fileId={b.exported_file_id} label="Download exported claim" />
                    </dd>
                  </div>
                )}
                {b.void_reason && (
                  <div className="sm:col-span-2">
                    <dt className="text-muted">Void reason ({utc(b.voided_at)})</dt>
                    <dd className="font-medium text-danger">{b.void_reason}</dd>
                  </div>
                )}
              </dl>

              {exportAccepted && (
                <div className="rounded-md border border-line bg-surface/50 p-4 text-sm">
                  <h2 className="font-semibold">Batch export</h2>
                  <p className="mt-1">
                    Job status: <span className="font-medium capitalize">{jobStatus ?? "Queued"}</span>
                  </p>
                  {isJobFailed && (
                    <div role="alert" className="mt-2">
                      <Alert tone="danger">
                        Export failed: {jobError || "The export job encountered an error."}
                      </Alert>
                    </div>
                  )}
                  {isJobSucceeded && (
                    <div className="mt-2">
                      <FileDownloadButton fileId={exportAccepted.file_id} label="Download generated export" />
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </Card>

        <Card title={`Batch lines (${b?.line_count ?? 0})`}>
          <DataTable
            caption="Batch claim lines"
            loading={lines.isPending}
            error={linesErr}
            rows={lines.data?.data}
            empty="No lines in this batch."
            columns={[
              {
                header: "Claim reference",
                cell: (l: LineOut) => (
                  <span className="font-mono text-xs font-medium">{l.claim_reference}</span>
                ),
              },
              {
                header: "Document / line",
                cell: (l) => (l.document_number ? `${l.document_number} #${l.line_number ?? 1}` : "—"),
              },
              { header: "Invoice date", cell: (l) => l.invoice_date ?? "—" },
              {
                header: "Quantity",
                cell: (l) => <span className="block text-right font-mono">{formatDecimal(l.quantity)}</span>,
              },
              {
                header: "Unit amount",
                cell: (l) => (
                  <span className="block text-right font-mono">{formatDecimal(l.unit_amount)}</span>
                ),
              },
              {
                header: "Claim amount",
                cell: (l) => (
                  <span className="block text-right font-mono font-medium">
                    {formatDecimal(l.claim_amount)}
                  </span>
                ),
              },
              {
                header: "Status",
                cell: (l) => {
                  if (l.frozen_at) {
                    return (
                      <span className="rounded border border-line px-1.5 py-0.5 text-xs font-medium text-ok">
                        Frozen ({utc(l.frozen_at)})
                      </span>
                    );
                  }
                  if (l.released_at) {
                    return (
                      <span className="rounded border border-line px-1.5 py-0.5 text-xs text-muted">
                        Released ({utc(l.released_at)})
                      </span>
                    );
                  }
                  return <span className="text-xs text-muted">Active</span>;
                },
              },
              {
                header: "Source",
                cell: (l: LineOut) => (l.source_kind === "rebate_accrual" ? "Rebate accrual" : "Sales line"),
              },
              {
                header: "Lineage",
                cell: (l: LineOut) =>
                  l.calculation_result_id ? (
                    <Link
                      to={`/calculations/results/${l.calculation_result_id}`}
                      className="font-mono text-xs text-brand underline"
                    >
                      View result
                    </Link>
                  ) : l.sales_document_line_id ? (
                    <Link
                      to={`/lines/sales/${l.sales_document_line_id}`}
                      className="font-mono text-xs text-brand underline"
                    >
                      View sales line
                    </Link>
                  ) : (
                    "—"
                  ),
              },
            ]}
          />
          <PagerControls pager={pager} page={lines.data?.page} />
        </Card>

        <Card title="Carried adjustments">
          <DataTable
            caption="Carried adjustments"
            loading={adjustments.isPending}
            error={adjustmentsErr}
            rows={adjustments.data?.data}
            empty="No adjustments carried in this batch."
            columns={[
              {
                header: "Type",
                cell: (a: AdjustmentOut) => ADJUSTMENT_TYPE_LABELS[a.adjustment_type] ?? a.adjustment_type,
              },
              {
                header: "Reference",
                cell: (a: AdjustmentOut) => (
                  <span className="font-mono text-xs font-medium">{a.claim_reference ?? "—"}</span>
                ),
              },
              {
                header: "Signed amount",
                cell: (a: AdjustmentOut) => (
                  <span className="block text-right font-mono font-medium">{formatDecimal(a.amount)}</span>
                ),
              },
              {
                header: "Reason",
                cell: (a: AdjustmentOut) => a.reason,
              },
            ]}
          />
          <PagerControls pager={adjPager} page={adjustments.data?.page} />
        </Card>

        <div>
          <Link to="/claims/batches" className="text-brand underline">
            Back to batches
          </Link>
        </div>

        {/* Approve Confirm Dialog */}
        <ConfirmDialog
          open={approveOpen}
          onOpenChange={setApproveOpen}
          title={`Approve claim batch ${b?.batch_number ?? ""}`}
          description={`Are you sure you want to approve this batch (${b?.line_count ?? 0} lines, ${formatDecimal(
            b?.total_amount,
          )} total)? After approval, the batch can be exported and submitted.`}
          confirmLabel="Approve batch"
          busy={approve.isPending}
          onConfirm={() => approve.mutate()}
        >
          <ValidationDetailsAlert error={approveErr} />
        </ConfirmDialog>

        {/* Void Confirm Dialog */}
        <ConfirmDialog
          open={voidOpen}
          onOpenChange={(open) => {
            setVoidOpen(open);
            if (!open) setVoidReason("");
          }}
          title={`Void claim batch ${b?.batch_number ?? ""}`}
          description="Voiding this batch will release its lines back to the claimable pool. This action cannot be undone."
          confirmLabel="Confirm void"
          confirmVariant="danger"
          busy={voidBatch.isPending}
          onConfirm={() => voidBatch.mutate()}
        >
          <div className="flex flex-col gap-3">
            <Field
              label="Void reason"
              required
              placeholder="Explain why this batch is being voided"
              value={voidReason}
              onChange={(e) => setVoidReason(e.target.value)}
            />
            <ValidationDetailsAlert error={voidErr} />
          </div>
        </ConfirmDialog>

        {/* Export Dialog */}
        <ConfirmDialog
          open={exportOpen}
          onOpenChange={setExportOpen}
          title={`Export claim batch ${b?.batch_number ?? ""}`}
          description="Choose an export template or use the default format."
          confirmLabel="Start export"
          busy={exportBatch.isPending}
          onConfirm={() => exportBatch.mutate()}
        >
          <div className="flex flex-col gap-3">
            <SelectField
              label="Export template"
              value={selectedTemplateId}
              onChange={(e) => setSelectedTemplateId(e.target.value)}
            >
              <option value="">Default format (server standard)</option>
              {templates.data?.data.map((t: ExportTemplateOut) => (
                <option key={t.id} value={t.id}>
                  {t.name} ({t.format.toUpperCase()})
                </option>
              ))}
            </SelectField>
            {templates.isPending && <Loading label="Loading templates…" />}
            <ValidationDetailsAlert error={exportErr} />
          </div>
        </ConfirmDialog>

        {/* Submit Dialog */}
        <ConfirmDialog
          open={submitOpen}
          onOpenChange={setSubmitOpen}
          title={`Submit claim batch ${b?.batch_number ?? ""}`}
          description="Submitting this batch freezes its lines permanently. Make sure the manufacturer portal reference is correct."
          confirmLabel="Confirm submission"
          busy={submitBatch.isPending}
          onConfirm={() => submitBatch.mutate()}
        >
          <div className="flex flex-col gap-3">
            <Field
              label="Submitted date"
              type="date"
              required
              value={submittedAt}
              onChange={(e) => setSubmittedAt(e.target.value)}
            />
            <Field
              label="External reference"
              required
              placeholder="e.g. Portal confirmation #, claim tracking ID"
              value={externalReference}
              onChange={(e) => setExternalReference(e.target.value)}
            />
            <p className="text-xs text-muted">
              Note: The server will verify that the submission date is not in the future.
            </p>
            <ValidationDetailsAlert error={submitErr} />
          </div>
        </ConfirmDialog>
      </div>
    </>
  );
}
