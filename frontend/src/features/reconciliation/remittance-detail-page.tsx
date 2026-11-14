import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import {
  Alert,
  Button,
  Card,
  Empty,
  ErrorAlert,
  Field,
  Loading,
  PermissionDenied,
} from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { DataTable, PagerControls, usePager, utc } from "../masterdata/common";
import {
  ConfirmDialog,
  EvidenceTree,
  ReconciliationNav,
  ValidationDetailsAlert,
  useManufacturerMap,
} from "./common";
import {
  CASE_TYPE_LABELS,
  MATCH_METHOD_LABELS,
  RECONCILIATION_STATE_LABELS,
  REMITTANCE_CHANNEL_LABELS,
  REMITTANCE_DOC_TYPE_LABELS,
  REMITTANCE_STATUS_LABELS,
  formatDecimal,
  type FindingOut,
  type LineCorrection,
  type ManualMatchIn,
  type MatchOut,
  type ReconciliationLineOut,
  type RemittanceAcceptIn,
} from "./logic";

export function RemittanceDetailPage() {
  const { remittanceId = "" } = useParams();
  const { can } = useSession();
  const client = useQueryClient();
  const pager = usePager();
  const batchLinesPager = usePager();
  const manufacturers = useManufacturerMap();

  const [stateFilter, setStateFilter] = useState<"all" | "matched" | "suggested" | "unmatched">("all");
  const [staleBanner, setStaleBanner] = useState(false);
  const [finalizeFindings, setFinalizeFindings] = useState<FindingOut[] | null>(null);

  // Pending review state
  const [docNumber, setDocNumber] = useState("");
  const [docDate, setDocDate] = useState("");
  const [paymentDate, setPaymentDate] = useState("");
  const [totalAmount, setTotalAmount] = useState("");
  const [corrections, setCorrections] = useState<Record<number, LineCorrection>>({});
  const [acceptOpen, setAcceptOpen] = useState(false);
  const [voidOpen, setVoidOpen] = useState(false);

  // Workbench state
  const [reconcileJobId, setReconcileJobId] = useState<string | null>(null);
  const [finalizeOpen, setFinalizeOpen] = useState(false);
  const [finalizeIdemKey] = useState(() => crypto.randomUUID());

  // Manual match dialog state
  const [manualMatchTargetLine, setManualMatchTargetLine] = useState<string | null>(null);
  const [selectedBatchId, setSelectedBatchId] = useState("");
  const [selectedClaimLineId, setSelectedClaimLineId] = useState("");
  const [selectedAdjustmentId, setSelectedAdjustmentId] = useState("");
  const [manualAllocatedAmount, setManualAllocatedAmount] = useState("");

  const canReconcile = can("remittance.reconcile");

  // Main remittance query
  const remittanceQuery = useQuery({
    queryKey: ["remittances", remittanceId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/remittances/{remittance_id}", {
          params: { path: { remittance_id: remittanceId } },
        }),
      ),
  });

  const r = remittanceQuery.data;

  // Staging / Extraction query (for pending_review)
  const extractionQuery = useQuery({
    queryKey: ["remittances", remittanceId, "extraction"],
    enabled: Boolean(r && r.status === "pending_review"),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/remittances/{remittance_id}/extraction", {
          params: { path: { remittance_id: remittanceId } },
        }),
      ),
  });

  // Reconciliation data query (for accepted / reconciled)
  const reconciliationQuery = useQuery({
    queryKey: ["remittances", remittanceId, "reconciliation", pager.cursor],
    enabled: Boolean(r && (r.status === "accepted" || r.status === "reconciled")),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/remittances/{remittance_id}/reconciliation", {
          params: {
            path: { remittance_id: remittanceId },
            query: { limit: 50, cursor: pager.cursor },
          },
        }),
      ),
  });

  // Submitted claim batches query (for manual match modal)
  const batchesQuery = useQuery({
    queryKey: ["claim-batches", "submitted", r?.manufacturer_id],
    enabled: Boolean(manualMatchTargetLine && r?.manufacturer_id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/claim-batches", {
          params: {
            query: {
              manufacturer_id: r?.manufacturer_id ?? null,
              status: "submitted",
              limit: 50,
            },
          },
        }),
      ),
  });

  // Lines of selected batch
  const batchLinesQuery = useQuery({
    queryKey: ["claim-batches", selectedBatchId, "lines", batchLinesPager.cursor],
    enabled: Boolean(selectedBatchId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/claim-batches/{batch_id}/lines", {
          params: {
            path: { batch_id: selectedBatchId },
            query: { limit: 100, cursor: batchLinesPager.cursor },
          },
        }),
      ),
  });

  // Adjustments carried in selected batch
  const batchAdjustmentsQuery = useQuery({
    queryKey: ["claim-adjustments", "batch", selectedBatchId],
    enabled: Boolean(selectedBatchId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/claim-adjustments", {
          params: {
            query: {
              included_in_batch_id: selectedBatchId,
              limit: 100,
            },
          },
        }),
      ),
  });

  // Reconcile job polling
  const jobQuery = useQuery({
    queryKey: ["jobs", reconcileJobId],
    enabled: Boolean(reconcileJobId),
    queryFn: () => {
      if (!reconcileJobId) throw new Error("Missing job id");
      return unwrap(
        api.GET("/api/v1/jobs/{job_id}", {
          params: { path: { job_id: reconcileJobId } },
        }),
      );
    },
    refetchInterval: (query) => {
      const st = query.state.data?.status;
      if (st === "succeeded" || st === "failed") {
        return false;
      }
      return 1500;
    },
  });

  const isJobFailed = Boolean(reconcileJobId && jobQuery.data?.status === "failed");
  const isMatching = Boolean(
    reconcileJobId &&
    (jobQuery.data?.status === "queued" ||
      jobQuery.data?.status === "running" ||
      (!jobQuery.data && !jobQuery.error)),
  );

  useEffect(() => {
    if (jobQuery.data?.status === "succeeded") {
      void client.invalidateQueries({ queryKey: ["remittances", remittanceId] });
      void client.invalidateQueries({ queryKey: ["remittances", remittanceId, "reconciliation"] });
    }
  }, [jobQuery.data?.status, client, remittanceId]);

  // Mutations
  const acceptMutation = useMutation({
    mutationFn: async () => {
      const body: RemittanceAcceptIn = {
        corrections: Object.values(corrections),
        ...(docNumber.trim() ? { document_number: docNumber.trim() } : {}),
        ...(docDate.trim() ? { document_date: docDate.trim() } : {}),
        ...(paymentDate.trim() ? { payment_date: paymentDate.trim() } : {}),
        ...(totalAmount.trim() ? { total_amount: totalAmount.trim() } : {}),
      };
      return unwrap(
        api.POST("/api/v1/remittances/{remittance_id}/accept", {
          params: { path: { remittance_id: remittanceId } },
          body,
        }),
      );
    },
    onSuccess: () => {
      setAcceptOpen(false);
      void client.invalidateQueries({ queryKey: ["remittances", remittanceId] });
    },
  });

  const voidMutation = useMutation({
    mutationFn: async () =>
      unwrap(
        api.POST("/api/v1/remittances/{remittance_id}/void", {
          params: { path: { remittance_id: remittanceId } },
        }),
      ),
    onSuccess: () => {
      setVoidOpen(false);
      void client.invalidateQueries({ queryKey: ["remittances", remittanceId] });
    },
  });

  const runReconcileMutation = useMutation({
    mutationFn: async () => {
      const res = (await unwrap(
        api.POST("/api/v1/remittances/{remittance_id}/reconcile", {
          params: { path: { remittance_id: remittanceId } },
        }),
      )) as { job?: { id: string } };
      return res;
    },
    onSuccess: (data) => {
      if (data.job?.id) {
        setReconcileJobId(data.job.id);
      } else {
        void client.invalidateQueries({ queryKey: ["remittances", remittanceId, "reconciliation"] });
      }
    },
  });

  const confirmMatchMutation = useMutation({
    mutationFn: async ({ matchId, revision }: { matchId: string; revision: number }) => {
      return unwrap(
        api.POST("/api/v1/matches/{match_id}/confirm", {
          params: {
            path: { match_id: matchId },
            header: { "if-match": `"${revision}"` },
          },
        }),
      );
    },
    onSuccess: () => {
      setStaleBanner(false);
      void client.invalidateQueries({ queryKey: ["remittances", remittanceId, "reconciliation"] });
    },
    onError: (err: ApiError) => {
      if (err.status === 412 || (err.status === 409 && err.code === "invalid_state")) {
        setStaleBanner(true);
        void client.invalidateQueries({ queryKey: ["remittances", remittanceId, "reconciliation"] });
      }
    },
  });

  const rejectMatchMutation = useMutation({
    mutationFn: async ({ matchId, revision }: { matchId: string; revision: number }) => {
      return unwrap(
        api.POST("/api/v1/matches/{match_id}/reject", {
          params: {
            path: { match_id: matchId },
            header: { "if-match": `"${revision}"` },
          },
        }),
      );
    },
    onSuccess: () => {
      setStaleBanner(false);
      void client.invalidateQueries({ queryKey: ["remittances", remittanceId, "reconciliation"] });
    },
    onError: (err: ApiError) => {
      if (err.status === 412 || (err.status === 409 && err.code === "invalid_state")) {
        setStaleBanner(true);
        void client.invalidateQueries({ queryKey: ["remittances", remittanceId, "reconciliation"] });
      }
    },
  });

  const manualMatchMutation = useMutation({
    mutationFn: async (body: ManualMatchIn) => {
      return unwrap(
        api.POST("/api/v1/matches", {
          body,
        }),
      );
    },
    onSuccess: () => {
      setManualMatchTargetLine(null);
      setSelectedBatchId("");
      setSelectedClaimLineId("");
      setSelectedAdjustmentId("");
      setManualAllocatedAmount("");
      void client.invalidateQueries({ queryKey: ["remittances", remittanceId, "reconciliation"] });
    },
  });

  const categoriseLineMutation = useMutation({
    mutationFn: async (lineId: string) => {
      return unwrap(
        api.POST("/api/v1/remittance-lines/{line_id}/categorise", {
          params: { path: { line_id: lineId } },
          body: { category: "unmatched_payment" },
        }),
      );
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["remittances", remittanceId, "reconciliation"] });
    },
  });

  const finalizeMutation = useMutation({
    mutationFn: async () => {
      return unwrap(
        api.POST("/api/v1/remittances/{remittance_id}/finalize", {
          params: {
            path: { remittance_id: remittanceId },
            header: { "idempotency-key": finalizeIdemKey },
          },
        }),
      );
    },
    onSuccess: (data) => {
      setFinalizeOpen(false);
      setFinalizeFindings(data.findings ?? []);
      void client.invalidateQueries({ queryKey: ["remittances", remittanceId] });
      void client.invalidateQueries({ queryKey: ["remittances", remittanceId, "reconciliation"] });
    },
  });

  const claimRefMap = useMemo(() => {
    const map = new Map<string, string>();
    for (const row of reconciliationQuery.data?.data ?? []) {
      if (row.line.claim_reference) {
        map.set(row.line.id, row.line.claim_reference);
      }
      for (const m of row.matches ?? []) {
        for (const al of m.allocations ?? []) {
          if (al.claim_line_id && al.claim_reference) {
            map.set(al.claim_line_id, al.claim_reference);
          }
        }
      }
    }
    return map;
  }, [reconciliationQuery.data?.data]);

  const remittanceErr = remittanceQuery.error as ApiError | null;
  if (remittanceErr?.status === 403 || remittanceErr?.code === "forbidden") {
    return (
      <>
        <ReconciliationNav />
        <PermissionDenied />
      </>
    );
  }

  if (remittanceQuery.isPending) {
    return (
      <>
        <ReconciliationNav />
        <Loading />
      </>
    );
  }

  if (remittanceErr) {
    return (
      <>
        <ReconciliationNav />
        <ErrorAlert error={remittanceErr} />
      </>
    );
  }

  if (!r) {
    return (
      <>
        <ReconciliationNav />
        <Empty>Remittance not found.</Empty>
      </>
    );
  }

  const m = manufacturers.get(r.manufacturer_id);
  const linesData = reconciliationQuery.data?.data ?? [];
  const filteredLines = linesData.filter((l) => {
    if (stateFilter === "all") return true;
    return l.state === stateFilter;
  });

  const totalRequiredProblem = extractionQuery.data?.problems?.find((p) => p.code === "total_required");

  return (
    <>
      <ReconciliationNav />

      <div className="flex flex-col gap-6">
        {staleBanner && (
          <Alert tone="info">
            Stale revision detected: this match was updated in another session. The view has been refreshed.
          </Alert>
        )}

        {/* Header card */}
        <Card title={`Remittance: ${r.document_number || "Untitled remittance"}`}>
          <dl className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <dt className="text-muted">Status</dt>
              <dd className="font-semibold capitalize">{REMITTANCE_STATUS_LABELS[r.status] ?? r.status}</dd>
            </div>
            <div>
              <dt className="text-muted">Manufacturer</dt>
              <dd className="font-medium">{m ? `${m.name} (${m.code})` : r.manufacturer_id}</dd>
            </div>
            <div>
              <dt className="text-muted">Total amount</dt>
              <dd className="font-mono text-lg font-semibold text-ok">{formatDecimal(r.total_amount)}</dd>
            </div>
            <div>
              <dt className="text-muted">Channel / Doc type</dt>
              <dd>
                {REMITTANCE_CHANNEL_LABELS[r.channel] ?? r.channel} ·{" "}
                {REMITTANCE_DOC_TYPE_LABELS[r.doc_type] ?? r.doc_type}
              </dd>
            </div>
            <div>
              <dt className="text-muted">Document date</dt>
              <dd>{r.document_date ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-muted">Payment date</dt>
              <dd>{r.payment_date ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-muted">Created at</dt>
              <dd>{utc(r.created_at)}</dd>
            </div>
            <div>
              <dt className="text-muted">Accepted at</dt>
              <dd>{r.accepted_at ? utc(r.accepted_at) : "—"}</dd>
            </div>
          </dl>
        </Card>

        {/* 1. Pending review view */}
        {r.status === "pending_review" && (
          <div className="flex flex-col gap-6">
            {!canReconcile ? (
              <Alert tone="info">Review is limited to reconcilers</Alert>
            ) : extractionQuery.isPending ? (
              <Loading />
            ) : extractionQuery.error ? (
              <ErrorAlert error={extractionQuery.error as ApiError} />
            ) : (
              <Card title="Review staged remittance extraction">
                {/* Document level problems */}
                {extractionQuery.data?.problems && extractionQuery.data.problems.length > 0 && (
                  <div className="mb-4 flex flex-col gap-2">
                    <h2 className="text-sm font-semibold text-danger">Document problems to resolve:</h2>
                    <ul className="list-disc pl-5 text-xs text-danger">
                      {extractionQuery.data.problems.map((p, idx) => (
                        <li key={`${p.code}-${idx}`}>
                          <span className="font-semibold">{p.code}:</span> {p.message}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Document corrections form */}
                <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  <Field
                    label="Document number"
                    defaultValue={r.document_number ?? ""}
                    onChange={(e) => setDocNumber(e.target.value)}
                  />
                  <Field
                    label="Document date"
                    type="date"
                    defaultValue={r.document_date ?? ""}
                    onChange={(e) => setDocDate(e.target.value)}
                  />
                  <Field
                    label="Payment date"
                    type="date"
                    defaultValue={r.payment_date ?? ""}
                    onChange={(e) => setPaymentDate(e.target.value)}
                  />
                  <div>
                    <Field
                      label="Document total"
                      required={Boolean(totalRequiredProblem)}
                      placeholder="e.g. 1500.00"
                      defaultValue={r.total_amount ?? ""}
                      onChange={(e) => setTotalAmount(e.target.value)}
                    />
                    {totalRequiredProblem && (
                      <p className="mt-1 text-xs text-danger">{totalRequiredProblem.message}</p>
                    )}
                  </div>
                </div>

                {/* Staged lines table */}
                <div className="mb-4 overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <caption className="sr-only">Staged remittance lines</caption>
                    <thead className="border-b border-line text-muted">
                      <tr>
                        <th scope="col" className="py-2 pr-4 font-medium">
                          Line #
                        </th>
                        <th scope="col" className="py-2 pr-4 font-medium">
                          Claim ref
                        </th>
                        <th scope="col" className="py-2 pr-4 font-medium">
                          Invoice # / Line
                        </th>
                        <th scope="col" className="py-2 pr-4 text-right font-medium">
                          Amount paid
                        </th>
                        <th scope="col" className="py-2 pr-4 text-right font-medium">
                          Claimed stated
                        </th>
                        <th scope="col" className="py-2 pr-4 font-medium">
                          Reason code
                        </th>
                        <th scope="col" className="py-2 pr-4 font-medium">
                          Issues
                        </th>
                        <th scope="col" className="py-2 pr-4 font-medium">
                          Correction
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {(extractionQuery.data?.lines ?? []).map((line) => {
                        const correction = corrections[line.line_number];
                        return (
                          <tr key={line.line_number} className="border-b border-line">
                            <td className="py-2 pr-4 font-mono">{line.line_number}</td>
                            <td className="py-2 pr-4 font-mono">{line.claim_reference ?? "—"}</td>
                            <td className="py-2 pr-4">
                              {line.invoice_number
                                ? `${line.invoice_number} #${line.invoice_line_number ?? 1}`
                                : "—"}
                            </td>
                            <td className="py-2 pr-4 text-right font-mono font-medium">
                              {formatDecimal(line.amount_paid)}
                            </td>
                            <td className="py-2 pr-4 text-right font-mono">
                              {formatDecimal(line.amount_claimed_stated)}
                            </td>
                            <td className="py-2 pr-4 font-mono text-xs">
                              {line.external_reason_code ?? "—"}
                            </td>
                            <td className="py-2 pr-4">
                              {line.issues.length > 0 ? (
                                <ul className="list-disc pl-4 text-xs text-danger">
                                  {line.issues.map((iss, i) => (
                                    <li key={i}>{iss.message}</li>
                                  ))}
                                </ul>
                              ) : (
                                <span className="text-xs text-ok">Clean</span>
                              )}
                            </td>
                            <td className="py-2 pr-4">
                              <input
                                type="text"
                                placeholder="Corrected paid"
                                className="w-24 rounded border border-line bg-surface px-1.5 py-0.5 text-xs font-mono"
                                aria-label={`Corrected paid for line ${line.line_number}`}
                                value={
                                  correction?.amount_paid !== undefined ? String(correction.amount_paid) : ""
                                }
                                onChange={(e) => {
                                  const val = e.target.value;
                                  setCorrections((prev) => ({
                                    ...prev,
                                    [line.line_number]: {
                                      line_number: line.line_number,
                                      ...(val ? { amount_paid: val } : {}),
                                    },
                                  }));
                                }}
                              />
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                <div className="flex gap-2">
                  <Button onClick={() => setAcceptOpen(true)}>Accept remittance</Button>
                  <Button variant="danger" onClick={() => setVoidOpen(true)}>
                    Void remittance
                  </Button>
                </div>
              </Card>
            )}

            {/* Accept Confirm Dialog */}
            <ConfirmDialog
              open={acceptOpen}
              onOpenChange={setAcceptOpen}
              title="Accept remittance"
              description="Are you sure you want to accept this remittance? Once accepted, lines become eligible for automated and manual matching."
              confirmLabel="Accept"
              busy={acceptMutation.isPending}
              onConfirm={() => acceptMutation.mutate()}
            >
              {acceptMutation.error && <ValidationDetailsAlert error={acceptMutation.error as ApiError} />}
            </ConfirmDialog>

            {/* Void Confirm Dialog */}
            <ConfirmDialog
              open={voidOpen}
              onOpenChange={setVoidOpen}
              title="Void remittance"
              description="Are you sure you want to void this remittance? Staged lines will be discarded."
              confirmLabel="Void"
              confirmVariant="danger"
              busy={voidMutation.isPending}
              onConfirm={() => voidMutation.mutate()}
            >
              {voidMutation.error && <ValidationDetailsAlert error={voidMutation.error as ApiError} />}
            </ConfirmDialog>
          </div>
        )}

        {/* 2. Accepted view (Workbench) */}
        {r.status === "accepted" && (
          <div className="flex flex-col gap-6">
            {isJobFailed && (
              <div role="alert" className="rounded-lg border border-line bg-surface/50 p-4 text-sm">
                <Alert tone="danger">
                  Matching failed:{" "}
                  {(jobQuery.data as { error?: string | null })?.error ?? "Matching job failed"}
                </Alert>
              </div>
            )}

            {isMatching && (
              <div className="rounded-lg border border-line bg-surface/50 p-4 text-sm">
                <div className="flex items-center gap-2">
                  <Loading />
                  <span className="font-medium">Matching remittance lines…</span>
                  <span className="text-muted">(Job status: {jobQuery.data?.status ?? "pending"})</span>
                </div>
              </div>
            )}

            <Card
              title="Reconciliation workbench"
              actions={
                canReconcile ? (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="secondary"
                      disabled={runReconcileMutation.isPending || isMatching}
                      onClick={() => runReconcileMutation.mutate()}
                    >
                      {runReconcileMutation.isPending ? "Starting matching…" : "Run matching"}
                    </Button>
                    <Button onClick={() => setFinalizeOpen(true)}>Finalise</Button>
                  </div>
                ) : undefined
              }
            >
              {runReconcileMutation.error && (
                <div className="mb-4">
                  <ValidationDetailsAlert error={runReconcileMutation.error as ApiError} />
                </div>
              )}

              {/* Filter chips */}
              <div className="mb-4 flex flex-wrap gap-2 text-xs">
                {(["all", "matched", "suggested", "unmatched"] as const).map((st) => (
                  <button
                    key={st}
                    type="button"
                    aria-pressed={stateFilter === st}
                    className={`rounded-full px-3 py-1 font-medium transition-colors ${
                      stateFilter === st
                        ? "bg-brand text-white"
                        : "border border-line bg-surface text-muted hover:text-ink"
                    }`}
                    onClick={() => setStateFilter(st)}
                  >
                    {st === "all" ? "All lines" : RECONCILIATION_STATE_LABELS[st]}
                  </button>
                ))}
              </div>

              {reconciliationQuery.isPending ? (
                <Loading />
              ) : reconciliationQuery.error ? (
                <ErrorAlert error={reconciliationQuery.error as ApiError} />
              ) : filteredLines.length === 0 ? (
                <Empty>No remittance lines matching this filter.</Empty>
              ) : (
                <div className="flex flex-col gap-4">
                  {filteredLines.map((row) => (
                    <div
                      key={row.line.id}
                      role="region"
                      aria-label={`Remittance line ${row.line.line_number}`}
                      className="flex flex-col gap-3 rounded-xl border border-line bg-panel shadow-card p-4"
                    >
                      {/* Remittance line info */}
                      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-2 text-sm">
                        <div className="flex items-center gap-3">
                          <span className="font-mono font-bold">Line #{row.line.line_number}</span>
                          <span className="rounded-lg border border-line px-2 py-0.5 text-xs font-semibold uppercase">
                            {RECONCILIATION_STATE_LABELS[row.state]}
                          </span>
                          {row.line.claim_reference && (
                            <span className="font-mono text-xs text-muted">
                              Ref: {row.line.claim_reference}
                            </span>
                          )}
                          {row.line.category && (
                            <span className="rounded bg-ok/10 px-2 py-0.5 text-xs font-medium text-ok">
                              Categorised: {row.line.category}
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-4 text-xs font-mono">
                          <span>
                            Paid: <strong className="text-ink">{formatDecimal(row.line.amount_paid)}</strong>
                          </span>
                          <span>Claimed: {formatDecimal(row.line.amount_claimed_stated)}</span>
                        </div>
                      </div>

                      {/* Line detail meta */}
                      <div className="grid gap-2 text-xs sm:grid-cols-3 text-muted">
                        <div>
                          Invoice:{" "}
                          {row.line.invoice_number
                            ? `${row.line.invoice_number} #${row.line.invoice_line_number ?? 1}`
                            : "—"}
                        </div>
                        <div>Customer: {row.line.customer_ref ?? "—"}</div>
                        <div>
                          Reason:{" "}
                          {row.line.external_reason_code
                            ? `${row.line.external_reason_code} ${row.line.external_reason_text ?? ""}`
                            : "—"}
                        </div>
                      </div>

                      {/* Matches / Suggestions */}
                      {row.matches.length > 0 && (
                        <div className="mt-2 flex flex-col gap-3">
                          <h2 className="text-xs font-semibold text-muted uppercase">
                            Suggestions & Matches
                          </h2>
                          {row.matches.map((m: MatchOut) => (
                            <div
                              key={m.id}
                              className="rounded-lg border border-line bg-surface/40 p-3 text-xs"
                            >
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <div className="flex items-center gap-2">
                                  <span className="font-semibold text-brand">
                                    {MATCH_METHOD_LABELS[m.method] ?? m.method}
                                  </span>
                                  <span className="rounded-lg border border-line px-1.5 py-0.5 text-[10px] font-medium capitalize">
                                    {m.status}
                                  </span>
                                  {m.score && <span className="font-mono text-muted">Score: {m.score}</span>}
                                </div>
                                {m.status === "suggested" && canReconcile && (
                                  <div className="flex gap-2">
                                    <Button
                                      disabled={confirmMatchMutation.isPending}
                                      onClick={() =>
                                        confirmMatchMutation.mutate({ matchId: m.id, revision: m.revision })
                                      }
                                    >
                                      Confirm
                                    </Button>
                                    <Button
                                      variant="danger"
                                      disabled={rejectMatchMutation.isPending}
                                      onClick={() =>
                                        rejectMatchMutation.mutate({ matchId: m.id, revision: m.revision })
                                      }
                                    >
                                      Reject
                                    </Button>
                                  </div>
                                )}
                              </div>

                              {confirmMatchMutation.error &&
                                confirmMatchMutation.variables?.matchId === m.id && (
                                  <div className="mt-2">
                                    <ValidationDetailsAlert error={confirmMatchMutation.error as ApiError} />
                                  </div>
                                )}

                              {rejectMatchMutation.error &&
                                rejectMatchMutation.variables?.matchId === m.id && (
                                  <div className="mt-2">
                                    <ValidationDetailsAlert error={rejectMatchMutation.error as ApiError} />
                                  </div>
                                )}

                              {/* Allocations */}
                              {m.allocations.length > 0 && (
                                <div className="mt-2 flex flex-col gap-1">
                                  <span className="text-muted">Allocations:</span>
                                  <ul className="list-disc pl-5">
                                    {m.allocations.map((al, idx) => (
                                      <li key={idx} className="font-mono">
                                        {al.claim_reference ?? al.claim_line_id ?? al.claim_adjustment_id}:{" "}
                                        <strong>{formatDecimal(al.allocated_amount)}</strong>
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}

                              {/* Evidence tree */}
                              {m.evidence && Object.keys(m.evidence).length > 0 && (
                                <div className="mt-3">
                                  <EvidenceTree evidence={m.evidence} />
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Row actions (manual match / categorise) */}
                      {canReconcile && !row.line.category && (
                        <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-line pt-2">
                          {(row.state === "unmatched" || row.state === "suggested") && (
                            <Button
                              variant="secondary"
                              onClick={() => {
                                setManualMatchTargetLine(row.line.id);
                                setSelectedBatchId("");
                                setSelectedClaimLineId("");
                                setSelectedAdjustmentId("");
                                setManualAllocatedAmount("");
                                batchLinesPager.reset();
                              }}
                            >
                              Manual match
                            </Button>
                          )}
                          {row.state === "unmatched" && (
                            <Button
                              variant="secondary"
                              disabled={categoriseLineMutation.isPending}
                              onClick={() => categoriseLineMutation.mutate(row.line.id)}
                            >
                              Mark as unmatched payment
                            </Button>
                          )}
                        </div>
                      )}

                      {categoriseLineMutation.error && categoriseLineMutation.variables === row.line.id && (
                        <div className="mt-2">
                          <ValidationDetailsAlert error={categoriseLineMutation.error as ApiError} />
                        </div>
                      )}
                    </div>
                  ))}
                  <PagerControls pager={pager} page={reconciliationQuery.data?.page} />
                </div>
              )}
            </Card>

            {/* Manual match dialog */}
            <ConfirmDialog
              open={Boolean(manualMatchTargetLine)}
              onOpenChange={(open) => !open && setManualMatchTargetLine(null)}
              title="Manual match"
              description="Match this remittance line to a submitted claim line or carried adjustment. (0.00 is only valid when the line paid 0.00, marking the claim line rejected)."
              confirmLabel="Apply match"
              busy={manualMatchMutation.isPending}
              onConfirm={() => {
                if (!manualMatchTargetLine) return;
                const body: ManualMatchIn = {
                  remittance_line_id: manualMatchTargetLine,
                  allocated_amount: manualAllocatedAmount.trim(),
                  ...(selectedClaimLineId ? { claim_line_id: selectedClaimLineId } : {}),
                  ...(selectedAdjustmentId ? { claim_adjustment_id: selectedAdjustmentId } : {}),
                };
                manualMatchMutation.mutate(body);
              }}
            >
              <div className="flex flex-col gap-3 text-sm">
                {manualMatchMutation.error && (
                  <ValidationDetailsAlert error={manualMatchMutation.error as ApiError} />
                )}

                {/* 1. Pick submitted claim batch */}
                <div className="flex flex-col gap-1">
                  <label htmlFor="select-batch" className="font-medium text-xs">
                    Submitted claim batch:
                  </label>
                  <select
                    id="select-batch"
                    value={selectedBatchId}
                    onChange={(e) => {
                      setSelectedBatchId(e.target.value);
                      setSelectedClaimLineId("");
                      setSelectedAdjustmentId("");
                      batchLinesPager.reset();
                    }}
                    className="rounded-lg border border-line bg-surface p-2 text-xs"
                  >
                    <option value="">Select a batch…</option>
                    {(batchesQuery.data?.data ?? []).map((b) => (
                      <option key={b.id} value={b.id}>
                        {b.batch_number} ({b.line_count} lines, {formatDecimal(b.total_amount)})
                      </option>
                    ))}
                  </select>
                </div>

                {/* 2. Pick claim line OR adjustment */}
                {selectedBatchId && (
                  <>
                    <div className="flex flex-col gap-1">
                      <label htmlFor="select-claim-line" className="font-medium text-xs">
                        Claim line:
                      </label>
                      <select
                        id="select-claim-line"
                        value={selectedClaimLineId}
                        onChange={(e) => {
                          setSelectedClaimLineId(e.target.value);
                          if (e.target.value) setSelectedAdjustmentId("");
                        }}
                        className="rounded-lg border border-line bg-surface p-2 text-xs font-mono"
                      >
                        <option value="">None / Or choose adjustment below</option>
                        {(batchLinesQuery.data?.data ?? []).map((l) => (
                          <option key={l.id} value={l.id}>
                            {l.claim_reference} (Line #{l.line_number ?? 1}, Claimed:{" "}
                            {formatDecimal(l.claim_amount)})
                          </option>
                        ))}
                      </select>
                      <PagerControls pager={batchLinesPager} page={batchLinesQuery.data?.page} />
                    </div>

                    {(batchAdjustmentsQuery.data?.data?.length ?? 0) > 0 && (
                      <div className="flex flex-col gap-1">
                        <label htmlFor="select-adjustment" className="font-medium text-xs">
                          Carried adjustment:
                        </label>
                        <select
                          id="select-adjustment"
                          value={selectedAdjustmentId}
                          onChange={(e) => {
                            setSelectedAdjustmentId(e.target.value);
                            if (e.target.value) setSelectedClaimLineId("");
                          }}
                          className="rounded-lg border border-line bg-surface p-2 text-xs font-mono"
                        >
                          <option value="">None</option>
                          {(batchAdjustmentsQuery.data?.data ?? []).map((a) => (
                            <option key={a.id} value={a.id}>
                              {a.claim_reference ?? a.id} ({formatDecimal(a.amount)}, {a.reason})
                            </option>
                          ))}
                        </select>
                      </div>
                    )}
                  </>
                )}

                {/* 3. Allocated amount */}
                <Field
                  label="Allocated amount"
                  placeholder="e.g. 150.00 or 0.00"
                  value={manualAllocatedAmount}
                  onChange={(e) => setManualAllocatedAmount(e.target.value)}
                />
              </div>
            </ConfirmDialog>

            {/* Finalize Confirm Dialog */}
            <ConfirmDialog
              open={finalizeOpen}
              onOpenChange={setFinalizeOpen}
              title="Finalise reconciliation"
              description="Finalising this remittance will freeze all matches, generate immutable findings, and close reconciled items. This action cannot be undone."
              confirmLabel="Confirm finalisation"
              busy={finalizeMutation.isPending}
              onConfirm={() => finalizeMutation.mutate()}
            >
              {finalizeMutation.error && (
                <div className="mt-2">
                  <ValidationDetailsAlert error={finalizeMutation.error as ApiError} />
                </div>
              )}
            </ConfirmDialog>
          </div>
        )}

        {/* 3. Reconciled view (Read-only + Findings) */}
        {r.status === "reconciled" && (
          <div className="flex flex-col gap-6">
            <Alert tone="ok">
              This remittance has been finalised and reconciled. Matches and allocations are immutable.
            </Alert>

            {finalizeFindings && finalizeFindings.length > 0 ? (
              <Card title={`Reconciliation findings (${finalizeFindings.length})`}>
                <DataTable
                  caption="Reconciliation findings"
                  loading={false}
                  error={null}
                  rows={finalizeFindings.map((f, i) => ({
                    ...f,
                    id: `${f.case_type}-${f.claim_line_id ?? i}-${i}`,
                  }))}
                  empty="No findings generated."
                  columns={[
                    {
                      header: "Case type",
                      cell: (f: FindingOut) => (
                        <span className="font-semibold">{CASE_TYPE_LABELS[f.case_type] ?? f.case_type}</span>
                      ),
                    },
                    {
                      header: "Reason code",
                      cell: (f: FindingOut) => f.reason_code ?? "—",
                    },
                    {
                      header: "External reason",
                      cell: (f: FindingOut) => f.external_reason_code ?? "—",
                    },
                    {
                      header: "Claim reference",
                      cell: (f: FindingOut) => {
                        const claimRef =
                          (f as { claim_reference?: string | null }).claim_reference ??
                          (f.claim_line_id ? claimRefMap.get(f.claim_line_id) : null) ??
                          (f.remittance_line_id ? claimRefMap.get(f.remittance_line_id) : null) ??
                          "—";
                        return <span className="font-mono text-xs">{claimRef}</span>;
                      },
                    },
                    {
                      header: "Claimed",
                      cell: (f: FindingOut) => (
                        <span className="block text-right font-mono">
                          {f.claimed ? formatDecimal(f.claimed) : "—"}
                        </span>
                      ),
                    },
                    {
                      header: "Paid",
                      cell: (f: FindingOut) => (
                        <span className="block text-right font-mono font-medium">
                          {formatDecimal(f.paid)}
                        </span>
                      ),
                    },
                    {
                      header: "Variance",
                      cell: (f: FindingOut) => (
                        <span className="block text-right font-mono font-medium">
                          {formatDecimal(f.amount)}
                        </span>
                      ),
                    },
                  ]}
                />
              </Card>
            ) : (
              <p className="text-sm text-muted">
                Findings were recorded during finalisation. Review details are read-only.
              </p>
            )}

            {/* Read-only reconciliation lines table */}
            <Card title="Finalised reconciliation lines">
              <DataTable
                caption="Finalised reconciliation lines"
                loading={reconciliationQuery.isPending}
                error={reconciliationQuery.error}
                rows={(reconciliationQuery.data?.data ?? []).map((row) => ({
                  ...row,
                  id: row.line.id,
                }))}
                empty="No reconciliation lines."
                columns={[
                  {
                    header: "Line #",
                    cell: (row: ReconciliationLineOut) => (
                      <span className="font-mono">#{row.line.line_number}</span>
                    ),
                  },
                  {
                    header: "State",
                    cell: (row: ReconciliationLineOut) => (
                      <span className="font-medium capitalize">{RECONCILIATION_STATE_LABELS[row.state]}</span>
                    ),
                  },
                  {
                    header: "Claim reference",
                    cell: (row: ReconciliationLineOut) => (
                      <span className="font-mono text-xs">{row.line.claim_reference ?? "—"}</span>
                    ),
                  },
                  {
                    header: "Amount paid",
                    cell: (row: ReconciliationLineOut) => (
                      <span className="block text-right font-mono font-medium">
                        {formatDecimal(row.line.amount_paid)}
                      </span>
                    ),
                  },
                  {
                    header: "Claimed stated",
                    cell: (row: ReconciliationLineOut) => (
                      <span className="block text-right font-mono">
                        {formatDecimal(row.line.amount_claimed_stated)}
                      </span>
                    ),
                  },
                ]}
              />
            </Card>
          </div>
        )}
      </div>
    </>
  );
}
