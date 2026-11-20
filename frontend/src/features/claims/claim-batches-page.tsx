import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { Card, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { DataTable, ManufacturerSelect, PagerControls, SelectField, usePager } from "../masterdata/common";
import { ClaimsNav, useManufacturerMap } from "./common";
import { BATCH_STATUSES, BATCH_STATUS_LABELS, type BatchOut } from "./logic";
import { formatDateTime, formatMoney } from "../../lib/format";

export function ClaimBatchesPage() {
  const pager = usePager();
  const manufacturers = useManufacturerMap();
  const [searchParams, setSearchParams] = useSearchParams();

  const statusParam = searchParams.get("status") ?? "";
  const statusFilter = BATCH_STATUSES.includes(statusParam as (typeof BATCH_STATUSES)[number])
    ? (statusParam as (typeof BATCH_STATUSES)[number])
    : "";
  const manufacturerId = searchParams.get("manufacturer_id") ?? "";

  const list = useQuery({
    queryKey: ["claim-batches", statusFilter, manufacturerId, pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/claim-batches", {
          params: {
            query: {
              status: statusFilter || null,
              manufacturer_id: manufacturerId || null,
              limit: 50,
              cursor: pager.cursor,
            },
          },
        }),
      ),
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

  function setStatus(st: string) {
    const next = new URLSearchParams(searchParams);
    if (st) next.set("status", st);
    else next.delete("status");
    setSearchParams(next, { replace: true });
    pager.reset();
  }

  function setManufacturer(mId: string) {
    const next = new URLSearchParams(searchParams);
    if (mId) next.set("manufacturer_id", mId);
    else next.delete("manufacturer_id");
    setSearchParams(next, { replace: true });
    pager.reset();
  }

  return (
    <>
      <ClaimsNav />
      <div className="flex flex-col gap-6">
        <Card title="Claim batches">
          <div className="mb-4 flex flex-wrap gap-4">
            <div className="w-48">
              <SelectField label="Status" value={statusFilter} onChange={(e) => setStatus(e.target.value)}>
                <option value="">All statuses</option>
                {BATCH_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {BATCH_STATUS_LABELS[s] ?? s}
                  </option>
                ))}
              </SelectField>
            </div>
            <div className="w-64">
              <ManufacturerSelect
                allowAny="All manufacturers"
                value={manufacturerId}
                onChange={setManufacturer}
              />
            </div>
          </div>

          <DataTable
            caption="Claim batches"
            loading={list.isPending}
            error={listErr}
            rows={list.data?.data}
            empty={statusFilter || manufacturerId ? "No matching claim batches." : "No claim batches yet."}
            columns={[
              {
                header: "Batch number",
                cell: (b: BatchOut) => (
                  <Link to={`/claims/batches/${b.id}`} className="font-medium text-brand underline">
                    {b.batch_number}
                  </Link>
                ),
              },
              {
                header: "Status",
                cell: (b) => (
                  <span
                    className={
                      b.status === "void"
                        ? "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium bg-danger-soft text-danger"
                        : b.status === "submitted"
                          ? "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium bg-ok-soft text-ok"
                          : "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium bg-brand-soft text-brand"
                    }
                  >
                    {BATCH_STATUS_LABELS[b.status] ?? b.status}
                  </span>
                ),
              },
              {
                header: "Manufacturer",
                cell: (b) => {
                  const m = manufacturers.get(b.manufacturer_id);
                  return m ? `${m.name} (${m.code})` : b.manufacturer_id;
                },
              },
              {
                header: "Period",
                cell: (b) => (b.period_from && b.period_to ? `${b.period_from} – ${b.period_to}` : "—"),
              },
              {
                header: "Lines",
                cell: (b) => <span className="block text-right font-mono">{b.line_count}</span>,
              },
              {
                header: "Total amount",
                cell: (b) => (
                  <span className="block text-right font-mono font-medium">
                    {formatMoney(b.total_amount)}
                  </span>
                ),
              },
              {
                header: "Activity",
                cell: (b) => {
                  if (b.submitted_at)
                    return <span className="text-xs">Submitted {formatDateTime(b.submitted_at)}</span>;
                  if (b.approved_at)
                    return <span className="text-xs">Approved {formatDateTime(b.approved_at)}</span>;
                  if (b.voided_at)
                    return <span className="text-xs text-danger">Voided {formatDateTime(b.voided_at)}</span>;
                  return <span className="text-xs text-muted">Created {formatDateTime(b.created_at)}</span>;
                },
              },
            ]}
          />
          <PagerControls pager={pager} page={list.data?.page} />
        </Card>
      </div>
    </>
  );
}
