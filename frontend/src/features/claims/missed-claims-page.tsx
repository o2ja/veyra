import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { Alert, Button, Card, ErrorAlert, Field, Loading, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { DataTable, ManufacturerSelect, PagerControls, usePager } from "../masterdata/common";
import { ClaimsNav, ConfirmDialog, FileDownloadButton, ValidationDetailsAlert } from "./common";
import {
  ADJUSTMENT_TYPE_LABELS,
  getJobRefetchInterval,
  type AccrualOut,
  type AdjustmentOut,
  type BatchIn,
  type ExportAccepted,
  type JobOut,
  type MissedList,
  type MissedOut,
} from "./logic";
import { formatDate, formatMoney, formatQty } from "../../lib/format";

export function MissedClaimsPage() {
  const { can } = useSession();
  const navigate = useNavigate();
  const client = useQueryClient();
  const pager = usePager();
  const accrualPager = usePager();
  const [searchParams, setSearchParams] = useSearchParams();

  const manufacturerId = searchParams.get("manufacturer_id") ?? "";
  const agreementId = searchParams.get("agreement_id") ?? "";
  const invoiceFrom = searchParams.get("invoice_from") ?? "";
  const invoiceTo = searchParams.get("invoice_to") ?? "";

  const [mfgInput, setMfgInput] = useState(manufacturerId);
  const [agreeInput, setAgreeInput] = useState(agreementId);
  const [fromInput, setFromInput] = useState(invoiceFrom);
  const [toInput, setToInput] = useState(invoiceTo);

  const [builderMode, setBuilderMode] = useState<"missed" | "accruals">("missed");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [selectedAdjIds, setSelectedAdjIds] = useState<Set<string>>(new Set());
  const [selectedAccrualIds, setSelectedAccrualIds] = useState<Set<string>>(new Set());
  const [createBatchMode, setCreateBatchMode] = useState<"selected" | "filter" | "accruals" | null>(null);
  const [batchIdemKey] = useState<string>(() => crypto.randomUUID());
  const [exportAccepted, setExportAccepted] = useState<ExportAccepted | null>(null);
  const [pollingJobId, setPollingJobId] = useState<string | null>(null);
  const [jobResult, setJobResult] = useState<JobOut | null>(null);

  const summary = useQuery({
    queryKey: ["missed-claims", "summary", manufacturerId, agreementId, invoiceFrom, invoiceTo],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/missed-claims/summary", {
          params: {
            query: {
              manufacturer_id: manufacturerId || null,
              agreement_id: agreementId || null,
              invoice_from: invoiceFrom || null,
              invoice_to: invoiceTo || null,
            },
          },
        }),
      ),
  });

  const list = useQuery({
    queryKey: ["missed-claims", "list", manufacturerId, agreementId, invoiceFrom, invoiceTo, pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/missed-claims", {
          params: {
            query: {
              manufacturer_id: manufacturerId || null,
              agreement_id: agreementId || null,
              invoice_from: invoiceFrom || null,
              invoice_to: invoiceTo || null,
              limit: 50,
              cursor: pager.cursor,
            },
          },
        }),
      ),
  });

  const approvedAdjustments = useQuery({
    queryKey: ["claim-adjustments", "approved", manufacturerId],
    enabled: Boolean(manufacturerId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/claim-adjustments", {
          params: {
            query: {
              status: "approved",
              manufacturer_id: manufacturerId,
              limit: 100,
            },
          },
        }),
      ),
  });

  const accruals = useQuery({
    queryKey: ["rebate-accruals", agreementId, accrualPager.cursor],
    enabled: builderMode === "accruals",
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/rebate-accruals", {
          params: {
            query: {
              agreement_id: agreementId || null,
              limit: 50,
              cursor: accrualPager.cursor,
            },
          },
        }),
      ),
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
      void client.invalidateQueries({ queryKey: ["missed-claims"] });
    } else if (job.status === "failed") {
      setJobResult(job);
      setPollingJobId(null);
    }
  }, [jobQuery.data, pollingJobId, client]);

  const exportList = useMutation({
    mutationFn: async (format: "csv" | "xlsx") => {
      const res = await unwrap(
        api.GET("/api/v1/missed-claims", {
          params: {
            query: {
              format,
              manufacturer_id: manufacturerId || null,
              agreement_id: agreementId || null,
              invoice_from: invoiceFrom || null,
              invoice_to: invoiceTo || null,
            },
          },
        }),
      );
      return res as unknown as ExportAccepted;
    },
    onSuccess: (data) => {
      setExportAccepted(data);
      setPollingJobId(data.job.id);
      setJobResult(null);
    },
  });

  const createBatch = useMutation({
    mutationFn: async (mode: "selected" | "filter" | "accruals") => {
      if (!manufacturerId) {
        throw new Error("A manufacturer must be selected to create a claim batch.");
      }
      let body: BatchIn;
      if (mode === "selected") {
        body = {
          manufacturer_id: manufacturerId,
          result_ids: Array.from(selectedIds),
          include_adjustment_ids: Array.from(selectedAdjIds),
        };
      } else if (mode === "filter") {
        body = {
          manufacturer_id: manufacturerId,
          filter: {
            agreement_id: agreementId || null,
            invoice_from: invoiceFrom || null,
            invoice_to: invoiceTo || null,
          },
          include_adjustment_ids: Array.from(selectedAdjIds),
        };
      } else {
        body = {
          manufacturer_id: manufacturerId,
          accrual_entry_ids: Array.from(selectedAccrualIds),
          include_adjustment_ids: Array.from(selectedAdjIds),
        };
      }

      return unwrap(
        api.POST("/api/v1/claim-batches", {
          params: { header: { "idempotency-key": batchIdemKey } },
          body,
        }),
      );
    },
    onError: () => setCreateBatchMode(null),
    onSuccess: (batch) => {
      setCreateBatchMode(null);
      setSelectedIds(new Set());
      setSelectedAdjIds(new Set());
      setSelectedAccrualIds(new Set());
      navigate(`/claims/batches/${batch.id}`);
    },
  });

  function applyFilters(e: React.FormEvent) {
    e.preventDefault();
    const next = new URLSearchParams(searchParams);
    if (mfgInput) next.set("manufacturer_id", mfgInput);
    else next.delete("manufacturer_id");
    if (agreeInput.trim()) next.set("agreement_id", agreeInput.trim());
    else next.delete("agreement_id");
    if (fromInput.trim()) next.set("invoice_from", fromInput.trim());
    else next.delete("invoice_from");
    if (toInput.trim()) next.set("invoice_to", toInput.trim());
    else next.delete("invoice_to");

    setSearchParams(next, { replace: true });
    setSelectedIds(new Set());
    if (mfgInput !== manufacturerId) {
      setSelectedAdjIds(new Set());
      setSelectedAccrualIds(new Set());
    }
    pager.reset();
    accrualPager.reset();
  }

  function toggleSelectRow(id: string, checked: boolean) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function toggleSelectAll(checked: boolean, rows: MissedOut[] = []) {
    if (!checked) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(rows.map((r) => r.id)));
    }
  }

  const summaryErr = summary.error as ApiError | null;
  const listErr = list.error as ApiError | null;
  const createBatchErr = createBatch.error as ApiError | null;
  const exportErr = exportList.error as ApiError | null;

  if (summaryErr?.status === 403 || summaryErr?.code === "forbidden") {
    return (
      <>
        <ClaimsNav />
        <PermissionDenied />
      </>
    );
  }

  const listData = list.data as MissedList | undefined;
  const rows: MissedOut[] = listData?.data ?? [];
  const allOnPageSelected = rows.length > 0 && rows.every((r) => selectedIds.has(r.id));
  const currentJob = jobResult ?? jobQuery.data;
  const jobStatus = currentJob?.status ?? (pollingJobId ? "queued" : undefined);
  const isJobSucceeded = jobStatus === "succeeded";
  const isJobFailed = jobStatus === "failed";
  const jobError = currentJob?.last_error ?? (currentJob as { error?: string } | undefined)?.error;

  return (
    <>
      <ClaimsNav />
      <div className="flex flex-col gap-6">
        <Card title="Missed claims filters">
          <form className="flex flex-col gap-4" onSubmit={applyFilters}>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <ManufacturerSelect allowAny="All manufacturers" value={mfgInput} onChange={setMfgInput} />
              <Field
                label="Agreement ID"
                value={agreeInput}
                placeholder="UUID"
                onChange={(e) => setAgreeInput(e.target.value)}
              />
              <Field
                label="Invoice from"
                type="date"
                value={fromInput}
                onChange={(e) => setFromInput(e.target.value)}
              />
              <Field
                label="Invoice to"
                type="date"
                value={toInput}
                onChange={(e) => setToInput(e.target.value)}
              />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit">Apply filters</Button>
              <Button
                type="button"
                variant="secondary"
                disabled={exportList.isPending}
                onClick={() => exportList.mutate("csv")}
              >
                {exportList.isPending ? "Starting export…" : "Export list (CSV)"}
              </Button>
              <Button
                type="button"
                variant="secondary"
                disabled={exportList.isPending}
                onClick={() => exportList.mutate("xlsx")}
              >
                {exportList.isPending ? "Starting export…" : "Export list (XLSX)"}
              </Button>
            </div>
          </form>

          {exportErr && <ErrorAlert error={exportErr} />}
          {exportAccepted && (
            <div className="mt-4 rounded-md border border-line bg-surface/50 p-3 text-sm">
              <span className="font-medium">Export job:</span> Status:{" "}
              <span className="font-medium capitalize">{jobStatus ? jobStatus : "queued"}</span>
              {isJobFailed && (
                <div role="alert" className="mt-2">
                  <Alert tone="danger">
                    Export failed: {jobError || "The export job encountered an error."}
                  </Alert>
                </div>
              )}
              {isJobSucceeded && (
                <div className="mt-2">
                  <FileDownloadButton
                    fileId={exportAccepted.file_id}
                    label="Download exported missed claims"
                  />
                </div>
              )}
            </div>
          )}
        </Card>

        <Card title="Missed claims summary">
          <ErrorAlert error={summaryErr} />
          {summary.isPending ? (
            <Loading />
          ) : (
            summary.data && (
              <div className="flex flex-col gap-6">
                <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
                  <div className="rounded-lg border border-line p-3">
                    <dt className="text-muted">Total claimable lines</dt>
                    <dd className="font-mono text-lg font-semibold">{summary.data.totals.lines}</dd>
                  </div>
                  <div className="rounded-lg border border-line p-3">
                    <dt className="text-muted">Total claimable amount</dt>
                    <dd className="font-mono text-lg font-semibold text-ok">
                      {formatMoney(summary.data.totals.amount)}
                    </dd>
                  </div>
                  <div className="rounded-lg border border-line p-3">
                    <dt className="text-muted">Lost to expiry (lines)</dt>
                    <dd className="font-mono text-lg font-semibold text-danger">
                      {summary.data.lost_to_expiry.lines}
                    </dd>
                  </div>
                  <div className="rounded-lg border border-line p-3">
                    <dt className="text-muted">Lost to expiry (amount)</dt>
                    <dd className="font-mono text-lg font-semibold text-danger">
                      {formatMoney(summary.data.lost_to_expiry.amount)}
                    </dd>
                  </div>
                </dl>

                {summary.data.groups.length > 0 && (
                  <div>
                    <h2 className="mb-2 text-sm font-semibold">Groups by manufacturer & agreement</h2>
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-sm">
                        <caption className="sr-only">Summary groups</caption>
                        <thead className="text-muted">
                          <tr>
                            <th scope="col" className="py-2 pr-4 font-medium">
                              Manufacturer
                            </th>
                            <th scope="col" className="py-2 pr-4 font-medium">
                              Agreement
                            </th>
                            <th scope="col" className="py-2 pr-4 text-right font-medium">
                              Lines
                            </th>
                            <th scope="col" className="py-2 pr-4 text-right font-medium">
                              Amount
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {summary.data.groups.map((g, idx) => (
                            <tr
                              key={`${g.manufacturer_id}-${g.agreement_id}-${idx}`}
                              className="border-t border-line"
                            >
                              <td className="py-2 pr-4">
                                {g.manufacturer_name ?? g.manufacturer_code ?? "—"}
                              </td>
                              <td className="py-2 pr-4">{g.agreement_number ?? "—"}</td>
                              <td className="py-2 pr-4 text-right font-mono">{g.lines}</td>
                              <td className="py-2 pr-4 text-right font-mono font-medium">
                                {formatMoney(g.amount)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {summary.data.exceptions.length > 0 && (
                  <div>
                    <h2 className="mb-2 text-sm font-semibold">Exceptions summary</h2>
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-sm">
                        <caption className="sr-only">Exceptions summary</caption>
                        <thead className="text-muted">
                          <tr>
                            <th scope="col" className="py-2 pr-4 font-medium">
                              Exception code
                            </th>
                            <th scope="col" className="py-2 pr-4 text-right font-medium">
                              Lines
                            </th>
                            <th scope="col" className="py-2 pr-4 text-right font-medium">
                              Stored amount
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {summary.data.exceptions.map((ex, idx) => (
                            <tr key={`${ex.code}-${idx}`} className="border-t border-line">
                              <td className="py-2 pr-4 font-mono text-xs">{ex.code ?? "—"}</td>
                              <td className="py-2 pr-4 text-right font-mono">{ex.lines}</td>
                              <td className="py-2 pr-4 text-right font-mono font-medium">
                                {formatMoney(ex.stored_amount)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            )
          )}
        </Card>

        <div className="flex items-center gap-2">
          <Button
            type="button"
            aria-pressed={builderMode === "missed"}
            variant={builderMode === "missed" ? "primary" : "secondary"}
            onClick={() => setBuilderMode("missed")}
          >
            Missed sales claims
          </Button>
          <Button
            type="button"
            aria-pressed={builderMode === "accruals"}
            variant={builderMode === "accruals" ? "primary" : "secondary"}
            onClick={() => setBuilderMode("accruals")}
          >
            Claim rebate accruals
          </Button>
        </div>

        {manufacturerId && (
          <Card title="Adjustments to carry">
            <p className="mb-3 text-sm text-muted">
              Select approved adjustments to carry into this claim batch. The server will calculate the
              adjusted batch total.
            </p>
            <DataTable
              caption="Adjustments to carry"
              loading={approvedAdjustments.isPending}
              error={approvedAdjustments.error as ApiError | null}
              rows={approvedAdjustments.data?.data}
              empty="No approved adjustments to carry for this manufacturer."
              columns={[
                {
                  header: "Select",
                  cell: (a: AdjustmentOut) => (
                    <input
                      type="checkbox"
                      aria-label={`Select adjustment ${a.claim_reference ?? a.id}`}
                      checked={selectedAdjIds.has(a.id)}
                      onChange={(e) => {
                        const checked = e.target.checked;
                        setSelectedAdjIds((prev) => {
                          const next = new Set(prev);
                          if (checked) next.add(a.id);
                          else next.delete(a.id);
                          return next;
                        });
                      }}
                    />
                  ),
                },
                {
                  header: "Type",
                  cell: (a: AdjustmentOut) => ADJUSTMENT_TYPE_LABELS[a.adjustment_type] ?? a.adjustment_type,
                },
                {
                  header: "Claim reference",
                  cell: (a: AdjustmentOut) => (
                    <span className="font-mono text-xs font-medium">{a.claim_reference ?? "—"}</span>
                  ),
                },
                {
                  header: "Amount",
                  cell: (a: AdjustmentOut) => (
                    <span className="block text-right font-mono font-medium">{formatMoney(a.amount)}</span>
                  ),
                },
                {
                  header: "Reason",
                  cell: (a: AdjustmentOut) => a.reason,
                },
              ]}
            />
            {approvedAdjustments.data?.data && approvedAdjustments.data.data.length > 0 && (
              <div className="mt-2 flex items-center gap-2 text-xs">
                <Button
                  variant="secondary"
                  onClick={() => {
                    const adjRows = approvedAdjustments.data?.data ?? [];
                    const allSelected = adjRows.every((a) => selectedAdjIds.has(a.id));
                    if (allSelected) {
                      setSelectedAdjIds(new Set());
                    } else {
                      setSelectedAdjIds(new Set(adjRows.map((a) => a.id)));
                    }
                  }}
                >
                  {(approvedAdjustments.data?.data ?? []).every((a) => selectedAdjIds.has(a.id))
                    ? "Deselect all adjustments"
                    : "Select all adjustments"}
                </Button>
                {selectedAdjIds.size > 0 && (
                  <Button variant="secondary" onClick={() => setSelectedAdjIds(new Set())}>
                    Clear adjustments ({selectedAdjIds.size})
                  </Button>
                )}
              </div>
            )}
          </Card>
        )}

        {builderMode === "missed" ? (
          <Card
            title="Missed claim lines"
            actions={
              can("claim_batch.create") ? (
                <div className="flex flex-wrap gap-2">
                  <Button
                    disabled={!manufacturerId || selectedIds.size === 0}
                    onClick={() => setCreateBatchMode("selected")}
                  >
                    Create batch from selected ({selectedIds.size})
                  </Button>
                  <Button
                    variant="secondary"
                    disabled={!manufacturerId || (summary.data?.totals.lines ?? 0) === 0}
                    onClick={() => setCreateBatchMode("filter")}
                  >
                    Create batch from all matching filter
                  </Button>
                </div>
              ) : undefined
            }
          >
            {!manufacturerId && (
              <div className="mb-4">
                <Alert tone="info">
                  Select a manufacturer in the filter above to enable line selection and batch creation.
                </Alert>
              </div>
            )}

            <ValidationDetailsAlert error={createBatchErr} />

            <DataTable
              caption="Missed claim lines"
              loading={list.isPending}
              error={listErr}
              rows={rows}
              empty="No missed claims matching the filter."
              columns={[
                {
                  header: "Select",
                  cell: (r: MissedOut) =>
                    manufacturerId ? (
                      <input
                        type="checkbox"
                        aria-label={`Select ${r.document_number} line ${r.line_number}`}
                        checked={selectedIds.has(r.id)}
                        onChange={(e) => toggleSelectRow(r.id, e.target.checked)}
                      />
                    ) : null,
                },
                {
                  header: "Document",
                  cell: (r: MissedOut) => (
                    <Link to={`/calculations/results/${r.id}`} className="text-brand underline">
                      {r.document_number}
                    </Link>
                  ),
                },
                { header: "Line", cell: (r) => r.line_number },
                { header: "Invoice date", cell: (r) => formatDate(r.invoice_date) },
                {
                  header: "Eligible qty",
                  cell: (r) => (
                    <span className="block text-right font-mono">{formatQty(r.eligible_quantity)}</span>
                  ),
                },
                { header: "UoM", cell: (r) => r.claim_uom ?? "—" },
                {
                  header: "Unit amount",
                  cell: (r) => (
                    <span className="block text-right font-mono">{formatMoney(r.claim_unit_amount)}</span>
                  ),
                },
                {
                  header: "Claim amount",
                  cell: (r) => (
                    <span className="block text-right font-mono font-medium">
                      {formatMoney(r.claim_amount)}
                    </span>
                  ),
                },
                {
                  header: "Flags",
                  cell: (r) => (r.flags.length ? r.flags.join(", ") : "—"),
                },
              ]}
            />
            {manufacturerId && rows.length > 0 && (
              <div className="mt-2 flex items-center gap-2 text-xs">
                <Button variant="secondary" onClick={() => toggleSelectAll(!allOnPageSelected, rows)}>
                  {allOnPageSelected ? "Deselect page" : "Select all on page"}
                </Button>
                {selectedIds.size > 0 && (
                  <Button variant="secondary" onClick={() => setSelectedIds(new Set())}>
                    Clear selection
                  </Button>
                )}
              </div>
            )}
            <PagerControls pager={pager} page={listData?.page} />
          </Card>
        ) : (
          <Card
            title="Rebate accrual entries"
            actions={
              can("claim_batch.create") ? (
                <div className="flex flex-wrap gap-2">
                  <Button
                    disabled={!manufacturerId || selectedAccrualIds.size === 0}
                    onClick={() => setCreateBatchMode("accruals")}
                  >
                    Create batch from selected accruals ({selectedAccrualIds.size})
                  </Button>
                </div>
              ) : undefined
            }
          >
            {!manufacturerId && (
              <div className="mb-4">
                <Alert tone="info">
                  Select a manufacturer in the filter above to enable batch creation from accruals.
                </Alert>
              </div>
            )}

            <ValidationDetailsAlert error={createBatchErr} />

            <DataTable
              caption="Rebate accrual entries"
              loading={accruals.isPending}
              error={accruals.error as ApiError | null}
              rows={accruals.data?.data}
              empty="No rebate accruals matching the filter."
              columns={[
                {
                  header: "Select",
                  cell: (a: AccrualOut) =>
                    manufacturerId ? (
                      <input
                        type="checkbox"
                        aria-label={`Select accrual entry ${a.id}`}
                        checked={selectedAccrualIds.has(a.id)}
                        onChange={(e) => {
                          const checked = e.target.checked;
                          setSelectedAccrualIds((prev) => {
                            const next = new Set(prev);
                            if (checked) next.add(a.id);
                            else next.delete(a.id);
                            return next;
                          });
                        }}
                      />
                    ) : null,
                },
                {
                  header: "Agreement",
                  cell: (a: AccrualOut) => `${a.agreement_name} (${a.external_number})`,
                },
                {
                  header: "Period",
                  cell: (a: AccrualOut) => `${formatDate(a.period_start)} – ${formatDate(a.period_end)}`,
                },
                {
                  header: "Tier rate",
                  cell: (a: AccrualOut) => (a.tier_rate ? formatQty(a.tier_rate) : "—"),
                },
                {
                  header: "Delta",
                  cell: (a: AccrualOut) => (
                    <span className="block text-right font-mono font-medium">{formatMoney(a.delta)}</span>
                  ),
                },
                {
                  header: "Flags",
                  cell: (a: AccrualOut) => (a.flags.length ? a.flags.join(", ") : "—"),
                },
              ]}
            />
            {manufacturerId && (accruals.data?.data?.length ?? 0) > 0 && (
              <div className="mt-2 flex items-center gap-2 text-xs">
                <Button
                  variant="secondary"
                  onClick={() => {
                    const accRows = accruals.data?.data ?? [];
                    const allSelected = accRows.every((a) => selectedAccrualIds.has(a.id));
                    if (allSelected) {
                      setSelectedAccrualIds(new Set());
                    } else {
                      setSelectedAccrualIds(new Set(accRows.map((a) => a.id)));
                    }
                  }}
                >
                  {(accruals.data?.data ?? []).every((a) => selectedAccrualIds.has(a.id))
                    ? "Deselect all accruals"
                    : "Select all accruals"}
                </Button>
                {selectedAccrualIds.size > 0 && (
                  <Button variant="secondary" onClick={() => setSelectedAccrualIds(new Set())}>
                    Clear selection ({selectedAccrualIds.size})
                  </Button>
                )}
              </div>
            )}
            <PagerControls pager={accrualPager} page={accruals.data?.page} />
          </Card>
        )}

        {createBatchMode && (
          <ConfirmDialog
            open={Boolean(createBatchMode)}
            onOpenChange={(open) => !open && setCreateBatchMode(null)}
            title={
              createBatchMode === "selected"
                ? `Create claim batch (${selectedIds.size} lines)`
                : createBatchMode === "filter"
                  ? "Create claim batch from filter"
                  : `Create claim batch from accruals (${selectedAccrualIds.size} entries)`
            }
            description={
              createBatchMode === "selected"
                ? `Create a new claim batch for the selected ${selectedIds.size} lines${selectedAdjIds.size > 0 ? ` and ${selectedAdjIds.size} adjustments` : ""}? The server will compute the batch totals and line details.`
                : createBatchMode === "filter"
                  ? `Create a new claim batch with all lines matching the current manufacturer and date filters${selectedAdjIds.size > 0 ? ` and ${selectedAdjIds.size} adjustments` : ""}? The server will compute the batch totals and line details.`
                  : `Create a new claim batch with the selected ${selectedAccrualIds.size} rebate accrual entries${selectedAdjIds.size > 0 ? ` and ${selectedAdjIds.size} adjustments` : ""}? The server will compute the batch totals and line details.`
            }
            confirmLabel="Create batch"
            busy={createBatch.isPending}
            onConfirm={() => createBatch.mutate(createBatchMode)}
          />
        )}
      </div>
    </>
  );
}
