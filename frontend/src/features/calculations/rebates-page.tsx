import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { Card, Empty, ErrorAlert, Loading, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { AgreementSelect, DataTable, PagerControls, SelectField, usePager } from "../masterdata/common";
import { CalculationsNav, ValidationDetailsAlert } from "./common";
import { runSummary, type AccrualOut } from "./logic";
import { formatDate, formatMoney, formatQty } from "../../lib/format";

export function RebatesPage() {
  const pager = usePager();
  const [searchParams, setSearchParams] = useSearchParams();
  const agreementFilter = searchParams.get("agreement_id") ?? "";
  const runFilter = searchParams.get("run_id") ?? "";

  const runs = useQuery({
    queryKey: ["calculation-runs", "picker"],
    queryFn: () =>
      unwrap(api.GET("/api/v1/calculation-runs", { params: { query: { status: "completed", limit: 50 } } })),
  });

  const tracker = useQuery({
    queryKey: ["rebate-tracker"],
    queryFn: () => unwrap(api.GET("/api/v1/rebate-tracker")),
  });

  const accruals = useQuery({
    queryKey: ["rebate-accruals", agreementFilter, runFilter, pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/rebate-accruals", {
          params: {
            query: {
              agreement_id: agreementFilter.trim() || null,
              run_id: runFilter.trim() || null,
              limit: 50,
              cursor: pager.cursor,
            },
          },
        }),
      ),
  });

  const trackerErr = tracker.error as ApiError | null;
  const accrualsErr = accruals.error as ApiError | null;

  if (
    trackerErr?.status === 403 ||
    trackerErr?.code === "forbidden" ||
    accrualsErr?.status === 403 ||
    accrualsErr?.code === "forbidden"
  ) {
    return (
      <>
        <CalculationsNav />
        <PermissionDenied />
      </>
    );
  }

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    setSearchParams(next, { replace: true });
    pager.reset();
  }

  return (
    <>
      <CalculationsNav />
      <div className="flex flex-col gap-6">
        <Card title="Rebate tracker">
          <ErrorAlert error={trackerErr} />
          {tracker.isPending ? (
            <Loading />
          ) : !tracker.data?.data.length ? (
            <Empty>No active rebate accruals yet.</Empty>
          ) : (
            <div className="flex flex-col gap-6">
              {tracker.data.data.map((row) => (
                <section
                  key={`${row.agreement_version_id}-${row.period_start}`}
                  className="rounded-md border border-line p-4"
                  aria-label={`${row.external_number} ${row.agreement_name}`}
                >
                  <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                    <h2 className="text-base font-semibold">
                      <Link to={`/agreements/${row.agreement_id}`} className="text-brand underline">
                        {row.external_number}
                      </Link>{" "}
                      · {row.agreement_name}
                    </h2>
                    <div className="flex flex-wrap gap-3 text-xs text-muted">
                      <span>
                        Period: {formatDate(row.period_start)} – {formatDate(row.period_end)}
                      </span>
                      <span>As of: {formatDate(row.as_of_date)}</span>
                      <Link
                        to={`/agreements/versions/${row.agreement_version_id}`}
                        className="text-brand underline"
                      >
                        Agreement version
                      </Link>
                    </div>
                  </div>

                  <dl className="mb-4 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
                    <div>
                      <dt className="text-muted">Cumulative qualifying</dt>
                      <dd className="font-mono font-medium">{formatQty(row.cumulative_qualifying)}</dd>
                    </div>
                    <div>
                      <dt className="text-muted">Cumulative payout base</dt>
                      <dd className="font-mono font-medium">{formatMoney(row.cumulative_payout_base)}</dd>
                    </div>
                    <div>
                      <dt className="text-muted">Cumulative earned</dt>
                      <dd className="font-mono font-medium">{formatMoney(row.cumulative_earned)}</dd>
                    </div>
                    <div>
                      <dt className="text-muted">Accrued to date</dt>
                      <dd className="font-mono font-medium">{formatMoney(row.accrued_to_date)}</dd>
                    </div>
                    <div>
                      <dt className="text-muted">Current tier</dt>
                      <dd className="font-medium">
                        {row.tier_number !== null
                          ? `Tier ${row.tier_number} (${row.tier_rate ?? "—"})`
                          : "None"}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted">Next tier lower bound</dt>
                      <dd className="font-mono">{formatQty(row.next_tier_lower_bound)}</dd>
                    </div>
                    <div>
                      <dt className="text-muted">Amount to next tier</dt>
                      <dd className="font-mono">{formatQty(row.amount_to_next_tier)}</dd>
                    </div>
                    <div>
                      <dt className="text-muted">Claim deadline</dt>
                      <dd>{formatDate(row.claim_deadline)}</dd>
                    </div>
                  </dl>

                  {row.tiers.length > 0 && (
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-sm">
                        <caption className="sr-only">Tiers for {row.external_number}</caption>
                        <thead className="text-muted">
                          <tr>
                            <th scope="col" className="py-1.5 pr-4 font-medium">
                              Tier
                            </th>
                            <th scope="col" className="py-1.5 pr-4 text-right font-medium">
                              Lower bound
                            </th>
                            <th scope="col" className="py-1.5 pr-4 text-right font-medium">
                              Rate
                            </th>
                            <th scope="col" className="py-1.5 pr-4 font-medium">
                              Status
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {row.tiers.map((tier) => {
                            const isCurrent =
                              row.tier_number !== null && tier.tier_number === row.tier_number;
                            return (
                              <tr
                                key={tier.tier_number}
                                className={`border-t border-line ${isCurrent ? "bg-surface font-medium" : ""}`}
                              >
                                <td className="py-1.5 pr-4">Tier {tier.tier_number}</td>
                                <td className="py-1.5 pr-4 text-right font-mono">
                                  {formatQty(tier.lower_bound)}
                                </td>
                                <td className="py-1.5 pr-4 text-right font-mono">{tier.rate ?? "—"}</td>
                                <td className="py-1.5 pr-4">{isCurrent ? "Current tier" : "—"}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </section>
              ))}
            </div>
          )}
        </Card>

        <Card title="Accrual ledger">
          <div className="mb-4 grid gap-3 sm:grid-cols-2">
            <AgreementSelect value={agreementFilter} onChange={(id) => setFilter("agreement_id", id)} />
            <SelectField
              label="Calculation run"
              value={runFilter}
              onChange={(e) => setFilter("run_id", e.target.value)}
            >
              <option value="">All runs</option>
              {runs.data?.data.map((r) => (
                <option key={r.id} value={r.id}>
                  {formatDate(r.run_date)} · {runSummary(r.totals)}
                </option>
              ))}
            </SelectField>
          </div>

          {accrualsErr ? (
            <ValidationDetailsAlert error={accrualsErr} />
          ) : (
            <DataTable
              caption="Rebate accruals"
              loading={accruals.isPending}
              error={null}
              rows={accruals.data?.data}
              empty="No rebate accrual entries."
              columns={[
                {
                  header: "Agreement",
                  cell: (a: AccrualOut) => (
                    <Link to={`/agreements/${a.agreement_id}`} className="text-brand underline">
                      {a.external_number}
                    </Link>
                  ),
                },
                { header: "As of", cell: (a) => formatDate(a.as_of_date) },
                {
                  header: "Period",
                  cell: (a) => `${formatDate(a.period_start)} – ${formatDate(a.period_end)}`,
                },
                {
                  header: "Prior accrued",
                  cell: (a) => (
                    <span className="block text-right font-mono">
                      {formatMoney(a.prior_cumulative_accrued)}
                    </span>
                  ),
                },
                {
                  header: "Delta",
                  cell: (a) => <span className="block text-right font-mono">{formatMoney(a.delta)}</span>,
                },
                {
                  header: "Cumulative earned",
                  cell: (a) => (
                    <span className="block text-right font-mono">{formatMoney(a.cumulative_earned)}</span>
                  ),
                },
                {
                  header: "Flags",
                  cell: (a) => (a.flags.length ? a.flags.join(", ") : "—"),
                },
                {
                  header: "Run",
                  cell: (a) => (
                    <Link to={`/calculations/${a.run_id}`} className="font-mono text-xs text-brand underline">
                      {a.run_id.slice(0, 8)}
                    </Link>
                  ),
                },
              ]}
            />
          )}
          <PagerControls pager={pager} page={accruals.data?.page} />
        </Card>
      </div>
    </>
  );
}
