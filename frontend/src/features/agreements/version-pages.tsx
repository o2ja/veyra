import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { Alert, Card, Empty, ErrorAlert, Loading, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { DataTable, PagerControls, usePager, utc } from "../masterdata/common";
import { AgreementsNav, useManufacturerMap } from "./common";
import { flattenRulesEntries, formatValue, VERSION_STATUS_LABELS } from "./logic";

export function VersionDetailPage() {
  const { versionId = "" } = useParams();
  const pager = usePager();
  const manufacturers = useManufacturerMap();

  const version = useQuery({
    queryKey: ["agreement-versions", versionId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/agreement-versions/{version_id}", { params: { path: { version_id: versionId } } }),
      ),
  });

  const items = useQuery({
    queryKey: ["agreement-versions", versionId, "items", pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/agreement-versions/{version_id}/items", {
          params: { path: { version_id: versionId }, query: { limit: 100, cursor: pager.cursor } },
        }),
      ),
    enabled: Boolean(version.data),
  });

  const v = version.data;
  const err = version.error as ApiError | null;

  if (err?.status === 403 || err?.code === "forbidden") {
    return (
      <>
        <AgreementsNav />
        <PermissionDenied />
      </>
    );
  }

  return (
    <>
      <AgreementsNav />
      <div className="flex flex-col gap-6">
        {err?.status === 404 || err?.code === "not_found" ? (
          <Card title="Version not found">
            <Alert>This agreement version could not be found.</Alert>
          </Card>
        ) : (
          <>
            <Card title={v ? `${v.external_number} · v${v.version_number}` : "Agreement version"}>
              <ErrorAlert error={err} />
              {version.isPending ? (
                <Loading />
              ) : (
                v && (
                  <div className="flex flex-col gap-4">
                    <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
                      <div>
                        <dt className="text-muted">Name</dt>
                        <dd className="font-medium">{v.name}</dd>
                      </div>
                      <div>
                        <dt className="text-muted">Type</dt>
                        <dd className="font-medium">{v.agreement_type.toUpperCase()}</dd>
                      </div>
                      <div>
                        <dt className="text-muted">Status</dt>
                        <dd className="font-medium">{VERSION_STATUS_LABELS[v.status] ?? v.status}</dd>
                      </div>
                      <div>
                        <dt className="text-muted">Manufacturer</dt>
                        <dd className="font-medium">
                          {(() => {
                            const m = manufacturers.get(v.manufacturer_id);
                            return m ? `${m.name} (${m.code})` : v.manufacturer_id;
                          })()}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-muted">Valid from</dt>
                        <dd className="font-medium">{v.valid_from}</dd>
                      </div>
                      <div>
                        <dt className="text-muted">Valid to</dt>
                        <dd className="font-medium">{v.valid_to}</dd>
                      </div>
                      <div>
                        <dt className="text-muted">Priority</dt>
                        <dd className="font-medium">{v.priority}</dd>
                      </div>
                      <div>
                        <dt className="text-muted">Items</dt>
                        <dd className="font-medium">{v.item_count}</dd>
                      </div>
                      <div>
                        <dt className="text-muted">Activated</dt>
                        <dd className="font-medium">{utc(v.activated_at)}</dd>
                      </div>
                      <div>
                        <dt className="text-muted">Content hash</dt>
                        <dd className="truncate font-mono text-xs">{v.content_hash}</dd>
                      </div>
                    </dl>

                    <div className="flex flex-wrap gap-4 text-sm">
                      <Link to={`/agreements/${v.agreement_id}`} className="text-brand underline">
                        Agreement overview
                      </Link>
                      {v.supersedes_version_id && (
                        <>
                          <Link
                            to={`/agreements/versions/${v.supersedes_version_id}`}
                            className="text-brand underline"
                          >
                            Supersedes version
                          </Link>
                          <Link
                            to={`/agreements/versions/${v.supersedes_version_id}/diff/${v.id}`}
                            className="text-brand underline"
                          >
                            Diff with prior version
                          </Link>
                        </>
                      )}
                      {v.superseded_by_version_id && (
                        <>
                          <Link
                            to={`/agreements/versions/${v.superseded_by_version_id}`}
                            className="text-brand underline"
                          >
                            Superseded by version
                          </Link>
                          <Link
                            to={`/agreements/versions/${v.id}/diff/${v.superseded_by_version_id}`}
                            className="text-brand underline"
                          >
                            Diff with successor version
                          </Link>
                        </>
                      )}
                      {v.derived_from_version_id && (
                        <Link
                          to={`/agreements/versions/${v.derived_from_version_id}`}
                          className="text-brand underline"
                        >
                          Derived from version
                        </Link>
                      )}
                    </div>
                  </div>
                )
              )}
            </Card>

            {v && (
              <Card title="Rules">
                <dl className="grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
                  {flattenRulesEntries(v.rules).map((entry) => (
                    <div key={entry.key} className="rounded-lg border border-line px-3 py-2">
                      <dt className="text-xs text-muted">{entry.key}</dt>
                      <dd className="font-medium">{entry.value}</dd>
                    </div>
                  ))}
                </dl>
              </Card>
            )}

            {v && (
              <Card title="Customers">
                {v.customers.length === 0 ? (
                  <Empty>No customer overrides listed.</Empty>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-sm">
                      <caption className="sr-only">Customers</caption>
                      <thead className="text-muted">
                        <tr>
                          <th scope="col" className="py-2 pr-4 font-medium">
                            Customer code
                          </th>
                          <th scope="col" className="py-2 pr-4 font-medium">
                            Scope
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {v.customers.map((c) => (
                          <tr key={`${c.customer_id}-${c.scope}`} className="border-t border-line">
                            <td className="py-2 pr-4">{c.customer_code ?? c.customer_id}</td>
                            <td className="py-2 pr-4">{c.scope}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            )}

            {v && v.tiers.length > 0 && (
              <Card title="Tiers">
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <caption className="sr-only">Tiers</caption>
                    <thead className="text-muted">
                      <tr>
                        <th scope="col" className="py-2 pr-4 font-medium">
                          Tier
                        </th>
                        <th scope="col" className="py-2 pr-4 font-medium">
                          Lower bound
                        </th>
                        <th scope="col" className="py-2 pr-4 font-medium">
                          Rate
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {v.tiers.map((t) => (
                        <tr key={t.tier_number} className="border-t border-line">
                          <td className="py-2 pr-4">{t.tier_number}</td>
                          <td className="py-2 pr-4">{t.lower_bound}</td>
                          <td className="py-2 pr-4">{t.rate ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}

            {v && (
              <Card title={`Items (${v.item_count})`}>
                <DataTable
                  caption="Version items"
                  loading={items.isPending}
                  error={items.error}
                  rows={items.data?.data}
                  empty="No items on this version."
                  columns={[
                    { header: "Line", cell: (it) => it.line_number },
                    { header: "SKU", cell: (it) => it.sku },
                    { header: "Reference cost", cell: (it) => it.reference_cost ?? "—" },
                    { header: "Deviated cost", cell: (it) => it.deviated_cost ?? "—" },
                    { header: "Sell price", cell: (it) => it.sell_price ?? "—" },
                    { header: "Rate", cell: (it) => it.rate ?? "—" },
                    { header: "Flat amount", cell: (it) => it.flat_amount ?? "—" },
                    { header: "Max qty", cell: (it) => it.max_quantity ?? "—" },
                    { header: "Claim UoM", cell: (it) => it.claim_uom ?? "—" },
                  ]}
                />
                <PagerControls pager={pager} page={items.data?.page} />
              </Card>
            )}
          </>
        )}
      </div>
    </>
  );
}

export function VersionDiffPage() {
  const { versionId = "", otherVersionId = "" } = useParams();
  const diff = useQuery({
    queryKey: ["agreement-versions", versionId, "diff", otherVersionId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/agreement-versions/{version_id}/diff/{other_version_id}", {
          params: { path: { version_id: versionId, other_version_id: otherVersionId } },
        }),
      ),
  });

  return (
    <>
      <AgreementsNav />
      <div className="flex flex-col gap-6">
        <Card title="Version comparison">
          <div className="mb-4 flex flex-wrap gap-4 text-sm">
            <Link to={`/agreements/versions/${versionId}`} className="text-brand underline">
              From version
            </Link>
            <Link to={`/agreements/versions/${otherVersionId}`} className="text-brand underline">
              To version
            </Link>
          </div>
          <ErrorAlert error={diff.error as ApiError | null} />
          {diff.isPending ? (
            <Loading />
          ) : diff.data && diff.data.changes.length === 0 ? (
            <Empty>No differences</Empty>
          ) : (
            diff.data && (
              <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Version differences">
                <table className="w-full text-left text-sm">
                  <caption className="sr-only">Version differences</caption>
                  <thead className="text-muted">
                    <tr>
                      <th scope="col" className="py-2 pr-4 font-medium">
                        Path
                      </th>
                      <th scope="col" className="py-2 pr-4 font-medium">
                        Before
                      </th>
                      <th scope="col" className="py-2 pr-4 font-medium">
                        After
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {diff.data.changes.map((c, idx) => (
                      <tr key={`${c.path}-${idx}`} className="border-t border-line align-top">
                        <td className="py-2 pr-4 font-mono text-xs">{c.path}</td>
                        <td className="py-2 pr-4 font-mono text-xs">{formatValue(c.before)}</td>
                        <td className="py-2 pr-4 font-mono text-xs">{formatValue(c.after)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          )}
        </Card>
      </div>
    </>
  );
}
