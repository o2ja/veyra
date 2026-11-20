import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { Button, Card, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { DataTable, ManufacturerSelect, PagerControls, SelectField, usePager } from "../masterdata/common";
import { ReconciliationNav, useManufacturerMap } from "./common";
import {
  REMITTANCE_CHANNEL_LABELS,
  REMITTANCE_DOC_TYPE_LABELS,
  REMITTANCE_STATUS_LABELS,
  REMITTANCE_STATUSES,
  type RemittanceOut,
  type RemittanceStatus,
} from "./logic";
import { formatDate, formatMoney } from "../../lib/format";

export function RemittancesPage() {
  const { can } = useSession();
  const pager = usePager();
  const manufacturers = useManufacturerMap();
  const [searchParams, setSearchParams] = useSearchParams();

  const statusParam = searchParams.get("status") ?? "";
  const statusFilter: RemittanceStatus | "" = (REMITTANCE_STATUSES as readonly string[]).includes(statusParam)
    ? (statusParam as RemittanceStatus)
    : "";

  const manufacturerId = searchParams.get("manufacturer_id") ?? "";

  const list = useQuery({
    queryKey: ["remittances", statusFilter, manufacturerId, pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/remittances", {
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
        <ReconciliationNav />
        <PermissionDenied />
      </>
    );
  }

  function setStatus(status: string) {
    const next = new URLSearchParams(searchParams);
    if (status) next.set("status", status);
    else next.delete("status");
    setSearchParams(next, { replace: true });
    pager.reset();
  }

  function setManufacturer(mfg: string) {
    const next = new URLSearchParams(searchParams);
    if (mfg) next.set("manufacturer_id", mfg);
    else next.delete("manufacturer_id");
    setSearchParams(next, { replace: true });
    pager.reset();
  }

  return (
    <>
      <ReconciliationNav />

      <div className="flex flex-col gap-6">
        <Card
          title="Remittances"
          actions={
            can("remittance.reconcile") ? (
              <Link to="/reconciliation/new">
                <Button>New remittance</Button>
              </Link>
            ) : undefined
          }
        >
          <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <ManufacturerSelect
              allowAny="All manufacturers"
              value={manufacturerId}
              onChange={setManufacturer}
            />
            <SelectField label="Status" value={statusFilter} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All statuses</option>
              {REMITTANCE_STATUSES.map((st) => (
                <option key={st} value={st}>
                  {REMITTANCE_STATUS_LABELS[st] ?? st}
                </option>
              ))}
            </SelectField>
          </div>

          <DataTable
            caption="Remittances"
            loading={list.isPending}
            error={listErr}
            rows={list.data?.data}
            empty="No remittances found."
            columns={[
              {
                header: "Document number",
                cell: (r: RemittanceOut) => (
                  <Link to={`/reconciliation/${r.id}`} className="font-medium text-brand underline">
                    {r.document_number || "Untitled remittance"}
                  </Link>
                ),
              },
              {
                header: "Manufacturer",
                cell: (r: RemittanceOut) => {
                  const m = manufacturers.get(r.manufacturer_id);
                  return m ? `${m.name} (${m.code})` : r.manufacturer_id;
                },
              },
              {
                header: "Doc type",
                cell: (r: RemittanceOut) => REMITTANCE_DOC_TYPE_LABELS[r.doc_type] ?? r.doc_type,
              },
              {
                header: "Channel",
                cell: (r: RemittanceOut) => REMITTANCE_CHANNEL_LABELS[r.channel] ?? r.channel,
              },
              {
                header: "Document date",
                cell: (r: RemittanceOut) => formatDate(r.document_date),
              },
              {
                header: "Payment date",
                cell: (r: RemittanceOut) => formatDate(r.payment_date),
              },
              {
                header: "Total amount",
                cell: (r: RemittanceOut) => (
                  <span className="block text-right font-mono font-medium">
                    {formatMoney(r.total_amount)}
                  </span>
                ),
              },
              {
                header: "Status",
                cell: (r: RemittanceOut) => (
                  <span className="inline-block rounded border border-line px-2 py-0.5 text-xs font-medium capitalize">
                    {REMITTANCE_STATUS_LABELS[r.status] ?? r.status}
                  </span>
                ),
              },
            ]}
          />
          <PagerControls pager={pager} page={list.data?.page} />
        </Card>
      </div>
    </>
  );
}
