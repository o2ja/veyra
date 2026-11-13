import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { Alert, Button, Card, ErrorAlert, Field, Loading, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { DataTable, ManufacturerSelect, PagerControls, usePager } from "../masterdata/common";
import { ClaimsNav, ConfirmDialog, FileDownloadButton, ValidationDetailsAlert } from "./common";
import { formatDecimal, type ExportAccepted, type MissedList, type MissedOut } from "./logic";

export function MissedClaimsPage() {
  const { can } = useSession();
  const navigate = useNavigate();
  const pager = usePager();
  const [searchParams, setSearchParams] = useSearchParams();

  const manufacturerId = searchParams.get("manufacturer_id") ?? "";
  const agreementId = searchParams.get("agreement_id") ?? "";
  const invoiceFrom = searchParams.get("invoice_from") ?? "";
  const invoiceTo = searchParams.get("invoice_to") ?? "";

  const [mfgInput, setMfgInput] = useState(manufacturerId);
  const [agreeInput, setAgreeInput] = useState(agreementId);
  const [fromInput, setFromInput] = useState(invoiceFrom);
  const [toInput, setToInput] = useState(invoiceTo);

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [createBatchMode, setCreateBatchMode] = useState<"selected" | "filter" | null>(null);
  const [batchIdemKey] = useState<string>(() => crypto.randomUUID());
  const [exportAccepted, setExportAccepted] = useState<ExportAccepted | null>(null);

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

  const jobQuery = useQuery({
    queryKey: ["jobs", exportAccepted?.job.id],
    enabled: Boolean(exportAccepted?.job.id),
    queryFn: () => {
      const jid = exportAccepted?.job.id;
      if (!jid) throw new Error("Missing job id");
      return unwrap(
        api.GET("/api/v1/jobs/{job_id}", {
          params: { path: { job_id: jid } },
        }),
      );
    },
    refetchInterval: (query) => {
      const st = query.state.data?.status;
      return st === "running" || st === "queued" || st === "pending" ? 2000 : false;
    },
  });

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
    },
  });

  const createBatch = useMutation({
    mutationFn: async (mode: "selected" | "filter") => {
      if (!manufacturerId) {
        throw new Error("A manufacturer must be selected to create a claim batch.");
      }
      const body =
        mode === "selected"
          ? {
              manufacturer_id: manufacturerId,
              result_ids: Array.from(selectedIds),
            }
          : {
              manufacturer_id: manufacturerId,
              filter: {
                agreement_id: agreementId || null,
                invoice_from: invoiceFrom || null,
                invoice_to: invoiceTo || null,
              },
            };

      return unwrap(
        api.POST("/api/v1/claim-batches", {
          params: { header: { "idempotency-key": batchIdemKey } },
          body,
        }),
      );
    },
    onSuccess: (batch) => {
      setCreateBatchMode(null);
      setSelectedIds(new Set());
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
    pager.reset();
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
  const jobStatus = jobQuery.data?.status;
  const isJobDone = jobStatus === "completed" || jobStatus === "finished";

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
              <span className="font-medium">Export job:</span> {jobStatus ? `Status: ${jobStatus}` : "Queued"}
              {isJobDone && (
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
                  <div className="rounded border border-line p-3">
                    <dt className="text-muted">Total claimable lines</dt>
                    <dd className="font-mono text-lg font-semibold">{summary.data.totals.lines}</dd>
                  </div>
                  <div className="rounded border border-line p-3">
                    <dt className="text-muted">Total claimable amount</dt>
                    <dd className="font-mono text-lg font-semibold text-ok">
                      {formatDecimal(summary.data.totals.amount)}
                    </dd>
                  </div>
                  <div className="rounded border border-line p-3">
                    <dt className="text-muted">Lost to expiry (lines)</dt>
                    <dd className="font-mono text-lg font-semibold text-danger">
                      {summary.data.lost_to_expiry.lines}
                    </dd>
                  </div>
                  <div className="rounded border border-line p-3">
                    <dt className="text-muted">Lost to expiry (amount)</dt>
                    <dd className="font-mono text-lg font-semibold text-danger">
                      {formatDecimal(summary.data.lost_to_expiry.amount)}
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
                                {formatDecimal(g.amount)}
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
                                {formatDecimal(ex.stored_amount)}
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
              { header: "Invoice date", cell: (r) => r.invoice_date },
              {
                header: "Eligible qty",
                cell: (r) => (
                  <span className="block text-right font-mono">{formatDecimal(r.eligible_quantity)}</span>
                ),
              },
              { header: "UoM", cell: (r) => r.claim_uom ?? "—" },
              {
                header: "Unit amount",
                cell: (r) => (
                  <span className="block text-right font-mono">{formatDecimal(r.claim_unit_amount)}</span>
                ),
              },
              {
                header: "Claim amount",
                cell: (r) => (
                  <span className="block text-right font-mono font-medium">
                    {formatDecimal(r.claim_amount)}
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

        {createBatchMode && (
          <ConfirmDialog
            open={Boolean(createBatchMode)}
            onOpenChange={(open) => !open && setCreateBatchMode(null)}
            title={
              createBatchMode === "selected"
                ? `Create claim batch (${selectedIds.size} lines)`
                : "Create claim batch from filter"
            }
            description={
              createBatchMode === "selected"
                ? `Create a new claim batch for the selected ${selectedIds.size} lines? The server will compute the batch totals and line details.`
                : "Create a new claim batch with all lines matching the current manufacturer and date filters? The server will compute the batch totals and line details."
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
