import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { Button, Card, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { DataTable, ManufacturerSelect, PagerControls, SelectField, usePager } from "../masterdata/common";
import { ClaimsNav, ConfirmDialog, ValidationDetailsAlert } from "./common";
import {
  ADJUSTMENT_STATUS_LABELS,
  ADJUSTMENT_STATUSES,
  ADJUSTMENT_TYPE_LABELS,
  ADJUSTMENT_TYPES,
  type AdjustmentOut,
  type AdjustmentStatus,
  type AdjustmentType,
} from "./logic";
import { formatDateTime, formatMoney } from "../../lib/format";

const STATUS_TABS: { id: AdjustmentStatus; label: string }[] = [
  { id: "proposed", label: "Proposed" },
  { id: "approved", label: "Approved" },
  { id: "included", label: "Included" },
  { id: "rejected", label: "Rejected" },
];

export function ClaimAdjustmentsPage() {
  const { can, me } = useSession();
  const client = useQueryClient();
  const pager = usePager();
  const [searchParams, setSearchParams] = useSearchParams();

  const statusParam = searchParams.get("status") ?? "proposed";
  const statusFilter: AdjustmentStatus = (ADJUSTMENT_STATUSES as readonly string[]).includes(statusParam)
    ? (statusParam as AdjustmentStatus)
    : "proposed";

  const manufacturerId = searchParams.get("manufacturer_id") ?? "";
  const typeParam = searchParams.get("adjustment_type") ?? "";
  const typeFilter = (ADJUSTMENT_TYPES as readonly string[]).includes(typeParam)
    ? (typeParam as AdjustmentType)
    : "";

  const [cannotNetClaimMap, setCannotNetClaimMap] = useState<Record<string, string>>({});
  const [approvingAdjustment, setApprovingAdjustment] = useState<AdjustmentOut | null>(null);
  const [rejectingAdjustment, setRejectingAdjustment] = useState<AdjustmentOut | null>(null);

  const list = useQuery({
    queryKey: ["claim-adjustments", statusFilter, manufacturerId, typeFilter, pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/claim-adjustments", {
          params: {
            query: {
              status: statusFilter,
              manufacturer_id: manufacturerId || null,
              adjustment_type: typeFilter || null,
              limit: 50,
              cursor: pager.cursor,
            },
          },
        }),
      ),
  });

  const approveMutation = useMutation({
    mutationFn: async (id: string) =>
      unwrap(
        api.POST("/api/v1/claim-adjustments/{adjustment_id}/approve", {
          params: { path: { adjustment_id: id } },
        }),
      ),
    onSuccess: () => {
      setApprovingAdjustment(null);
      void client.invalidateQueries({ queryKey: ["claim-adjustments"] });
    },
    onError: (err: ApiError) => {
      if (approvingAdjustment && err.code === "cannot_net_claim") {
        const msg = err.details.find((d) => d.code === "cannot_net_claim")?.message ?? err.message;
        setCannotNetClaimMap((prev) => ({ ...prev, [approvingAdjustment.id]: msg }));
        setApprovingAdjustment(null);
        approveMutation.reset();
      }
    },
  });

  const rejectMutation = useMutation({
    mutationFn: async (id: string) =>
      unwrap(
        api.POST("/api/v1/claim-adjustments/{adjustment_id}/reject", {
          params: { path: { adjustment_id: id } },
        }),
      ),
    onSuccess: () => {
      setRejectingAdjustment(null);
      void client.invalidateQueries({ queryKey: ["claim-adjustments"] });
    },
  });

  const listErr = list.error as ApiError | null;

  if (listErr?.status === 403 || listErr?.code === "forbidden") {
    return (
      <>
        <ClaimsNav />
        <PermissionDenied />
      </>
    );
  }

  function setTab(status: AdjustmentStatus) {
    const next = new URLSearchParams(searchParams);
    next.set("status", status);
    setSearchParams(next, { replace: true });
    pager.reset();
  }

  // WAI-ARIA tabs: arrows/Home/End move focus and select.
  function onTabKeyDown(e: React.KeyboardEvent) {
    const i = STATUS_TABS.findIndex((t) => t.id === statusFilter);
    const last = STATUS_TABS.length - 1;
    const target =
      e.key === "ArrowRight"
        ? (i + 1) % STATUS_TABS.length
        : e.key === "ArrowLeft"
          ? (i + last) % STATUS_TABS.length
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? last
              : -1;
    const tab = STATUS_TABS[target];
    if (!tab) return;
    e.preventDefault();
    setTab(tab.id);
    document.getElementById(`tab-${tab.id}`)?.focus();
  }

  function setManufacturer(mfg: string) {
    const next = new URLSearchParams(searchParams);
    if (mfg) next.set("manufacturer_id", mfg);
    else next.delete("manufacturer_id");
    setSearchParams(next, { replace: true });
    pager.reset();
  }

  function setType(type: string) {
    const next = new URLSearchParams(searchParams);
    if (type) next.set("adjustment_type", type);
    else next.delete("adjustment_type");
    setSearchParams(next, { replace: true });
    pager.reset();
  }

  const emptyText =
    manufacturerId || typeFilter
      ? "No matching adjustments."
      : statusFilter === "proposed"
        ? "No proposed adjustments."
        : statusFilter === "approved"
          ? "No approved adjustments."
          : statusFilter === "included"
            ? "No included adjustments."
            : "No rejected adjustments.";

  const canApprove = can("adjustment.approve");

  return (
    <>
      <ClaimsNav />

      <div className="flex flex-col gap-6">
        <Card title="Claim adjustments">
          <div className="flex flex-col gap-4">
            {/* Status tabs */}
            <div
              role="tablist"
              aria-label="Adjustment status"
              className="flex overflow-x-auto border-b border-line"
              onKeyDown={onTabKeyDown}
            >
              {STATUS_TABS.map((tab) => {
                const isActive = statusFilter === tab.id;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    role="tab"
                    id={`tab-${tab.id}`}
                    aria-controls={`panel-${tab.id}`}
                    aria-selected={isActive}
                    tabIndex={isActive ? 0 : -1}
                    className={`border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
                      isActive
                        ? "border-brand text-brand"
                        : "border-transparent text-muted hover:border-line hover:text-ink"
                    }`}
                    onClick={() => setTab(tab.id)}
                  >
                    {tab.label}
                  </button>
                );
              })}
            </div>

            {/* Filters */}
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <ManufacturerSelect
                allowAny="All manufacturers"
                value={manufacturerId}
                onChange={setManufacturer}
              />
              <SelectField
                label="Adjustment type"
                value={typeFilter}
                onChange={(e) => setType(e.target.value)}
              >
                <option value="">All types</option>
                {ADJUSTMENT_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {ADJUSTMENT_TYPE_LABELS[t] ?? t}
                  </option>
                ))}
              </SelectField>
            </div>
          </div>
        </Card>

        <Card title={`${ADJUSTMENT_STATUS_LABELS[statusFilter]} adjustments`}>
          <div id={`panel-${statusFilter}`} role="tabpanel" aria-labelledby={`tab-${statusFilter}`}>
            <DataTable
              caption="Claim adjustments"
              loading={list.isPending}
              error={listErr}
              rows={list.data?.data}
              empty={emptyText}
              columns={[
                {
                  header: "Claim reference",
                  cell: (a: AdjustmentOut) => (
                    <div>
                      {a.included_in_batch_id ? (
                        <Link
                          to={`/claims/batches/${a.included_in_batch_id}`}
                          className="font-mono text-xs font-medium text-brand underline"
                        >
                          {a.claim_reference ?? a.claim_line_id ?? "View batch line"}
                        </Link>
                      ) : (
                        <span className="font-mono text-xs font-medium">{a.claim_reference ?? "—"}</span>
                      )}
                      {cannotNetClaimMap[a.id] && (
                        <div
                          role="alert"
                          className="mt-1 flex items-center gap-1.5 rounded border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
                        >
                          <span aria-hidden="true">⚠️</span>
                          <span>{cannotNetClaimMap[a.id]}</span>
                        </div>
                      )}
                    </div>
                  ),
                },
                {
                  header: "Type",
                  cell: (a: AdjustmentOut) => ADJUSTMENT_TYPE_LABELS[a.adjustment_type] ?? a.adjustment_type,
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
                {
                  header: "Run",
                  cell: (a: AdjustmentOut) =>
                    a.run_id ? (
                      <Link
                        to={`/calculations/${a.run_id}`}
                        className="font-mono text-xs text-brand underline"
                      >
                        {a.run_id.slice(0, 8)}
                      </Link>
                    ) : (
                      "—"
                    ),
                },
                {
                  header: "Proposed by / at",
                  cell: (a: AdjustmentOut) =>
                    `${a.proposed_by ?? "—"}${a.created_at ? ` (${formatDateTime(a.created_at)})` : ""}`,
                },
                {
                  header: "Status",
                  cell: (a: AdjustmentOut) => (
                    <span className="inline-block rounded border border-line px-2 py-0.5 text-xs font-medium capitalize">
                      {ADJUSTMENT_STATUS_LABELS[a.status] ?? a.status}
                    </span>
                  ),
                },
                {
                  header: "Included batch",
                  cell: (a: AdjustmentOut) =>
                    a.included_in_batch_id ? (
                      <Link
                        to={`/claims/batches/${a.included_in_batch_id}`}
                        className="font-mono text-xs text-brand underline"
                      >
                        View batch
                      </Link>
                    ) : (
                      "—"
                    ),
                },
                ...(canApprove
                  ? [
                      {
                        header: "Actions",
                        cell: (a: AdjustmentOut) => {
                          const isOwnProposal = Boolean(me?.user?.id && a.proposed_by === me.user.id);
                          return (
                            <div className="flex flex-wrap items-center gap-2">
                              {a.status === "proposed" && (
                                <div className="flex flex-col items-start gap-0.5">
                                  <Button
                                    disabled={isOwnProposal}
                                    title={
                                      isOwnProposal
                                        ? "Four-eyes rule: you proposed this adjustment, so a second approver must approve it."
                                        : undefined
                                    }
                                    aria-label={`Approve adjustment ${a.claim_reference ?? a.id}`}
                                    onClick={() => setApprovingAdjustment(a)}
                                  >
                                    Approve
                                  </Button>
                                  {isOwnProposal && (
                                    <span className="text-[10px] text-muted">
                                      Proposer cannot approve (four-eyes)
                                    </span>
                                  )}
                                </div>
                              )}
                              {(a.status === "proposed" || a.status === "approved") && (
                                <Button
                                  variant="danger"
                                  aria-label={`Reject adjustment ${a.claim_reference ?? a.id}`}
                                  onClick={() => setRejectingAdjustment(a)}
                                >
                                  Reject
                                </Button>
                              )}
                            </div>
                          );
                        },
                      },
                    ]
                  : []),
              ]}
            />
            <PagerControls pager={pager} page={list.data?.page} />
          </div>
        </Card>

        {/* Approve Confirm Dialog */}
        {approvingAdjustment && (
          <ConfirmDialog
            open={Boolean(approvingAdjustment)}
            onOpenChange={(open) => {
              if (!open) {
                setApprovingAdjustment(null);
                approveMutation.reset();
              }
            }}
            title={`Approve adjustment ${approvingAdjustment.claim_reference ?? approvingAdjustment.id}`}
            description={`Are you sure you want to approve this adjustment (${formatMoney(approvingAdjustment.amount)})?`}
            confirmLabel="Confirm approval"
            busy={approveMutation.isPending}
            onConfirm={() => approveMutation.mutate(approvingAdjustment.id)}
          >
            {approveMutation.error && (
              <div className="mt-2">
                <ValidationDetailsAlert error={approveMutation.error as ApiError} />
              </div>
            )}
          </ConfirmDialog>
        )}

        {/* Reject Confirm Dialog */}
        {rejectingAdjustment && (
          <ConfirmDialog
            open={Boolean(rejectingAdjustment)}
            onOpenChange={(open) => {
              if (!open) {
                setRejectingAdjustment(null);
                rejectMutation.reset();
              }
            }}
            title={`Reject adjustment ${rejectingAdjustment.claim_reference ?? rejectingAdjustment.id}`}
            description="Are you sure you want to reject this adjustment?"
            confirmLabel="Reject adjustment"
            confirmVariant="danger"
            busy={rejectMutation.isPending}
            onConfirm={() => rejectMutation.mutate(rejectingAdjustment.id)}
          >
            {rejectMutation.error && (
              <div className="mt-2">
                <ValidationDetailsAlert error={rejectMutation.error as ApiError} />
              </div>
            )}
          </ConfirmDialog>
        )}
      </div>
    </>
  );
}
