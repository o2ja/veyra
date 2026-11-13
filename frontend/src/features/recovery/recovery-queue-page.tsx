import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { Alert, Button, Card, Empty, ErrorAlert, Loading, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { type ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { ManufacturerSelect, PagerControls, SelectField, usePager } from "../masterdata/common";
import { useManufacturerMap } from "../claims/common";
import { RecoveryNav } from "./common";
import {
  CASE_STATUSES,
  CASE_STATUS_LABELS,
  DEADLINE_BASIS_LABELS,
  amountText,
  caseTypeLabel,
  ownerText,
  reasonLabel,
  statusLabel,
  usDate,
  type CaseOut,
  type CaseStatus,
  type SummaryOut,
} from "./logic";

export function RecoveryQueuePage() {
  const pager = usePager();
  const { me } = useSession();
  const manufacturers = useManufacturerMap();
  const [searchParams, setSearchParams] = useSearchParams();

  const statusParam = searchParams.get("status") ?? "";
  const statusFilter = CASE_STATUSES.includes(statusParam as CaseStatus) ? (statusParam as CaseStatus) : "";
  const manufacturerId = searchParams.get("manufacturer_id") ?? "";
  const reasonFilter = searchParams.get("reason_code") ?? "";
  const ownerFilter = searchParams.get("owner") ?? "";
  const deadlineBefore = searchParams.get("deadline_before") ?? "";

  const summary = useQuery({
    queryKey: ["recovery", "summary"],
    queryFn: () => unwrap(api.GET("/api/v1/cases/summary")),
  });

  const list = useQuery({
    queryKey: [
      "recovery",
      "cases",
      statusFilter,
      manufacturerId,
      reasonFilter,
      ownerFilter,
      deadlineBefore,
      pager.cursor,
    ],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/cases", {
          params: {
            query: {
              status: statusFilter ? [statusFilter] : null,
              manufacturer_id: manufacturerId || null,
              reason_code: reasonFilter || null,
              owner_user_id: ownerFilter === "mine" ? (me?.user.id ?? null) : null,
              deadline_before: deadlineBefore || null,
              limit: 50,
              cursor: pager.cursor,
            },
          },
        }),
      ),
  });

  const listErr = list.error as ApiError | null;
  const summaryErr = summary.error as ApiError | null;
  const summaryData = summary.data as SummaryOut | undefined;
  const hasFilters = Boolean(statusFilter || manufacturerId || reasonFilter || ownerFilter || deadlineBefore);

  if (listErr?.status === 403 || listErr?.code === "forbidden") {
    return (
      <>
        <RecoveryNav />
        <PermissionDenied />
      </>
    );
  }

  function setParam(name: string, value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(name, value);
    else next.delete(name);
    setSearchParams(next, { replace: true });
    pager.reset();
  }

  function clearFilters() {
    setSearchParams(new URLSearchParams(), { replace: true });
    pager.reset();
  }

  return (
    <>
      <RecoveryNav />
      <div className="flex flex-col gap-6">
        <Card title="Recovery summary">
          <ErrorAlert error={summaryErr} />
          {summary.isPending ? (
            <Loading label="Loading summary…" />
          ) : summaryData ? (
            <div className="flex flex-col gap-5">
              <div className="grid gap-5 lg:grid-cols-2">
                <SummaryTable
                  caption="Cases by status"
                  keyHeader="Status"
                  rows={summaryData.by_status.map((row) => ({
                    ...row,
                    key: statusLabel(row.key),
                  }))}
                />
                <SummaryTable
                  caption="Cases by reason"
                  keyHeader="Reason"
                  rows={summaryData.by_reason.map((row) => ({
                    ...row,
                    key: reasonLabel(row.key),
                  }))}
                />
              </div>
              <Alert tone="info">
                At risk within {summaryData.at_risk.deadline_within_days} days: {summaryData.at_risk.count}{" "}
                cases, {amountText(summaryData.at_risk.amount)}
              </Alert>
            </div>
          ) : null}
        </Card>

        <Card title="Recovery queue">
          <div className="mb-4 flex flex-wrap items-end gap-4">
            <div className="w-48">
              <SelectField
                label="Status"
                value={statusFilter}
                onChange={(event) => setParam("status", event.target.value)}
              >
                <option value="">All statuses</option>
                {CASE_STATUSES.map((status) => (
                  <option key={status} value={status}>
                    {CASE_STATUS_LABELS[status]}
                  </option>
                ))}
              </SelectField>
            </div>
            <div className="w-64">
              <ManufacturerSelect
                allowAny="All manufacturers"
                value={manufacturerId}
                onChange={(value) => setParam("manufacturer_id", value)}
              />
            </div>
            <div className="w-52">
              <SelectField
                label="Reason"
                value={reasonFilter}
                onChange={(event) => setParam("reason_code", event.target.value)}
              >
                <option value="">All reasons</option>
                {(summaryData?.by_reason ?? []).map((row) => (
                  <option key={row.key} value={row.key}>
                    {reasonLabel(row.key)}
                  </option>
                ))}
              </SelectField>
            </div>
            <div className="w-44">
              <SelectField
                label="Owner"
                value={ownerFilter}
                onChange={(event) => setParam("owner", event.target.value)}
              >
                <option value="">Any owner</option>
                <option value="mine">Mine</option>
              </SelectField>
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="deadline-before" className="text-sm font-medium">
                Deadline before
              </label>
              <input
                id="deadline-before"
                type="date"
                className="rounded-md border border-line bg-panel px-3 py-2 text-sm"
                value={deadlineBefore}
                onChange={(event) => setParam("deadline_before", event.target.value)}
              />
            </div>
            {hasFilters && (
              <Button variant="secondary" onClick={clearFilters}>
                Clear filters
              </Button>
            )}
          </div>

          <ErrorAlert error={listErr} />
          {list.isPending ? (
            <Loading label="Loading cases…" />
          ) : !list.data?.data.length ? (
            <Empty>{hasFilters ? "No cases match these filters." : "No recovery cases yet."}</Empty>
          ) : (
            <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Recovery cases">
              <table className="w-full text-left text-sm">
                <caption className="sr-only">Recovery cases</caption>
                <thead className="text-muted">
                  <tr>
                    <th scope="col" className="py-2 pr-4 font-medium">
                      Deadline
                    </th>
                    <th scope="col" className="py-2 pr-4 font-medium">
                      Basis
                    </th>
                    <th scope="col" className="py-2 pr-4 font-medium">
                      Type
                    </th>
                    <th scope="col" className="py-2 pr-4 font-medium">
                      Reason
                    </th>
                    <th scope="col" className="py-2 pr-4 font-medium">
                      Manufacturer
                    </th>
                    <th scope="col" className="py-2 pr-4 font-medium">
                      Variance
                    </th>
                    <th scope="col" className="py-2 pr-4 font-medium">
                      Recovered
                    </th>
                    <th scope="col" className="py-2 pr-4 font-medium">
                      Status
                    </th>
                    <th scope="col" className="py-2 pr-4 font-medium">
                      Owner
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {list.data.data.map((item: CaseOut) => {
                    const manufacturer = manufacturers.get(item.manufacturer_id);
                    return (
                      <tr key={item.id} className="relative border-t border-line align-top">
                        <td className="py-2 pr-4">
                          <Link
                            to={`/recovery/${item.id}`}
                            aria-label={`Open recovery case ${item.id}`}
                            className="font-medium text-brand underline after:absolute after:inset-0 after:content-['']"
                          >
                            {item.deadline_unknown ? (
                              <span className="rounded border border-line px-1.5 py-0.5 text-xs font-medium">
                                Unknown deadline
                              </span>
                            ) : (
                              usDate(item.deadline_date)
                            )}
                          </Link>
                        </td>
                        <td className="py-2 pr-4">
                          {item.deadline_basis ? DEADLINE_BASIS_LABELS[item.deadline_basis] : "—"}
                        </td>
                        <td className="py-2 pr-4">{caseTypeLabel(item.case_type)}</td>
                        <td className="py-2 pr-4">
                          {reasonLabel(item.reason_code)}
                          {item.external_reason_code && (
                            <span className="block text-xs text-muted">{item.external_reason_code}</span>
                          )}
                        </td>
                        <td className="py-2 pr-4">
                          {manufacturer
                            ? `${manufacturer.name} (${manufacturer.code})`
                            : item.manufacturer_id}
                        </td>
                        <td className="py-2 pr-4 font-mono">{amountText(item.variance_amount)}</td>
                        <td className="py-2 pr-4 font-mono">{amountText(item.recovered_amount)}</td>
                        <td className="py-2 pr-4">{statusLabel(item.status)}</td>
                        <td className="py-2 pr-4">{ownerText(item.owner_user_id, me?.user.id)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <PagerControls pager={pager} page={list.data?.page} />
        </Card>
      </div>
    </>
  );
}

function SummaryTable({
  caption,
  keyHeader,
  rows,
}: {
  caption: string;
  keyHeader: string;
  rows: {
    key: string;
    count: number;
    variance_amount: string;
    recovered_amount: string;
    written_off_amount: string;
  }[];
}) {
  if (rows.length === 0) return <Empty>No {caption.toLowerCase()} yet.</Empty>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <caption className="mb-1 text-left font-medium">{caption}</caption>
        <thead className="text-muted">
          <tr>
            <th scope="col" className="py-1 pr-3 font-medium">
              {keyHeader}
            </th>
            <th scope="col" className="py-1 pr-3 font-medium">
              Count
            </th>
            <th scope="col" className="py-1 pr-3 font-medium">
              Variance
            </th>
            <th scope="col" className="py-1 pr-3 font-medium">
              Recovered
            </th>
            <th scope="col" className="py-1 pr-3 font-medium">
              Written off
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} className="border-t border-line">
              <td className="py-1 pr-3">{row.key}</td>
              <td className="py-1 pr-3 font-mono">{row.count}</td>
              <td className="py-1 pr-3 font-mono">{amountText(row.variance_amount)}</td>
              <td className="py-1 pr-3 font-mono">{amountText(row.recovered_amount)}</td>
              <td className="py-1 pr-3 font-mono">{amountText(row.written_off_amount)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
